import asyncio
import json
import os
import numpy as np
from pathlib import Path
from dotenv import load_dotenv

import mimetypes
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response, HTMLResponse

from ingestion.parser import parse_unified
from tracker import ScriptTracker, MIN_CONFIDENCE  # noqa: F401
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
PROCESS_EVERY_N = 3           # trigger check every N chunks (0.75s)
CONTEXT_CHUNKS  = 8           # transcribe last N chunks (2s) — short = fast
SCAN_CHUNKS     = 20          # longer clip (5s) used in scan mode for accuracy
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

current_script = None       # type: dict
tracker = None              # type: ScriptTracker
stt_engine = None           # type: OfflineSTT | CloudSTT
current_file_bytes = None   # raw uploaded file bytes
current_file_name = None    # original filename
current_html = None         # pre-built display HTML (same line numbering as word list)
model_ready = False


async def build_stt(mode: str, status_cb=None):
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
    global current_script, tracker, current_file_bytes, current_file_name, current_html
    content = await file.read()
    try:
        data = parse_unified(content, file.filename)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    current_script = data
    current_file_bytes = content
    current_file_name = file.filename
    current_html = data.get("html")   # None for PDF/image — frontend handles those natively
    tracker = ScriptTracker(data["words"])
    return {
        "ok": True,
        "lines": data["line_count"],
        "words": data["word_count"],
        "filename": file.filename,
    }


@app.get("/api/document/raw")
async def get_document_raw():
    if not current_file_bytes or not current_file_name:
        raise HTTPException(404, "No document loaded")
    mime = mimetypes.guess_type(current_file_name)[0] or "application/octet-stream"
    return Response(content=current_file_bytes, media_type=mime)


@app.get("/api/document/html")
async def get_document_html():
    if current_html is None:
        raise HTTPException(400, "No HTML available — file is rendered natively (PDF or image)")
    return HTMLResponse(current_html)


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
    global stt_engine, model_ready, STT_MODE, WHISPER_MODEL, DEEPGRAM_KEY
    if "stt_mode" in body:
        STT_MODE = body["stt_mode"]
    if "whisper_model" in body:
        WHISPER_MODEL = body["whisper_model"]
    if "deepgram_key" in body and body["deepgram_key"]:
        DEEPGRAM_KEY = body["deepgram_key"]   # memory only — never written to disk
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
    chunk_count    = 0
    is_transcribing = False   # offline only: skip trigger if one is already running

    async def send(data: dict):
        try:
            await websocket.send_text(json.dumps(data))
        except Exception:
            pass

    async def dispatch(transcript: str):
        """Update tracker and push position to frontend."""
        if not transcript or not tracker or not current_script:
            return
        position, confidence = tracker.update(transcript)
        current_line = current_script["words"][position]["line_index"]
        await send({
            "type":       "position",
            "word_index": position,
            "line_index": current_line,
            "confidence": round(confidence, 2),
            "transcript": transcript,
        })
        if confidence < MIN_CONFIDENCE and not tracker.locked:
            asyncio.create_task(_claude_fallback(send, transcript))

    async def run_offline():
        """Transcribe from buffer and dispatch. Runs in background task."""
        nonlocal is_transcribing
        try:
            # Use longer clip in scan mode for better initial lock-on accuracy
            n = SCAN_CHUNKS if tracker and tracker._mode == 'scan' else CONTEXT_CHUNKS
            context    = np.concatenate(audio_buffer[-n:])
            transcript = await stt_engine.transcribe(context)
            if transcript:
                await dispatch(transcript)
            else:
                await send({"type": "silence"})
        except Exception:
            pass
        finally:
            is_transcribing = False

    # Connect Deepgram if in cloud mode
    is_cloud = STT_MODE == "cloud" and isinstance(stt_engine, CloudSTT)
    if is_cloud:
        try:
            await stt_engine.connect()
            await send({"type": "stt_status", "engine": "deepgram", "connected": True, "model": "Nova-3"})
        except Exception as e:
            await send({"type": "stt_status", "engine": "deepgram", "connected": False, "error": str(e)})
    else:
        await send({"type": "stt_status", "engine": "whisper", "connected": True, "model": WHISPER_MODEL})

    await send({"type": "model_status", "status": "ready" if model_ready else "loading"})

    try:
        while True:
            data  = await websocket.receive_bytes()
            chunk = np.frombuffer(data, dtype=np.int16).copy()
            audio_buffer.append(chunk)
            chunk_count += 1

            if len(audio_buffer) > MAX_BUFFER_CHUNKS:
                audio_buffer.pop(0)

            if not model_ready or not stt_engine or not tracker:
                continue

            if is_cloud:
                # ── Cloud path: pipe every chunk to Deepgram, get transcripts back ──
                await stt_engine.send_chunk(chunk)
                transcript = stt_engine.pop_transcript()
                if transcript:
                    await dispatch(transcript)

            else:
                # ── Offline path: batch every N chunks, skip if already running ──
                if chunk_count % PROCESS_EVERY_N == 0 and not is_transcribing:
                    is_transcribing = True
                    asyncio.create_task(run_offline())

    except WebSocketDisconnect:
        if is_cloud:
            await stt_engine.close()


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
