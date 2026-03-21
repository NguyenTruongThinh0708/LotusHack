"""
Voice Engine — Blaze.vn STT & TTS
API docs: https://api.blaze.vn
"""
import os
import io
import requests
from openai import OpenAI


def _get_api_key():
    return os.getenv("BLAZE_API_KEY")


def _get_stt_url():
    return os.getenv("BLAZE_STT_URL", "https://api.blaze.vn/v1/stt/execute")


def _get_tts_url():
    return os.getenv("BLAZE_TTS_URL", "https://api.blaze.vn/v1/tts/execute")


_openai_stt_client = None


def _get_openai_stt_client():
    global _openai_stt_client
    if _openai_stt_client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        _openai_stt_client = OpenAI(api_key=api_key)
    return _openai_stt_client

def blaze_stt(audio_bytes: bytes, audio_format: str = "wav") -> dict | str:
    """Speech-to-Text via Blaze.vn API. Returns full response dict on success."""
    api_key = _get_api_key()
    if not api_key:
        return {"error": "Thiếu BLAZE_API_KEY"}
    headers = {"Authorization": f"Bearer {api_key}"}
    fmt = (audio_format or "wav").lower().strip(".")
    if fmt in ("mp3", "mpeg"):
        content_type = "audio/mpeg"
    elif fmt in ("webm",):
        content_type = "audio/webm"
    elif fmt in ("ogg",):
        content_type = "audio/ogg"
    else:
        fmt = "wav"
        content_type = "audio/wav"
    files = {
        "audio_file": (f"audio.{fmt}", audio_bytes, content_type),
    }
    try:
        response = requests.post(
            _get_stt_url(),
            params={"model": "v1.0"},
            headers=headers,
            files=files,
            timeout=30,
        )
        if response.status_code == 200:
            return response.json()
        else:
            return {"error": f"Lỗi STT ({response.status_code}): {response.text[:200]}"}
    except requests.Timeout:
        return {"error": "Lỗi STT: Quá thời gian chờ"}
    except Exception as e:
        return {"error": f"Lỗi kết nối STT: {str(e)}"}


def openai_stt(audio_bytes: bytes, audio_format: str = "wav") -> dict:
    """Fallback STT via OpenAI when Blaze returns empty/low-confidence text."""
    client = _get_openai_stt_client()
    if client is None:
        return {"error": "Thiếu OPENAI_API_KEY"}

    fmt = (audio_format or "wav").lower().strip(".")
    if fmt not in ("wav", "mp3", "mpeg", "webm", "ogg", "m4a"):
        fmt = "wav"

    primary_model = os.getenv("OPENAI_STT_MODEL", "gpt-4o-mini-transcribe")
    models = [primary_model]
    if primary_model != "whisper-1":
        models.append("whisper-1")

    last_error = None
    for model_name in models:
        try:
            buffer = io.BytesIO(audio_bytes)
            buffer.name = f"audio.{fmt}"
            resp = client.audio.transcriptions.create(
                model=model_name,
                file=buffer,
            )
            text = getattr(resp, "text", None)
            if not text and isinstance(resp, dict):
                text = resp.get("text")
            if text and isinstance(text, str) and text.strip():
                return {"text": text.strip(), "model": model_name}
            last_error = "OpenAI STT returned empty transcription"
        except Exception as e:
            last_error = str(e)
    return {"error": last_error or "OpenAI STT failed"}


def blaze_tts(text: str) -> bytes | None:
    """Text-to-Speech via Blaze.vn API."""
    api_key = _get_api_key()
    if not api_key:
        return None
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {"text": text}
    try:
        response = requests.post(
            _get_tts_url(),
            params={"model": "v1.0"},
            headers=headers,
            json=payload,
            timeout=30,
        )
        if response.status_code == 200:
            content_type = response.headers.get("Content-Type", "")
            if "audio" in content_type:
                return response.content
            # If JSON response with audio URL
            data = response.json()
            audio_url = data.get("audio_url") or data.get("url") or data.get("result")
            if audio_url and isinstance(audio_url, str) and audio_url.startswith("http"):
                audio_resp = requests.get(audio_url, timeout=15)
                if audio_resp.status_code == 200:
                    return audio_resp.content
        return None
    except Exception:
        return None
