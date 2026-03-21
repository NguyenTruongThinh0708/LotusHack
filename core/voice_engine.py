import os
import requests
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("ELEVENLABS_API_KEY")

def elevenlabs_stt(audio_bytes):
    # Endpoint STT của ElevenLabs (Vừa cập nhật 2026)
    url = "https://api.elevenlabs.io/v1/speech-to-text"
    headers = {"xi-api-key": API_KEY}
    files = {"file": ("audio.wav", audio_bytes, "audio/wav")}
    try:
        response = requests.post(url, headers=headers, files=files)
        return response.json().get("text", "")
    except:
        return "Lỗi nhận diện giọng nói."

def elevenlabs_tts(text):
    voice_id = os.getenv("ELEVENLABS_VOICE_ID", "pNInz6obpgDQGcFmaJgB")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {"xi-api-key": API_KEY, "Content-Type": "application/json"}
    data = {"text": text, "model_id": "eleven_multilingual_v2"}
    response = requests.post(url, json=data, headers=headers)
    return response.content