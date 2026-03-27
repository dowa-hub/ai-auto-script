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


MAX_RECONNECT_ATTEMPTS = 8
RECONNECT_BASE_DELAY   = 1.0   # seconds — doubles each attempt, caps at 30s


class CloudSTT:
    def __init__(self, api_key: str):
        self.api_key  = api_key
        self.ready    = bool(api_key)
        self._ws      = None
        self._pending           = []    # final transcripts for position tracking
        self._interim_text      = ""    # latest partial result for live display
        self._lock              = asyncio.Lock()
        self._task              = None
        self._keepalive_task    = None
        self._reconnecting      = False
        self._closed            = False  # set True on intentional close

    async def initialize(self, status_callback=None):
        if not self.api_key:
            raise ValueError("DEEPGRAM_API_KEY is not set in backend/.env")
        if status_callback:
            await status_callback({
                "type": "model_status", "status": "ready", "model": "deepgram-nova-3"
            })

    async def connect(self):
        """Open the persistent WebSocket to Deepgram. Call once when listening starts."""
        self._closed = False
        await self._open_ws()
        self._task           = asyncio.create_task(self._receive_loop())
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def _open_ws(self):
        headers = {"Authorization": f"Token {self.api_key}"}
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        self._ws = await websockets.connect(DEEPGRAM_URL, extra_headers=headers, ssl=ssl_ctx)

    async def _keepalive_loop(self):
        """Send a KeepAlive ping every 8s so Deepgram doesn't close idle connections."""
        try:
            while True:
                await asyncio.sleep(8)
                if self._ws and not self._ws.closed:
                    await self._ws.send('{"type": "KeepAlive"}')
        except Exception:
            pass

    async def _receive_loop(self):
        """Background task — reads transcripts from Deepgram, reconnects on drop."""
        attempt = 0
        while not self._closed:
            try:
                async for message in self._ws:
                    attempt = 0   # reset backoff on successful message
                    data = json.loads(message)
                    if data.get("type") != "Results":
                        continue
                    text = (
                        data.get("channel", {})
                        .get("alternatives", [{}])[0]
                        .get("transcript", "")
                        .strip()
                    )
                    if not text:
                        continue
                    is_final = data.get("is_final", False)
                    async with self._lock:
                        if is_final:
                            self._pending.append(text)
                            self._interim_text = ""
                        else:
                            self._interim_text = text
            except Exception:
                pass

            if self._closed:
                break

            # Connection dropped — reconnect with exponential backoff
            if attempt >= MAX_RECONNECT_ATTEMPTS:
                print("[Deepgram] Max reconnect attempts reached — giving up")
                break
            delay = min(RECONNECT_BASE_DELAY * (2 ** attempt), 30)
            attempt += 1
            print(f"[Deepgram] Connection lost — reconnecting in {delay:.0f}s (attempt {attempt})")
            await asyncio.sleep(delay)
            try:
                await self._open_ws()
                print("[Deepgram] Reconnected")
            except Exception as e:
                print(f"[Deepgram] Reconnect failed: {e}")

    async def send_chunk(self, audio_int16: np.ndarray):
        """Pipe a raw PCM int16 chunk straight to Deepgram."""
        if self._ws and not self._ws.closed:
            try:
                await self._ws.send(audio_int16.tobytes())
            except Exception:
                pass

    def pop_transcript(self) -> str:
        """Non-blocking drain — returns final transcripts for position tracking."""
        if not self._pending:
            return ""
        items = self._pending[:]
        self._pending.clear()
        return " ".join(items)

    def peek_interim(self) -> str:
        """Return the latest partial transcript for live display (non-destructive)."""
        return self._interim_text

    async def close(self):
        self._closed = True
        if self._keepalive_task:
            self._keepalive_task.cancel()
        if self._task:
            self._task.cancel()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

