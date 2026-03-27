"""
Script position tracker — Constrained N-gram matching + Semantic Zone Recovery.

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
  5. No match — cursor stays put, miss counter increments
  6. After RELOCATE_AFTER misses — full-script trigram/bigram scan
  7. After ZONE_MISS_THRESHOLD misses + n-gram scan fails — semantic zone
     match using IDF keyword fingerprints (handles off-script speech)
"""
import os
import re
import math
import time
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
MAX_FORWARD_BASE = 40   # baseline forward window at ~150 WPM
MAX_FORWARD_MIN  = 30   # floor clamp
MAX_FORWARD_MAX  = 80   # ceiling clamp
MAX_BACK         = 5    # max words cursor can move backward (for repeats)
RELOCATE_AFTER   = 8    # consecutive misses before full-script n-gram rescan

# ── Dynamic window / speech rate ─────────────────────────────────────────────
WPS_DEFAULT    = 2.5   # default words per second (~150 WPM)
WPS_EMA_ALPHA  = 0.3   # EMA smoothing factor for speech rate estimate

# ── Semantic zone recovery ────────────────────────────────────────────────────
ZONE_SIZE           = 80   # words per zone
ZONE_TOP_KEYWORDS   = 12   # top IDF keywords stored per zone fingerprint
ZONE_BUFFER_SIZE    = 50   # recent words kept for zone scoring
ZONE_MISS_THRESHOLD = 16   # misses before attempting zone match (after n-gram fails)

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
    return re.sub(r"[^\w]", "", w.lower())


def _phonetic_key(word: str) -> str:
    """Simplified Metaphone-style phonetic hash (pure Python, no deps).

    Designed for STT mishearing patterns: consonant swaps, vowel variations,
    initial cluster reductions. E.g. 'hedgebrook' == 'hedgbrook' (vowel drop),
    'knocking' == 'nocking' (silent K), 'phone' == 'fone' (PH/F swap).
    """
    if not word:
        return ""
    w = word.upper()
    # Strip apostrophes before processing
    w = w.replace("'", "")
    if not w:
        return ""

    # Initial cluster reductions
    for prefix, replacement in [
        ("TSC", "S"), ("TS", "S"), ("WR", "R"), ("KN", "N"),
        ("GN", "N"), ("PN", "N"), ("AE", "E"), ("PH", "F"),
    ]:
        if w.startswith(prefix):
            w = replacement + w[len(prefix):]
            break

    # Keep first char, then transform remainder (drop vowels, map equivalences)
    result = [w[0]]
    for c in w[1:]:
        if c in "AEIOU":
            continue  # drop interior vowels
        if c in "DT":
            result.append("T")
        elif c in "BP":
            result.append("P")
        elif c in "CKQ":
            result.append("K")
        elif c in "SXZ":
            result.append("S")
        elif c in "GJ":
            result.append("J")
        elif c in "FV":
            result.append("F")
        elif c in "MN":
            result.append("N")
        else:
            result.append(c)

    # Deduplicate consecutive identical codes, truncate to 6
    deduped = [result[0]]
    for ch in result[1:]:
        if ch != deduped[-1]:
            deduped.append(ch)
    return "".join(deduped[:6])


def _is_partial_match(a: str, b: str) -> bool:
    """Catch STT near-misses: prefix, edit distance, or phonetic equivalence."""
    if not a or not b:
        return False
    # Rule 1: first 4 chars match (both >= 4 chars)
    if len(a) >= 4 and len(b) >= 4 and a[:4] == b[:4]:
        return True
    # Rule 2: short words (<=7 chars), length diff <=1, edit distance <=1
    if abs(len(a) - len(b)) <= 1 and max(len(a), len(b)) <= 7:
        diff = sum(1 for x, y in zip(a, b) if x != y) + abs(len(a) - len(b))
        if diff <= 1:
            return True
    # Rule 3: phonetic equivalence — catches consonant swaps, vowel drops
    if len(a) >= 4 and len(b) >= 4:
        if _phonetic_key(a) == _phonetic_key(b):
            return True
    return False


class ScriptTracker:
    def __init__(self, words: list):
        self.words = words
        self.position = 0
        self.locked = False
        self._script = [_clean(w["word"]) for w in words]
        self._buffer = []       # rolling buffer of recent STT words (n-gram matching)
        self._zone_buffer = []  # wider buffer for semantic zone scoring
        self._miss_count = 0       # consecutive updates with no match
        self._locked_on = False    # True after first confident match — enables constrained window
        self._provisional = False  # True during provisional lock — reverts to full scan on quick misses
        self._good_count = 0       # consecutive good matches — confirms lock is solid
        self._last_feed_time = None  # monotonic time of last feed_words call
        self._wps_ema = WPS_DEFAULT  # rolling words-per-second estimate

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

        # Zone fingerprints for semantic recovery
        self._zones = self._build_zones()

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

    def _build_zones(self) -> list:
        """Divide the script into zones and fingerprint each with top IDF keywords.

        Each zone is a tuple of (start_word_idx, end_word_idx, keyword_set).
        Keywords are the most distinctive words in that zone by IDF score,
        weighted by how often they appear within the zone itself.
        """
        zones = []
        for start in range(0, len(self._script), ZONE_SIZE):
            end = min(start + ZONE_SIZE, len(self._script))
            zone_words = self._script[start:end]
            # Score each word: idf * frequency within zone
            scored = {}
            for w in zone_words:
                if w in STOP_WORDS or not w:
                    continue
                idf = self._idf.get(w, 0)
                if idf > 0:
                    scored[w] = scored.get(w, 0) + idf
            top = sorted(scored.items(), key=lambda x: -x[1])[:ZONE_TOP_KEYWORDS]
            keywords = {w for w, _ in top}
            zones.append((start, end, keywords))
        return zones

    def _semantic_zone_match(self) -> tuple:
        """Match recent speech against zone fingerprints using IDF keyword overlap.

        Scores each zone by summing IDF weights of zone keywords that appear
        in the recent zone buffer. Returns the midpoint of the best zone.
        Confidence reflects how cleanly one zone beats the others.
        """
        if not self._zone_buffer or not self._zones:
            return None, 0.0

        buffer_set = set(self._zone_buffer)
        best_score  = 0.0
        second_score = 0.0
        best_zone   = None

        for start, end, keywords in self._zones:
            score = sum(self._idf.get(w, 0) for w in buffer_set if w in keywords)
            if score > best_score:
                second_score = best_score
                best_score   = score
                best_zone    = (start, end)
            elif score > second_score:
                second_score = score

        if best_zone is None or best_score == 0:
            return None, 0.0

        # Confidence based on how clearly one zone leads
        separation = (best_score - second_score) / max(best_score, 1)
        if separation > 0.4:
            conf = 0.70
        elif separation > 0.2:
            conf = 0.62
        else:
            conf = 0.0   # too ambiguous — two zones look equally likely, don't jump

        if conf == 0.0:
            return None, 0.0

        mid = (best_zone[0] + best_zone[1]) // 2
        log.debug(f"[ZONE] best_zone={best_zone} score={best_score:.2f} separation={separation:.2f} conf={conf} buf_sample={self._zone_buffer[-5:]}")
        return mid, conf

    def _dynamic_forward(self) -> int:
        """Forward window size scaled to current speech rate.

        At normal pace (~2.5 WPS / 150 WPM) returns MAX_FORWARD_BASE (40).
        At fast pace (~4 WPS / 240 WPM) returns ~64. Clamped to [30, 80].
        """
        scale = self._wps_ema / WPS_DEFAULT
        return max(MAX_FORWARD_MIN, min(MAX_FORWARD_MAX, int(MAX_FORWARD_BASE * scale)))

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
        """Try exact bigram match — scans all buffer bigrams, most recent first."""
        if len(self._buffer) < 2:
            return None, 0.0
        for i in range(len(self._buffer) - 1, 0, -1):
            bg = (self._buffer[i - 1], self._buffer[i])
            if bg[0] in STOP_WORDS and bg[1] in STOP_WORDS:
                continue
            positions = self._bigrams.get(bg, [])
            nearby = self._positions_near_cursor(positions, lo, hi)
            if nearby:
                best = min(nearby, key=lambda p: abs(p + 1 - self.position))
                return best + 1, 0.70
        return None, 0.0

    def _try_rare_unigram(self, lo: int, hi: int) -> tuple:
        """Try matching the most distinctive word in the buffer (highest IDF)."""
        if not self._buffer:
            return None, 0.0
        best_w, best_idf = None, 3.0  # minimum IDF threshold
        for w in self._buffer:
            if w not in STOP_WORDS:
                idf = self._idf.get(w, 0)
                if idf > best_idf:
                    best_idf, best_w = idf, w
        if best_w is None:
            return None, 0.0
        for j in range(lo, hi):
            if self._script[j] == best_w:
                return j, 0.40
            if _is_partial_match(self._script[j], best_w) and self._idf.get(self._script[j], 0) >= 3.0:
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
        # Track speech rate (EMA of words per second)
        now = time.monotonic()
        if self._last_feed_time is not None and new_words:
            dt = now - self._last_feed_time
            if 0.05 < dt < 10.0:
                instant_wps = len(new_words) / dt
                self._wps_ema = (WPS_EMA_ALPHA * instant_wps +
                                 (1 - WPS_EMA_ALPHA) * self._wps_ema)
        self._last_feed_time = now

        cleaned = [_clean(w) for w in new_words if _clean(w)]
        self._buffer.extend(cleaned)
        if len(self._buffer) > 20:
            self._buffer = self._buffer[-20:]
        # Zone buffer is wider — accumulates more context for semantic matching
        self._zone_buffer.extend(cleaned)
        if len(self._zone_buffer) > ZONE_BUFFER_SIZE:
            self._zone_buffer = self._zone_buffer[-ZONE_BUFFER_SIZE:]

        if self.locked:
            return self.position, 1.0
        if not self._buffer:
            return self.position, 0.0

        # Before first lock-on (or during provisional lock), search the entire
        # script freely. After confirmed lock, constrain window to prevent jumps.
        if self._locked_on and not self._provisional:
            lo = max(0, self.position - MAX_BACK)
            hi = min(len(self._script), self.position + self._dynamic_forward())
        else:
            lo = 0
            hi = len(self._script)

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
                if conf >= 0.80:
                    if not self._locked_on:
                        # First confident match — provisional lock
                        self._locked_on = True
                        self._provisional = True
                        self._good_count = 1
                        log.debug(f"[LOCK] provisional lock at pos {pos} conf={conf:.2f}")
                    else:
                        self._good_count += 1
                        if self._good_count >= 3 and self._provisional:
                            self._provisional = False
                            log.debug(f"[LOCK] lock confirmed at pos {pos} after {self._good_count} good matches")
                # Lower-confidence matches (bigram/unigram) don't confirm provisional lock —
                # only trigrams (conf >= 0.80) can. This prevents weak bigrams from locking
                # onto the wrong script position.
                return self.position, conf

        # No match in window — increment miss counter and hold position
        self._miss_count += 1

        # Provisional lock: revert to full-script scan quickly if we lose signal
        if self._provisional and self._miss_count >= 3:
            self._locked_on = False
            self._provisional = False
            self._good_count = 0
            log.debug(f"[LOCK] provisional lock reverted after {self._miss_count} misses — re-scanning full script")

        if self._miss_count >= RELOCATE_AFTER and len(self._buffer) >= 3:
            pos, conf = self._full_rescan()
            if conf > 0:
                if conf < 0.60:
                    # Large jump (>500 words) — don't trust it as an anchor.
                    # Unlock so the next update searches full script fresh rather
                    # than treating the bad position as confirmed.
                    self._locked_on = False
                    self._provisional = False
                    self._good_count = 0
                    log.debug(f"[RESCAN] large jump to {pos} (conf={conf:.2f}) — unlocking, will re-confirm from full script")
                return pos, conf
            # N-gram rescan found nothing — try semantic zone match after more misses
            if self._miss_count >= ZONE_MISS_THRESHOLD:
                pos, conf = self._semantic_zone_match()
                if pos is not None:
                    self.position = pos
                    self._miss_count = 0
                    return self.position, conf

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

    def confirmed_seek(self, word_index: int):
        """Seek + immediately enter confirmed lock mode (skips provisional phase)."""
        self.seek(word_index)
        self._locked_on   = True
        self._provisional = False
        self._good_count  = 5
        self._miss_count  = 0

    def resume(self):
        self.locked = False

    def reset(self):
        self.position = 0
        self.locked = False
        self._buffer = []
        self._zone_buffer = []
        self._miss_count = 0
        self._locked_on = False
        self._provisional = False
        self._good_count = 0
        self._last_feed_time = None
        self._wps_ema = WPS_DEFAULT

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
