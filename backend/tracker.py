"""
Script position tracker — two-mode design:

  SCAN  mode  : full-script search on every update (startup + after losing the speaker)
  TRACK mode  : small window around current position (once confidently locked)

Pre-builds overlapping chunks at init so RapidFuzz can scan the whole script
in one vectorised call instead of a Python loop.
"""
import os
import re
import asyncio
from rapidfuzz import fuzz, process as fz_process

# Minimum score to accept a position update (0-1)
MIN_CONFIDENCE  = 0.55
# Higher bar required before committing to TRACK mode — prevents locking on wrong place
LOCK_CONFIDENCE = 0.72

# Chunk parameters for the pre-built index
CHUNK_WORDS  = 30   # words per chunk (longer = more context per match)
CHUNK_STEP   = 8    # step between chunk starts (smaller = finer granularity)

# How many consecutive confident / lost cycles before switching modes
LOCK_AFTER   = 3    # cycles at LOCK_CONFIDENCE → switch SCAN→TRACK
LOSE_AFTER   = 4    # cycles below MIN_CONFIDENCE → switch TRACK→SCAN

# Window sizes in TRACK mode (in chunk indices, not word indices)
TRACK_AHEAD  = 15   # ~120 words ahead
TRACK_BEHIND = 4    # ~32 words behind


class ScriptTracker:
    def __init__(self, words: list):
        self.words     = words
        self.position  = 0       # current word index
        self.locked    = False   # True when operator has manually seized control
        self._mode     = 'scan'
        self._confident = 0
        self._lost      = 0

        # Pre-build overlapping text chunks across the whole script
        # Each entry: (start_word_idx, chunk_text)
        self._chunks: list = []
        for i in range(0, max(1, len(words) - CHUNK_WORDS + 1), CHUNK_STEP):
            end  = min(i + CHUNK_WORDS, len(words))
            text = " ".join(_clean(w["word"]) for w in words[i:end])
            self._chunks.append((i, text))

        # Parallel list of just the texts (for fuzz.process.extract)
        self._chunk_texts: list = [t for _, t in self._chunks]

    # ── Public API ────────────────────────────────────────────────────────────

    def update(self, transcript: str):
        """Match transcript against script. Returns (word_index, confidence 0-1)."""
        if self.locked or not transcript or not self.words:
            return self.position, 1.0 if self.locked else 0.0

        query = _clean(transcript)
        if not query:
            return self.position, 0.0

        if self._mode == 'scan':
            return self._full_scan(query)
        return self._window_track(query)

    def seek(self, word_index: int):
        """Operator manually jumped to a position."""
        self.position = max(0, min(word_index, len(self.words) - 1))
        self.locked   = True
        self._mode    = 'track'  # resume tracking from this point when unlocked
        self._confident = LOCK_AFTER

    def resume(self):
        """Resume AI tracking after manual override."""
        self.locked = False

    def reset(self):
        self.position   = 0
        self.locked     = False
        self._mode      = 'scan'
        self._confident = 0
        self._lost      = 0

    # ── Modes ─────────────────────────────────────────────────────────────────

    def _full_scan(self, query: str):
        """Search the entire script — used at startup and after losing the speaker."""
        hits = fz_process.extract(
            query,
            self._chunk_texts,
            scorer=fuzz.WRatio,   # combines partial/token/set ratios — best overall
            limit=5,
        )
        if not hits:
            return self.position, 0.0

        best_text, best_score, best_idx = hits[0]
        confidence = best_score / 100.0
        best_start = self._chunks[best_idx][0]

        if confidence >= LOCK_CONFIDENCE:
            self._advance(best_start, query)
            self._confident += 1
            self._lost       = 0
            if self._confident >= LOCK_AFTER:
                self._mode = 'track'
        elif confidence >= MIN_CONFIDENCE:
            # Decent match but not confident enough to lock — move cautiously
            self._advance(best_start, query)
            self._lost = 0
        else:
            self._lost      += 1
            self._confident  = 0

        return self.position, confidence

    def _window_track(self, query: str):
        """Search a window around the current position — fast, low-latency."""
        # Find the chunk index closest to current position
        cur_chunk = self.position // CHUNK_STEP

        lo = max(0, cur_chunk - TRACK_BEHIND)
        hi = min(len(self._chunks), cur_chunk + TRACK_AHEAD)

        window_texts  = self._chunk_texts[lo:hi]
        window_starts = [self._chunks[i][0] for i in range(lo, hi)]

        if not window_texts:
            self._mode = 'scan'
            return self._full_scan(query)

        hits = fz_process.extract(
            query,
            window_texts,
            scorer=fuzz.WRatio,
            limit=3,
        )
        if not hits:
            return self.position, 0.0

        best_text, best_score, rel_idx = hits[0]
        confidence  = best_score / 100.0
        best_start  = window_starts[rel_idx]

        if confidence >= MIN_CONFIDENCE:
            self._advance(best_start, query)
            self._lost      = 0
        else:
            self._lost += 1
            if self._lost >= LOSE_AFTER:
                # Lost the speaker — fall back to full scan
                self._mode      = 'scan'
                self._confident = 0

        return self.position, confidence

    def _advance(self, chunk_start: int, query: str):
        """Move position to the end of the matched region (never go backwards)."""
        query_word_count = len(query.split())
        new_pos = min(chunk_start + query_word_count, len(self.words) - 1)
        # Only advance, never jump backwards (avoids thrash on repeated words)
        if new_pos > self.position or (self.position - new_pos) < CHUNK_WORDS:
            self.position = new_pos

    # ── Claude fallback (called externally when confidence is very low) ────────

    async def claude_recovery(self, transcript: str):
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            return self.position, 0.0
        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=api_key)

            ctx_start = max(0, self.position - 40)
            ctx_end   = min(len(self.words), self.position + 200)
            context   = " ".join(w["word"] for w in self.words[ctx_start:ctx_end])

            msg = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=16,
                messages=[{"role": "user", "content": (
                    f'Script context (starts at word {ctx_start}):\n\n{context}\n\n'
                    f'Speaker just said: "{transcript}"\n\n'
                    f'Reply with ONLY the integer word index (from the full script start) '
                    f'where the speaker currently is.'
                )}],
            )
            raw = msg.content[0].text.strip()
            idx = int(re.search(r"\d+", raw).group())
            idx = max(0, min(idx, len(self.words) - 1))
            self.position = idx
            self._mode      = 'track'
            self._confident = LOCK_AFTER
            return self.position, 0.75
        except Exception:
            return self.position, 0.0


def _clean(text: str) -> str:
    return re.sub(r"[^\w\s']", "", text.lower()).strip()
