"""
Voice Engine — Blaze.vn STT & TTS
API docs: https://api.blaze.vn
"""
import os
import requests


def _get_api_key():
    return os.getenv("BLAZE_API_KEY")


def _get_stt_url():
    return os.getenv("BLAZE_STT_URL", "https://api.blaze.vn/v1/stt/execute")


def _get_tts_url():
    return os.getenv("BLAZE_TTS_URL", "https://api.blaze.vn/v1/tts/execute")

def blaze_stt(audio_bytes: bytes) -> dict | str:
    """Speech-to-Text via Blaze.vn API. Returns full response dict on success."""
    api_key = _get_api_key()
    if not api_key:
        return {"error": "Thiếu BLAZE_API_KEY"}
    headers = {"Authorization": f"Bearer {api_key}"}
    files = {
        "audio_file": ("audio.wav", audio_bytes, "audio/wav"),
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