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
from tracker import ScriptTracker, log as tracker_log
from stt.cloud import CloudSTT

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

DEEPGRAM_KEY = os.getenv("DEEPGRAM_API_KEY", "")

# Audio processing constants (16kHz mono from browser)
SAMPLE_RATE = 16000
CHUNK_SAMPLES = 4000          # 250ms per chunk
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
stt_engine = None           # type: CloudSTT
current_file_bytes = None   # raw uploaded file bytes
current_file_name = None    # original filename
current_html = None         # pre-built display HTML (same line numbering as word list)
model_ready = False


@app.on_event("startup")
async def startup():
    _load_script_cache()  # restore last uploaded script


# ── REST endpoints ─────────────────────────────────────────────────────────────

_CACHE_DIR = Path(__file__).parent / ".cache"
_CACHE_DIR.mkdir(exist_ok=True)


def _save_script_cache(content: bytes, filename: str):
    """Persist uploaded script to disk so it survives server restarts."""
    (Path(_CACHE_DIR) / "last_script").write_bytes(content)
    (Path(_CACHE_DIR) / "last_script_name").write_text(filename)


def _load_script_cache():
    """Reload last uploaded script from disk cache."""
    global current_script, tracker, current_file_bytes, current_file_name, current_html
    cache_file = _CACHE_DIR / "last_script"
    name_file = _CACHE_DIR / "last_script_name"
    if cache_file.exists() and name_file.exists():
        content = cache_file.read_bytes()
        filename = name_file.read_text().strip()
        try:
            data = parse_unified(content, filename)
            current_script = data
            current_file_bytes = content
            current_file_name = filename
            current_html = data.get("html")
            tracker = ScriptTracker(data["words"])
            print(f"[Cache] Restored script '{filename}': {data['word_count']} words")
        except Exception as e:
            print(f"[Cache] Failed to restore script: {e}")


MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB


@app.post("/api/upload-script")
async def upload_script(file: UploadFile = File(...)):
    global current_script, tracker, current_file_bytes, current_file_name, current_html
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large — 50 MB max")
    try:
        data = parse_unified(content, file.filename)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    current_script = data
    current_file_bytes = content
    current_file_name = file.filename
    current_html = data.get("html")   # None for PDF/image — frontend handles those natively
    tracker = ScriptTracker(data["words"])
    _save_script_cache(content, file.filename)
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
        "has_script": current_script is not None,
        "position": tracker.position if tracker else 0,
    }


@app.post("/api/settings")
async def update_settings(body: dict):
    global stt_engine, model_ready, DEEPGRAM_KEY
    if "deepgram_key" in body and body["deepgram_key"]:
        DEEPGRAM_KEY = body["deepgram_key"]   # memory only — never written to disk
    model_ready = False
    engine = CloudSTT(api_key=DEEPGRAM_KEY)
    await engine.initialize()
    stt_engine = engine
    model_ready = True
    return {"ok": True}


@app.post("/api/seek")
async def seek(body: dict):
    if not tracker:
        raise HTTPException(status_code=400, detail="No script loaded")
    word_index = body.get("word_index", 0)
    tracker.seek(word_index)
    return {"ok": True, "position": tracker.position}


@app.post("/api/seek-confirmed")
async def seek_confirmed(body: dict):
    if not tracker:
        raise HTTPException(status_code=400, detail="No script loaded")
    word_index = body.get("word_index", 0)
    tracker.confirmed_seek(word_index)
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

    async def send(data: dict):
        try:
            await websocket.send_text(json.dumps(data))
        except Exception:
            pass

    async def dispatch(transcript: str):
        """Push live transcript and, if a script is loaded, update tracker position."""
        if not transcript:
            return
        # Always show what was heard
        await send({"type": "transcript", "text": transcript})
        if not tracker or not current_script:
            return

        old_pos = tracker.position
        position, confidence = tracker.update(transcript)

        MIN_CONFIDENCE = 0.60

        if confidence == 0:
            # No match found — tell frontend how long we've been waiting
            miss = tracker._miss_count
            state = "searching" if miss >= 8 else "waiting"
            tracker_log.debug(f"[MISS]  \"{transcript}\" → pos stays {position}, buf={tracker._buffer[-5:]}")
            await send({"type": "tracker_status", "state": state, "confidence": 0, "miss_count": miss})
            return

        if confidence < MIN_CONFIDENCE:
            # Match found but too weak to move cursor
            tracker_log.debug(f"[SKIP]  \"{transcript}\" → conf={confidence:.2f} below threshold, pos stays {position}")
            await send({"type": "tracker_status", "state": "holding", "confidence": round(confidence, 2), "miss_count": tracker._miss_count})
            return

        # Good match — move cursor and send position
        lookahead = min(position + 3, len(current_script["words"]) - 1)
        display_line = current_script["words"][lookahead]["line_index"]
        current_line = current_script["words"][position]["line_index"]
        ctx_s = max(0, position - 2)
        ctx_e = min(len(current_script["words"]), position + 6)
        ctx = " ".join(current_script["words"][j]["word"] for j in range(ctx_s, ctx_e))
        tracker_log.debug(f"[TRACK] \"{transcript}\" → pos={old_pos}→{position} line={current_line}→display={display_line} conf={confidence:.2f} buf={tracker._buffer[-5:]}")
        tracker_log.debug(f"        script: ...{ctx}...")
        await send({
            "type":       "position",
            "word_index": lookahead,
            "line_index": display_line,
            "confidence": round(confidence, 2),
            "transcript": transcript,
        })

    # Connect Deepgram
    if stt_engine:
        try:
            await stt_engine.connect()
            await send({"type": "stt_status", "engine": "deepgram", "connected": True, "model": "Nova-3"})
        except Exception as e:
            await send({"type": "stt_status", "engine": "deepgram", "connected": False, "error": str(e)})
    else:
        await send({"type": "stt_status", "engine": "deepgram", "connected": False, "error": "No API key set"})

    await send({"type": "model_status", "status": "ready" if model_ready else "loading"})

    try:
        while True:
            data  = await websocket.receive_bytes()
            chunk = np.frombuffer(data, dtype=np.int16).copy()
            audio_buffer.append(chunk)
            chunk_count += 1

            if len(audio_buffer) > MAX_BUFFER_CHUNKS:
                audio_buffer.pop(0)

            if not model_ready or not stt_engine:
                continue

            await stt_engine.send_chunk(chunk)
            # Show interim (partial) results live
            interim = stt_engine.peek_interim()
            if interim:
                await send({"type": "transcript", "text": interim})
            # Feed final words to the tracker
            final = stt_engine.pop_transcript()
            if final:
                await dispatch(final)

    except WebSocketDisconnect:
        if stt_engine:
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
