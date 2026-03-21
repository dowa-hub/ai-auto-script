import asyncio
import json
import os
import numpy as np
from pathlib import Path
from dotenv import load_dotenv

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from ingestion.parser import parse_script
from tracker import ScriptTracker, MIN_CONFIDENCE
from stt.offline import OfflineSTT
from stt.cloud import CloudSTT

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

STT_MODE = os.getenv("STT_MODE", "offline")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base.en")
DEEPGRAM_KEY = os.getenv("DEEPGRAM_API_KEY", "")

# Audio processing constants (16kHz mono from browser)
SAMPLE_RATE = 16000
CHUNK_SAMPLES = 4000          # 250ms per chunk
PROCESS_EVERY_N = 10          # run transcription every N chunks (~2.5s)
CONTEXT_CHUNKS = 20           # transcribe last N chunks (~5s of audio)
MAX_BUFFER_CHUNKS = 120       # keep last 30s of audio

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="AI Auto Script")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:4173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── State (single-session for now) ────────────────────────────────────────────

current_script: dict | None = None
tracker: ScriptTracker | None = None
stt_engine: OfflineSTT | CloudSTT | None = None
model_ready = False


async def build_stt(mode: str, status_cb=None) -> OfflineSTT | CloudSTT:
    if mode == "cloud":
        engine = CloudSTT(api_key=DEEPGRAM_KEY)
        await engine.initialize(status_callback=status_cb)
    else:
        engine = OfflineSTT(model_size=WHISPER_MODEL)
        await engine.initialize(status_callback=status_cb)
    return engine


@app.on_event("startup")
async def startup():
    global stt_engine, model_ready
    # Load model in the background so the server starts immediately
    asyncio.create_task(_init_stt_background())


async def _init_stt_background():
    global stt_engine, model_ready
    try:
        stt_engine = await build_stt(STT_MODE)
        model_ready = True
    except Exception as e:
        print(f"[STT] Failed to initialize: {e}")


# ── REST endpoints ─────────────────────────────────────────────────────────────

@app.post("/api/upload-script")
async def upload_script(file: UploadFile = File(...)):
    global current_script, tracker
    content = await file.read()
    try:
        data = parse_script(content, file.filename)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    current_script = data
    tracker = ScriptTracker(data["words"])
    return {
        "ok": True,
        "lines": data["line_count"],
        "words": data["word_count"],
    }


@app.get("/api/script")
async def get_script():
    return current_script or {}


@app.get("/api/status")
async def get_status():
    return {
        "model_ready": model_ready,
        "stt_mode": STT_MODE,
        "whisper_model": WHISPER_MODEL,
        "has_script": current_script is not None,
        "position": tracker.position if tracker else 0,
    }


@app.post("/api/settings")
async def update_settings(body: dict):
    global stt_engine, model_ready, STT_MODE, WHISPER_MODEL
    if "stt_mode" in body:
        STT_MODE = body["stt_mode"]
    if "whisper_model" in body:
        WHISPER_MODEL = body["whisper_model"]
    model_ready = False
    stt_engine = await build_stt(STT_MODE)
    model_ready = True
    return {"ok": True, "stt_mode": STT_MODE}


@app.post("/api/seek")
async def seek(body: dict):
    if not tracker:
        raise HTTPException(status_code=400, detail="No script loaded")
    word_index = body.get("word_index", 0)
    tracker.seek(word_index)
    return {"ok": True, "position": tracker.position}


@app.post("/api/resume")
async def resume():
    if tracker:
        tracker.resume()
    return {"ok": True}


@app.post("/api/reset")
async def reset():
    if tracker:
        tracker.reset()
    return {"ok": True}


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws/audio")
async def audio_ws(websocket: WebSocket):
    await websocket.accept()

    audio_buffer: list[np.ndarray] = []
    chunk_count = 0

    async def send(data: dict):
        try:
            await websocket.send_text(json.dumps(data))
        except Exception:
            pass

    # Send initial status
    await send({"type": "model_status", "status": "ready" if model_ready else "loading"})

    try:
        while True:
            data = await websocket.receive_bytes()

            # Each message is a 250ms chunk of 16kHz mono int16 PCM
            chunk = np.frombuffer(data, dtype=np.int16).copy()
            audio_buffer.append(chunk)
            chunk_count += 1

            # Keep rolling buffer
            if len(audio_buffer) > MAX_BUFFER_CHUNKS:
                audio_buffer.pop(0)

            # Transcribe every PROCESS_EVERY_N chunks
            if chunk_count % PROCESS_EVERY_N == 0 and model_ready and stt_engine and tracker:
                context = np.concatenate(audio_buffer[-CONTEXT_CHUNKS:])
                transcript = await stt_engine.transcribe(context)

                if transcript:
                    position, confidence = tracker.update(transcript)

                    # Fire Claude fallback if confidence is too low
                    if confidence < MIN_CONFIDENCE and not tracker.locked:
                        asyncio.create_task(_claude_fallback(send, transcript))

                    current_line = current_script["words"][position]["line_index"] if current_script else 0
                    await send({
                        "type": "position",
                        "word_index": position,
                        "line_index": current_line,
                        "confidence": round(confidence, 2),
                        "transcript": transcript,
                    })
                else:
                    # Silence / no speech
                    await send({"type": "silence"})

    except WebSocketDisconnect:
        pass


async def _claude_fallback(send, transcript: str):
    if not tracker:
        return
    position, confidence = await tracker.claude_recovery(transcript)
    if current_script:
        current_line = current_script["words"][position]["line_index"]
        await send({
            "type": "position",
            "word_index": position,
            "line_index": current_line,
            "confidence": round(confidence, 2),
            "transcript": transcript,
            "recovered": True,
        })


# ── Serve built frontend (production) ─────────────────────────────────────────

_frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="static")
