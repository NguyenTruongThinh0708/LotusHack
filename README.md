# LotusHack - SafeWash AI

This project now supports two UI modes:

- Streamlit app (legacy): `app.py`
- React frontend + FastAPI backend (product-oriented structure)

## Architecture

- `core/`: shared AI logic (agents, prompts, evaluator, voice engine, pipeline service)
- `server/`: MCP tools over local store database
- `backend/`: FastAPI API (`/api/chat`, `/api/voice/stt`, `/api/voice/tts`)
- `frontend/`: React + Vite client

## Run Backend (FastAPI)

1. Install Python deps:

```bash
pip install -r requirements.txt
```

2. Start backend:

```bash
python main.py
```

Optional `.env` overrides:

```bash
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000
BACKEND_RELOAD=true
```

Required/optional API keys:

```bash
# Required for Router/Advisor/Tips agents
OPENAI_API_KEY=...

# Optional voice provider (STT/TTS)
BLAZE_API_KEY=...
```

3. Check health:

```bash
http://localhost:8000/health
```

## Run Frontend (React)

1. Install Node deps:

```bash
cd frontend
npm install
```

2. Start UI:

```bash
npm run dev
```

3. Open:

```bash
http://localhost:5173
```

Set `VITE_API_BASE` if your backend is not at `http://localhost:8000`.

## Legacy Streamlit Mode

```bash
streamlit run app.py
```
