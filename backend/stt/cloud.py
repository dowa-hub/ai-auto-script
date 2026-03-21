"""
Deepgram Nova-3 real-time streaming STT.

Audio chunks are piped directly to Deepgram's WebSocket as they arrive.
Deepgram returns transcripts in <300ms with no buffering on our end.

Enable with: STT_MODE=cloud and DEEPGRAM_API_KEY=dg_... in backend/.env
"""
import asyncio
import json
import ssl
import certifi
import numpy as np
import websockets

DEEPGRAM_URL = (
    "wss://api.deepgram.com/v1/listen"
    "?model=nova-3"
    "&language=en"
    "&encoding=linear16"
    "&sample_rate=16000"
    "&channels=1"
    "&interim_results=true"
    "&punctuate=true"
    "&endpointing=200"          # fire result after 200ms of silence
    "&utterance_end_ms=1000"
)


class CloudSTT:
    def __init__(self, api_key: str):
        self.api_key  = api_key
        self.ready    = bool(api_key)
        self._ws      = None
        self._pending = []          # transcripts waiting to be popped
        self._lock    = asyncio.Lock()
        self._task    = None

    async def initialize(self, status_callback=None):
        if not self.api_key:
            raise ValueError("DEEPGRAM_API_KEY is not set in backend/.env")
        if status_callback:
            await status_callback({
                "type": "model_status", "status": "ready", "model": "deepgram-nova-3"
            })

    async def connect(self):
        """Open the persistent WebSocket to Deepgram. Call once when listening starts."""
        headers = {"Authorization": f"Token {self.api_key}"}
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        self._ws = await websockets.connect(DEEPGRAM_URL, extra_headers=headers, ssl=ssl_ctx)
        self._task = asyncio.create_task(self._receive_loop())

    async def _receive_loop(self):
        """Background task — reads transcripts from Deepgram as they arrive."""
        try:
            async for message in self._ws:
                data = json.loads(message)
                if data.get("type") != "Results":
                    continue
                text = (
                    data.get("channel", {})
                    .get("alternatives", [{}])[0]
                    .get("transcript", "")
                    .strip()
                )
                if text:
                    async with self._lock:
                        self._pending.append(text)
        except Exception:
            pass

    async def send_chunk(self, audio_int16: np.ndarray):
        """Pipe a raw PCM int16 chunk straight to Deepgram."""
        if self._ws and self._ws.close_code is None:
            try:
                await self._ws.send(audio_int16.tobytes())
            except Exception:
                pass

    def pop_transcript(self) -> str:
        """Non-blocking drain — returns all pending transcripts joined, or empty string."""
        if not self._pending:
            return ""
        # Grab everything accumulated since last pop
        items = self._pending[:]
        self._pending.clear()
        return " ".join(items)

    async def close(self):
        if self._task:
            self._task.cancel()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

    # Compatibility shim so the offline code path can call transcribe() on either engine
    async def transcribe(self, audio_int16: np.ndarray) -> str:
        await self.send_chunk(audio_int16)
        await asyncio.sleep(0.35)      # give Deepgram time to respond
        return self.pop_transcript()
