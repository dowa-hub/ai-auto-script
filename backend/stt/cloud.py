"""
Deepgram Nova-3 cloud STT — enabled by setting STT_MODE=cloud in .env
and providing a DEEPGRAM_API_KEY.
"""
import asyncio
import json
import numpy as np
import websockets


DEEPGRAM_WS_URL = (
    "wss://api.deepgram.com/v1/listen"
    "?model=nova-3&language=en&encoding=linear16&sample_rate=16000"
    "&channels=1&interim_results=true&punctuate=true"
)


class CloudSTT:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.ready = bool(api_key)
        self._ws = None
        self._pending: list[str] = []
        self._lock = asyncio.Lock()

    async def initialize(self, status_callback=None):
        if not self.api_key:
            raise ValueError("DEEPGRAM_API_KEY is not set")
        if status_callback:
            await status_callback({"type": "model_status", "status": "ready", "model": "deepgram-nova-3"})

    async def connect(self):
        """Open persistent WebSocket to Deepgram."""
        headers = {"Authorization": f"Token {self.api_key}"}
        self._ws = await websockets.connect(DEEPGRAM_WS_URL, additional_headers=headers)
        asyncio.create_task(self._receive_loop())

    async def _receive_loop(self):
        try:
            async for message in self._ws:
                data = json.loads(message)
                if data.get("type") == "Results":
                    transcript = (
                        data.get("channel", {})
                        .get("alternatives", [{}])[0]
                        .get("transcript", "")
                    )
                    if transcript:
                        async with self._lock:
                            self._pending.append(transcript)
        except Exception:
            pass

    async def send_audio(self, audio_int16: np.ndarray):
        if self._ws:
            await self._ws.send(audio_int16.tobytes())

    async def transcribe(self, audio_int16: np.ndarray) -> str:
        """For compatibility with the offline interface — sends audio and flushes pending."""
        if self._ws:
            await self._ws.send(audio_int16.tobytes())
        async with self._lock:
            result = " ".join(self._pending)
            self._pending.clear()
        return result

    async def close(self):
        if self._ws:
            await self._ws.close()
