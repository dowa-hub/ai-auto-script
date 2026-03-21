"""
Script position tracker.

Primary path  : RapidFuzz sliding-window match (~50ms cycle)
Fallback path : Claude API context match (async, only on low confidence)
"""
import os
import re
import asyncio
from rapidfuzz import fuzz


MIN_CONFIDENCE = 0.45          # below this, Claude fallback fires (if configured)
WINDOW_FORWARD = 80            # words to look ahead of current position
WINDOW_BACK = 15               # words to look behind (handles repeated lines)
QUERY_LOOK_BACK = 6            # extra context words prepended to query


class ScriptTracker:
    def __init__(self, words: list[dict]):
        self.words = words
        self.position = 0          # current word index
        self.locked = False        # True when operator has manually grabbed control

    # ── Public API ────────────────────────────────────────────────────────────

    def update(self, transcript: str) -> tuple[int, float]:
        """Match transcript against script, advance position. Returns (word_index, confidence)."""
        if self.locked or not transcript or not self.words:
            return self.position, 1.0 if self.locked else 0.0

        query = _clean(transcript)
        if not query:
            return self.position, 0.0

        start = max(0, self.position - WINDOW_BACK)
        end = min(len(self.words), self.position + WINDOW_FORWARD)

        query_words = query.split()
        query_len = len(query_words)

        best_score = 0.0
        best_pos = self.position

        for i in range(start, end):
            chunk_end = min(i + query_len + 8, end)
            chunk = " ".join(_clean(w["word"]) for w in self.words[i:chunk_end])
            score = fuzz.partial_ratio(query, chunk) / 100.0
            if score > best_score:
                best_score = score
                best_pos = i

        if best_score >= MIN_CONFIDENCE:
            # Advance to end of matched region
            new_pos = min(best_pos + max(1, query_len - 2), len(self.words) - 1)
            self.position = new_pos

        return self.position, best_score

    async def claude_recovery(self, transcript: str) -> tuple[int, float]:
        """
        Ask Claude to locate the transcript in the script.
        Only called when RapidFuzz confidence is too low.
        Requires ANTHROPIC_API_KEY in environment.
        """
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            return self.position, 0.0

        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=api_key)

            # Build context: 200 words around current position
            ctx_start = max(0, self.position - 30)
            ctx_end = min(len(self.words), self.position + 170)
            script_context = " ".join(w["word"] for w in self.words[ctx_start:ctx_end])

            prompt = (
                f"You are tracking position in a script. "
                f"The speaker recently said:\n\n\"{transcript}\"\n\n"
                f"Here is the script context (starting at word {ctx_start}):\n\n"
                f"{script_context}\n\n"
                f"Which word index (0-based, counting from the start of the FULL script, "
                f"not just this context) does the speaker appear to be at RIGHT NOW? "
                f"Respond with ONLY an integer."
            )

            message = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=16,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text.strip()
            idx = int(re.search(r"\d+", raw).group())
            idx = max(0, min(idx, len(self.words) - 1))
            self.position = idx
            return self.position, 0.75  # approximate confidence after recovery
        except Exception:
            return self.position, 0.0

    def seek(self, word_index: int):
        """Manual seek — operator grabbed control."""
        self.position = max(0, min(word_index, len(self.words) - 1))
        self.locked = True

    def resume(self):
        """Resume auto-tracking after manual override."""
        self.locked = False

    def reset(self):
        self.position = 0
        self.locked = False


def _clean(text: str) -> str:
    return re.sub(r"[^\w\s']", "", text.lower()).strip()
