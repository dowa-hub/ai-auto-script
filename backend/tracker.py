"""
Script position tracker — Constrained N-gram matching.

Based on research into professional teleprompter algorithms (PromptSmart,
Autoscript Voice, CuePrompter). Instead of Smith-Waterman alignment, uses
pre-indexed bigrams/trigrams with strict positional constraints.

Key insight: trigrams of consecutive words are almost always unique in any
script. Combined with a cursor that can only move forward (or back a few
words), false matches become nearly impossible.

Algorithm layers (tried in order):
  1. Trigram exact match near cursor (most distinctive, highest confidence)
  2. Trigram fuzzy match (allows 1 STT error in the trigram)
  3. Bigram exact match near cursor
  4. Rare unigram match (IDF > threshold)
  5. No match — cursor stays put
  6. After sustained misses — full-script trigram scan to relocate
"""
import os
import re
import math
import asyncio
import logging
from collections import Counter
from pathlib import Path

# File logger so we can read logs without terminal access
_log_path = Path(__file__).parent / "tracker.log"
_fh = logging.FileHandler(str(_log_path), mode="w")  # overwrite each restart
_fh.setFormatter(logging.Formatter("%(message)s"))
log = logging.getLogger("tracker")
log.addHandler(_fh)
log.setLevel(logging.DEBUG)

# ── Cursor movement bounds (in words) ────────────────────────────────────────
MAX_FORWARD  = 40    # max words cursor can jump forward per update
MAX_BACK     = 5     # max words cursor can move backward (for repeats)
RELOCATE_AFTER = 8   # consecutive misses before full-script rescan

# ── Stop words (ignored for unigram matching, still used in n-grams) ─────────
STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "it", "its", "are", "was", "were",
    "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might", "can", "shall",
    "not", "no", "so", "if", "as", "that", "this", "these", "those",
    "i", "we", "you", "he", "she", "they", "me", "us", "him", "her",
    "them", "my", "our", "your", "his", "their", "all", "up", "out",
})


def _clean(w: str) -> str:
    return re.sub(r"[^\w']", "", w.lower())


def _is_partial_match(a: str, b: str) -> bool:
    """Catch STT near-misses like 'communities' → 'comunities'."""
    if not a or not b:
        return False
    if len(a) >= 4 and len(b) >= 4 and a[:4] == b[:4]:
        return True
    if abs(len(a) - len(b)) <= 1 and max(len(a), len(b)) <= 7:
        diff = sum(1 for x, y in zip(a, b) if x != y) + abs(len(a) - len(b))
        if diff <= 1:
            return True
    return False


class ScriptTracker:
    def __init__(self, words: list):
        self.words = words
        self.position = 0
        self.locked = False
        self._script = [_clean(w["word"]) for w in words]
        self._buffer = []       # rolling buffer of recent STT words
        self._miss_count = 0    # consecutive updates with no match

        # Pre-build n-gram indices: n-gram → [positions]
        self._bigrams = {}
        self._trigrams = {}
        for i in range(len(self._script) - 1):
            key = (self._script[i], self._script[i + 1])
            self._bigrams.setdefault(key, []).append(i)
        for i in range(len(self._script) - 2):
            key = (self._script[i], self._script[i + 1], self._script[i + 2])
            self._trigrams.setdefault(key, []).append(i)

        # IDF for rare-word detection
        self._idf = self._build_idf()

    def _build_idf(self) -> dict:
        counts = Counter(self._script)
        n = len(self._script)
        idf = {}
        for word, count in counts.items():
            if word in STOP_WORDS or not word:
                idf[word] = 0.0
            else:
                idf[word] = max(0.5, min(5.0, math.log(max(1, n) / max(1, count))))
        return idf

    # ── Core matching ─────────────────────────────────────────────────────────

    def _positions_near_cursor(self, positions: list, lo: int, hi: int) -> list:
        """Filter positions to those within cursor bounds."""
        return [p for p in positions if lo <= p <= hi]

    def _try_trigram_exact(self, lo: int, hi: int) -> tuple:
        """Try exact trigram match from the last 3 buffer words."""
        if len(self._buffer) < 3:
            return None, 0.0
        tg = tuple(self._buffer[-3:])
        positions = self._trigrams.get(tg, [])
        nearby = self._positions_near_cursor(positions, lo, hi)
        if nearby:
            best = min(nearby, key=lambda p: abs(p + 2 - self.position))
            return best + 2, 0.95  # point to END of trigram (where speaker is)
        return None, 0.0

    def _try_trigram_fuzzy(self, lo: int, hi: int) -> tuple:
        """Try trigram with 1 fuzzy word (STT error)."""
        if len(self._buffer) < 3:
            return None, 0.0
        w1, w2, w3 = self._buffer[-3], self._buffer[-2], self._buffer[-1]
        for j in range(lo, min(hi, len(self._script) - 2)):
            s1, s2, s3 = self._script[j], self._script[j + 1], self._script[j + 2]
            exact = (w1 == s1) + (w2 == s2) + (w3 == s3)
            if exact >= 2:
                # 2 of 3 exact — check the third is a partial match
                if exact == 3 or \
                   (w1 != s1 and _is_partial_match(w1, s1)) or \
                   (w2 != s2 and _is_partial_match(w2, s2)) or \
                   (w3 != s3 and _is_partial_match(w3, s3)):
                    return j + 2, 0.85
        return None, 0.0

    def _try_bigram_exact(self, lo: int, hi: int) -> tuple:
        """Try exact bigram match from last 2 buffer words."""
        if len(self._buffer) < 2:
            return None, 0.0
        bg = tuple(self._buffer[-2:])
        # Skip if both words are stop words (not distinctive)
        if bg[0] in STOP_WORDS and bg[1] in STOP_WORDS:
            return None, 0.0
        positions = self._bigrams.get(bg, [])
        nearby = self._positions_near_cursor(positions, lo, hi)
        if nearby:
            best = min(nearby, key=lambda p: abs(p + 1 - self.position))
            return best + 1, 0.70
        return None, 0.0

    def _try_rare_unigram(self, lo: int, hi: int) -> tuple:
        """Try matching a single rare word (high IDF)."""
        if not self._buffer:
            return None, 0.0
        w = self._buffer[-1]
        if w in STOP_WORDS or self._idf.get(w, 0) < 3.0:
            return None, 0.0
        for j in range(lo, hi):
            if self._script[j] == w:
                return j, 0.40
            if _is_partial_match(self._script[j], w) and self._idf.get(self._script[j], 0) >= 3.0:
                return j, 0.30
        return None, 0.0

    def _full_rescan(self) -> tuple:
        """Full-script trigram scan to relocate when completely lost."""
        if len(self._buffer) < 3:
            return self.position, 0.0

        # Try trigrams from END of buffer first (most recent words)
        best_pos = None
        matched_tg = None
        for i in range(len(self._buffer) - 3, -1, -1):
            tg = tuple(self._buffer[i:i + 3])
            positions = self._trigrams.get(tg, [])
            if positions:
                # If cursor > 0 (we've been tracking), pick occurrence nearest cursor
                if self.position > 0:
                    best_pos = min(positions, key=lambda p: abs(p - self.position)) + 2
                else:
                    best_pos = positions[0] + 2
                matched_tg = tg
                break

        if best_pos is not None:
            jump = abs(best_pos - self.position)
            # Penalise confidence for large jumps — a 2000-word leap is not 80% confident
            if jump > 500:
                conf = 0.45
            elif jump > 100:
                conf = 0.60
            else:
                conf = 0.80
            log.debug(f"[RESCAN] trigram {matched_tg} → pos {best_pos} (jump={jump}, conf={conf}), buffer={self._buffer}")
            self.position = best_pos
            self._miss_count = 0
            # Discard stale off-script words — keep only the matched trigram so
            # future updates start clean rather than re-matching old content.
            self._buffer = list(matched_tg)
            return self.position, conf

        # Trigram not found — try bigrams across full script
        for i in range(len(self._buffer) - 1):
            bg = tuple(self._buffer[i:i + 2])
            if bg[0] in STOP_WORDS and bg[1] in STOP_WORDS:
                continue
            positions = self._bigrams.get(bg, [])
            if positions:
                jump = abs(positions[0] + 1 - self.position)
                conf = 0.40 if jump > 100 else 0.60
                best_pos = positions[0] + 1
                self.position = best_pos
                self._miss_count = 0
                self._buffer = list(bg)
                return self.position, conf

        return self.position, 0.0

    # ── Public API ────────────────────────────────────────────────────────────

    def feed_words(self, new_words: list, raw_transcript: str = ""):
        cleaned = [_clean(w) for w in new_words if _clean(w)]
        self._buffer.extend(cleaned)
        # Keep buffer manageable
        if len(self._buffer) > 20:
            self._buffer = self._buffer[-20:]

        if self.locked:
            return self.position, 1.0
        if not self._buffer:
            return self.position, 0.0

        # Define cursor bounds
        lo = max(0, self.position - MAX_BACK)
        hi = min(len(self._script), self.position + MAX_FORWARD)

        # Try matching layers in order of confidence
        for try_fn in [
            self._try_trigram_exact,
            self._try_trigram_fuzzy,
            self._try_bigram_exact,
            self._try_rare_unigram,
        ]:
            pos, conf = try_fn(lo, hi)
            if pos is not None:
                log.debug(f"[MATCH] {try_fn.__name__}: pos {self.position}→{pos}, window=[{lo},{hi}], last3={self._buffer[-3:] if len(self._buffer)>=3 else self._buffer}")
                self.position = pos
                self._miss_count = 0
                return self.position, conf

        # No match in window — increment miss counter and hold position
        # Only rescan after RELOCATE_AFTER consecutive misses so off-script
        # speech doesn't cause the cursor to jump around.
        self._miss_count += 1
        if self._miss_count >= RELOCATE_AFTER and len(self._buffer) >= 3:
            return self._full_rescan()

        return self.position, 0.0

    def update(self, transcript: str):
        words = [w for w in transcript.split() if w.strip()]
        return self.feed_words(words, raw_transcript=transcript)

    def initial_scan(self, transcript: str):
        """Full-script scan for first lock-on. Uses trigram matching."""
        words = [_clean(w) for w in transcript.split() if _clean(w)]
        if len(words) < 3:
            return self.position, 0.0

        self._buffer = words[-20:]  # seed buffer

        # Try trigrams from the transcript against full script
        for i in range(len(words) - 2):
            tg = tuple(words[i:i + 3])
            positions = self._trigrams.get(tg, [])
            if positions:
                self.position = positions[0] + 2
                return self.position, 0.90

        # Fall back to bigrams
        for i in range(len(words) - 1):
            bg = tuple(words[i:i + 2])
            if bg[0] in STOP_WORDS and bg[1] in STOP_WORDS:
                continue
            positions = self._bigrams.get(bg, [])
            if positions:
                self.position = positions[0] + 1
                return self.position, 0.70

        return self.position, 0.0

    def seek(self, word_index: int):
        self.position = max(0, min(word_index, len(self.words) - 1))
        self.locked = True

    def resume(self):
        self.locked = False

    def reset(self):
        self.position = 0
        self.locked = False
        self._buffer = []
        self._miss_count = 0

    # ── Claude fallback ───────────────────────────────────────────────────────

    async def claude_recovery(self, transcript: str):
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            return self.position, 0.0
        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=api_key)
            ctx_start = max(0, self.position - 40)
            ctx_end = min(len(self.words), self.position + 200)
            context = " ".join(w["word"] for w in self.words[ctx_start:ctx_end])
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
            return self.position, 0.75
        except Exception:
            return self.position, 0.0
