# Voice Agent

A hands-free voice agent you talk to like a phone call. Speech to text and replies come from Groq, the voice comes from Fish Audio, and answers are grounded in the files in `knowledge/`.

- `public/index.html` is the web page (mic, turn detection, playback).
- `server.py` is the FastAPI backend that keeps the API keys private.
- `voice_agent.py` is a terminal version of the same agent.

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env     # then fill in your keys
python -m uvicorn server:app --port 8000
```

Open http://localhost:8000, press **Start call**, and talk.

## Environment variables

| Name | Purpose |
|---|---|
| `FISH_API_KEY` | Fish Audio key (text to speech) |
| `FISH_VOICE_ID` | Fish Audio voice model ID |
| `GROQ_API_KEY` | Groq key (speech to text and replies) |
| `GROQ_MODEL`, `GROQ_STT_MODEL`, `FISH_TTS_MODEL` | Optional model overrides |

## Knowledge base

Every `.txt` or `.md` file in `knowledge/` is loaded at startup and split into sections at each `## ` heading. On every turn only the sections that match the question are sent to the model, which keeps replies fast and stays under Groq's free token-per-minute limit. Use clear `## ` headings and plain words a customer would say. The sample files describe a fictional coffee brand, Marlow & Pine. Replace them with your own content. `test_questions.md` holds sample questions to check the answers.

## Deploy

The repo deploys to Vercel as is: `public/` is served as static files and `server.py` runs as a Python function. Add the environment variables above in the Vercel project settings.

## Terminal version

```bash
pip install -r requirements-terminal.txt
python voice_agent.py
```
