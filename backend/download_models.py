"""
Pre-download all Whisper models so there's no wait at runtime.
Run once: python download_models.py
"""
from faster_whisper import WhisperModel

MODELS = [
    ("tiny.en",   "~39 MB"),
    ("base.en",   "~150 MB"),
    ("small.en",  "~500 MB"),
    ("medium.en", "~1.5 GB"),
]

if __name__ == "__main__":
    for name, size in MODELS:
        print(f"  Downloading {name} ({size})...", end=" ", flush=True)
        try:
            WhisperModel(name, device="cpu", compute_type="int8")
            print("done")
        except Exception as e:
            print(f"FAILED: {e}")
    print("\nAll models cached. You won't need to wait again.")
