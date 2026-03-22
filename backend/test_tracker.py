"""
Diagnostic test: upload the real script, feed real transcripts,
and print exactly what the tracker does at each step.

Usage: python test_tracker.py
(Run from the backend/ directory while the server is running on port 8000)
"""
import requests
import json
import sys

BASE = "http://localhost:8000"

def main():
    # 1. Fetch the currently loaded script from the running server
    print("=" * 70)
    print("FETCHING SCRIPT FROM SERVER...")
    resp = requests.get(f"{BASE}/api/script")
    if resp.status_code != 200 or not resp.json():
        print("ERROR: No script loaded on server. Upload one first.")
        sys.exit(1)

    script_data = resp.json()
    words = script_data["words"]
    print(f"Script loaded: {len(words)} words, {script_data['line_count']} lines")

    # 2. Import the tracker and create a fresh instance
    from tracker import ScriptTracker, _smith_waterman, _clean, STOP_WORDS

    tracker = ScriptTracker(words)

    # 3. Show a portion of the script so we can see what's there
    print("\n" + "=" * 70)
    print("FIRST 200 WORDS OF SCRIPT (with indices):")
    print("=" * 70)
    for i, w in enumerate(words[:200]):
        stop = " [STOP]" if _clean(w["word"]) in STOP_WORDS else ""
        print(f"  [{i:4d}] line={w['line_index']:3d}  '{w['word']}'{stop}")

    # 4. Search for "whidbey" in the full script to find where it appears
    print("\n" + "=" * 70)
    print("SEARCHING FOR 'whidbey' IN SCRIPT:")
    print("=" * 70)
    clean_script = [_clean(w["word"]) for w in words]
    for i, cw in enumerate(clean_script):
        if "whidbey" in cw:
            ctx_start = max(0, i - 5)
            ctx_end = min(len(words), i + 15)
            ctx = " ".join(words[j]["word"] for j in range(ctx_start, ctx_end))
            print(f"  Found at word index {i}, line {words[i]['line_index']}: ...{ctx}...")

    # Also search for "hedgebrook"
    print("\nSEARCHING FOR 'hedgebrook' IN SCRIPT:")
    for i, cw in enumerate(clean_script):
        if "hedgebrook" in cw:
            print(f"  Found at word index {i}, line {words[i]['line_index']}: '{words[i]['word']}'")

    # 5. Now simulate the transcript the user actually said
    test_transcripts = [
        "on the magical whidbey island hedgebrook offers women an opportunity to put the larger world on pause for a moment so that they can focus inward",
        "jump into the chat on the right side of your screen and say hello",
    ]

    for transcript in test_transcripts:
        print("\n" + "=" * 70)
        print(f"TESTING TRANSCRIPT: \"{transcript}\"")
        print("=" * 70)

        # Reset tracker for each test
        tracker2 = ScriptTracker(words)

        # First, test initial_scan (what happens on first lock-on)
        print("\n--- initial_scan() ---")
        position, confidence = tracker2.initial_scan(transcript)
        if position < len(words):
            line_idx = words[position]["line_index"]
            ctx_start = max(0, position - 3)
            ctx_end = min(len(words), position + 10)
            ctx = " ".join(words[j]["word"] for j in range(ctx_start, ctx_end))
            print(f"  Result: position={position}, line={line_idx}, confidence={confidence:.2f}")
            print(f"  Script at position: ...{ctx}...")
        else:
            print(f"  Result: position={position}, confidence={confidence:.2f}")

        # Also test feed_words (what happens during tracking)
        tracker3 = ScriptTracker(words)
        print("\n--- feed_words() (from position 0) ---")
        t_words = [w for w in transcript.split() if w.strip()]
        position, confidence = tracker3.feed_words(t_words)
        if position < len(words):
            line_idx = words[position]["line_index"]
            ctx_start = max(0, position - 3)
            ctx_end = min(len(words), position + 10)
            ctx = " ".join(words[j]["word"] for j in range(ctx_start, ctx_end))
            print(f"  Result: position={position}, line={line_idx}, confidence={confidence:.2f}")
            print(f"  Script at position: ...{ctx}...")
        else:
            print(f"  Result: position={position}, confidence={confidence:.2f}")

        # Run detailed SW alignment against EVERY window to see where the best match is
        print("\n--- Detailed SW scan (every window) ---")
        query = [_clean(w) for w in transcript.split() if _clean(w)]
        content_words = [w for w in query if w not in STOP_WORDS]
        print(f"  Query: {query}")
        print(f"  Content words (non-stop): {content_words}")
        print(f"  Stop words filtered out: {[w for w in query if w in STOP_WORDS]}")

        results = []
        step = 30
        window_sz = 120
        for start in range(0, len(clean_script), step):
            end = min(len(clean_script), start + window_sz)
            window = clean_script[start:end]
            score, ref_start, ref_end, content_matches = _smith_waterman(
                query, window, tracker2._idf
            )
            if score > 5:  # show anything above trivial
                abs_start = start + ref_start
                abs_end = start + ref_end
                ctx = " ".join(words[j]["word"] for j in range(abs_start, min(abs_end + 1, len(words))))
                results.append((score, content_matches, abs_start, abs_end, ctx))

        results.sort(key=lambda x: x[0], reverse=True)
        print(f"\n  Top 10 matches:")
        for i, (score, cm, astart, aend, ctx) in enumerate(results[:10]):
            line_s = words[astart]["line_index"] if astart < len(words) else "?"
            line_e = words[aend]["line_index"] if aend < len(words) else "?"
            print(f"  #{i+1}: score={score:.1f}, content_matches={cm}, "
                  f"words[{astart}..{aend}], lines {line_s}-{line_e}")
            print(f"       \"{ctx[:100]}...\"" if len(ctx) > 100 else f"       \"{ctx}\"")

    print("\n" + "=" * 70)
    print("DONE")


if __name__ == "__main__":
    main()
