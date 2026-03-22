import io
import os
import wave
from typing import Any, Dict, Tuple

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from backend.schemas import ChatRequest, ChatResponse, ErrorResponse, STTResponse, TTSRequest
from core.pipeline_service import SafeWashPipeline
from core.voice_engine import blaze_stt, blaze_tts, openai_stt, openai_tts
load_dotenv()

app = FastAPI(title="WashGo API", version="1.0.0")
pipeline = SafeWashPipeline()
BACKEND_HOST = os.getenv("BACKEND_HOST", "0.0.0.0")
BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8000"))
BACKEND_RELOAD = os.getenv("BACKEND_RELOAD", "true").lower() in {"1", "true", "yes"}

origins = [o.strip() for o in os.getenv("FRONTEND_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def estimate_wav_duration_seconds(audio_bytes: bytes) -> float | None:
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate() or 1
            return round(frames / float(rate), 2)
    except Exception:
        return None


def extract_blaze_text(stt_result: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    logs = []
    result_obj = stt_result.get("result")
    inner = None
    source = "unknown"

    if isinstance(result_obj, dict) and isinstance(result_obj.get("data"), dict):
        inner = result_obj.get("data")
        source = "result.data"
    elif isinstance(stt_result.get("data"), dict):
        inner = stt_result.get("data")
        source = "data"
    elif isinstance(result_obj, dict):
        inner = result_obj
        source = "result"
    elif isinstance(stt_result.get("data"), str):
        inner = stt_result.get("data")
        source = "data(string)"
    else:
        inner = {}

    keys = list(inner.keys()) if isinstance(inner, dict) else "N/A"
    logs.append(f"🔑 parse from={source}, data type={type(inner).__name__}, keys={keys}")

    text = ""
    if isinstance(inner, dict):
        for key in ("transcription", "raw_text", "text", "result", "transcript", "content"):
            val = inner.get(key)
            if val and isinstance(val, str) and val.strip():
                text = val.strip()
                break
    elif isinstance(inner, str) and inner.strip():
        text = inner.strip()

    if not text:
        for key in ("transcription", "text", "result", "transcript"):
            val = stt_result.get(key)
            if val and isinstance(val, str) and val.strip():
                text = val.strip()
                break
    if not text and isinstance(result_obj, dict):
        for key in ("transcription", "raw_text", "text", "transcript", "content"):
            val = result_obj.get(key)
            if val and isinstance(val, str) and val.strip():
                text = val.strip()
                break

    return text, {"logs": logs, "inner": inner, "result_obj": result_obj}


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "blaze_key_set": bool(os.getenv("BLAZE_API_KEY")),
        "openai_key_set": bool(os.getenv("OPENAI_API_KEY")),
    }


@app.post(
    "/api/chat",
    response_model=ChatResponse,
    responses={500: {"model": ErrorResponse}},
)
async def chat(payload: ChatRequest) -> ChatResponse:
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(status_code=500, detail="Missing OPENAI_API_KEY")

    coords_dict = payload.user_coords.model_dump() if payload.user_coords else None
    result = await pipeline.run_async(payload.message, user_coords=coords_dict)
    return ChatResponse(
        reply=result.display_text,
        intent=result.intent_info,
        shops=result.shops,
        logs=result.logs,
    )


@app.post(
    "/api/voice/stt",
    response_model=STTResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def stt(audio_file: UploadFile = File(...)) -> STTResponse:
    audio_bytes = await audio_file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file")

    filename = (audio_file.filename or "audio.wav").lower()
    fmt = filename.rsplit(".", 1)[-1] if "." in filename else "wav"

    logs = []
    logs.append(f"🎛️ Audio meta: filename={audio_file.filename}, bytes={len(audio_bytes)}, format={fmt}")

    stt_result = blaze_stt(audio_bytes, audio_format=fmt)
    logs.append(f"🔬 Blaze raw: {str(stt_result)[:300]}")
    if not isinstance(stt_result, dict):
        stt_result = {}

    blaze_text, meta = extract_blaze_text(stt_result)
    logs.extend(meta["logs"])

    duration = estimate_wav_duration_seconds(audio_bytes) if fmt == "wav" else None
    likely_truncated = bool(blaze_text and duration and duration >= 3.0 and len(blaze_text.split()) <= 1)

    if blaze_text and not likely_truncated:
        logs.append("🎙️ STT engine=Blaze")
        return STTResponse(text=blaze_text, engine="Blaze", logs=logs)

    if likely_truncated:
        logs.append(f"⚠️ Blaze transcript may be truncated (words={len(blaze_text.split())}, seconds={duration})")

    fb = openai_stt(audio_bytes, audio_format=fmt)
    fb_text = fb.get("text") if isinstance(fb, dict) else None
    if fb_text:
        model_name = fb.get("model", "unknown") if isinstance(fb, dict) else "unknown"
        logs.append(f"🛟 Fallback STT engine=OpenAI ({model_name})")
        return STTResponse(text=fb_text, engine=f"OpenAI ({model_name})", logs=logs)

    if blaze_text:
        logs.append("ℹ️ Fallback empty, keep Blaze result")
        return STTResponse(text=blaze_text, engine="Blaze", logs=logs)

    logs.append("⚠️ STT failed with both Blaze and OpenAI fallback")
    raise HTTPException(status_code=500, detail="STT failed")


@app.post(
    "/api/voice/tts",
    responses={200: {"content": {"audio/mpeg": {}}}, 400: {"model": ErrorResponse}},
)
async def tts(payload: TTSRequest):
    audio = blaze_tts(payload.text)
    if not audio:
        audio = openai_tts(payload.text)
    if not audio:
        return JSONResponse(status_code=400, content={"detail": "TTS failed"})
    return Response(content=audio, media_type="audio/mpeg")


def run() -> None:
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=BACKEND_HOST,
        port=BACKEND_PORT,
        reload=BACKEND_RELOAD,
    )


if __name__ == "__main__":
    run()
