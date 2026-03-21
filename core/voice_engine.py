import os
import requests
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("ELEVENLABS_API_KEY")

def elevenlabs_stt(audio_bytes):
    url = "https://api.elevenlabs.io/v1/speech-to-text"
    headers = {"xi-api-key": API_KEY}
    # Năm 2026, ElevenLabs yêu cầu chỉ định model_id rõ ràng cho STT
    files = {
        "file": ("audio.wav", audio_bytes, "audio/wav"),
        "model_id": (None, "scribe_v1") 
    }
    try:
        response = requests.post(url, headers=headers, files=files)
        if response.status_code == 200:
            return response.json().get("text", "")
        else:
            return f"Error STT: {response.text}"
    except Exception as e:
        return f"Lỗi kết nối STT: {str(e)}"

def elevenlabs_tts(text):
    voice_id = os.getenv("ELEVENLABS_VOICE_ID", "pNInz6obpgDQGcFmaJgB")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {"xi-api-key": API_KEY, "Content-Type": "application/json"}
    data = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
    }
    response = requests.post(url, json=data, headers=headers)
    if response.status_code == 200:
        return response.content
    else:
        print(f"TTS Error: {response.text}") # In ra console để debug
        return None