import asyncio
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from faster_whisper import WhisperModel


class OfflineSTT:
    def __init__(self, model_size: str = "base.en"):
        self.model_size = model_size
        self.model = None  # type: WhisperModel
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.ready = False

    async def initialize(self, status_callback=None):
        """Download and load the Whisper model. Runs in a thread so it doesn't block."""
        if status_callback:
            await status_callback({"type": "model_status", "status": "loading", "model": self.model_size})

        loop = asyncio.get_event_loop()
        try:
            self.model = await loop.run_in_executor(
                self.executor,
                lambda: WhisperModel(self.model_size, device="cpu", compute_type="int8"),
            )
            self.ready = True
            if status_callback:
                await status_callback({"type": "model_status", "status": "ready", "model": self.model_size})
        except Exception as e:
            if status_callback:
                await status_callback({"type": "model_status", "status": "error", "error": str(e)})
            raise

    async def transcribe(self, audio_int16: np.ndarray) -> str:
        """Transcribe a numpy int16 audio array (16kHz mono). Returns transcribed text."""
        if not self.ready or self.model is None:
            return ""

        audio_float32 = audio_int16.astype(np.float32) / 32768.0

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            self.executor,
            lambda: self._run_transcription(audio_float32),
        )
        return result

    async def close(self):
        pass  # nothing to close for offline STT

    def _run_transcription(self, audio: np.ndarray) -> str:
        segments, _ = self.model.transcribe(
            audio,
            language="en",
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
            word_timestamps=False,
        )
        words = []
        for segment in segments:
            words.append(segment.text.strip())
        return " ".join(words)
