"""
Web version of the voice agent.

The browser records the visitor's voice and sends each turn here. This server
keeps the API keys private, calls Groq (speech to text + reply) and Fish Audio
(text to speech), and returns the reply audio.

Run:
    py -m uvicorn server:app --port 8000
Then open http://localhost:8000
"""

import base64
import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from openai import OpenAI

load_dotenv()

FISH_API_KEY = os.environ["FISH_API_KEY"]
FISH_VOICE_ID = os.environ["FISH_VOICE_ID"]
FISH_TTS_MODEL = os.getenv("FISH_TTS_MODEL", "s2.1-pro-free")
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
GROQ_STT_MODEL = os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo")

BASE_DIR = Path(__file__).parent
MAX_HISTORY = 20  # messages kept per visitor

SYSTEM_PROMPT = (
    "You are a friendly voice assistant on a phone call. Keep replies short "
    "(1-3 sentences) and plain text with no markdown, since your reply will be spoken aloud."
)


def load_knowledge() -> str:
    """Read every .txt/.md file in the knowledge folder into one block of text."""
    folder = BASE_DIR / "knowledge"
    if not folder.is_dir():
        return ""
    parts = []
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() in (".txt", ".md"):
            parts.append(f"## {path.name}\n{path.read_text(encoding='utf-8').strip()}")
    return "\n\n".join(parts)


SYSTEM = SYSTEM_PROMPT
_knowledge = load_knowledge()
if _knowledge:
    SYSTEM += (
        "\n\nUse the knowledge base below to answer questions. If the answer is not "
        "in it, say you don't know rather than guessing.\n\n" + _knowledge
    )

# Groq exposes an OpenAI-compatible API, so the OpenAI SDK works with a different base URL.
llm = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")

app = FastAPI(title="Voice agent")


def transcribe(data: bytes, filename: str) -> str:
    result = llm.audio.transcriptions.create(model=GROQ_STT_MODEL, file=(filename, data))
    return result.text.strip()


def ask_llm(history: list[dict]) -> str:
    resp = llm.chat.completions.create(
        model=GROQ_MODEL,
        max_tokens=300,
        messages=[{"role": "system", "content": SYSTEM}, *history],
    )
    return resp.choices[0].message.content.strip()


def speak(text: str) -> bytes:
    resp = httpx.post(
        "https://api.fish.audio/v1/tts",
        headers={
            "Authorization": f"Bearer {FISH_API_KEY}",
            "Content-Type": "application/json",
            "model": FISH_TTS_MODEL,
        },
        json={"text": text, "reference_id": FISH_VOICE_ID, "format": "wav"},
        timeout=120.0,
    )
    if resp.is_error:
        raise RuntimeError(f"Fish TTS failed ({resp.status_code}): {resp.text}")
    return resp.content


@app.post("/api/turn")
def turn(audio: UploadFile = File(...), history: str = Form("[]")):
    """One conversation turn: visitor audio plus past messages in, reply text and audio out.

    The page sends the conversation so far on every turn, so the server keeps no state
    (serverless functions can't rely on memory between requests).
    """
    data = audio.file.read()
    if not data:
        raise HTTPException(400, "No audio received")

    try:
        past = json.loads(history)
        messages = [
            {"role": m["role"], "content": str(m["content"])[:2000]}
            for m in past
            if m.get("role") in ("user", "assistant")  # never accept a client-supplied system prompt
        ][-MAX_HISTORY:]
    except (ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(400, "Invalid history")

    try:
        user_text = transcribe(data, audio.filename or "speech.webm")
        if len(user_text) < 2:
            return {"user_text": "", "reply": "", "audio": None}

        messages.append({"role": "user", "content": user_text})
        reply = ask_llm(messages)
        wav = speak(reply)
    except Exception as exc:  # report the real error to the page
        raise HTTPException(500, str(exc))

    return {
        "user_text": user_text,
        "reply": reply,
        "audio": base64.b64encode(wav).decode("ascii"),
    }


# Locally, serve the page from public/. On Vercel the platform serves public/ itself
# (mounting it there is not supported), so skip the mount when running on Vercel.
if not os.getenv("VERCEL"):
    app.mount("/", StaticFiles(directory=BASE_DIR / "public", html=True), name="site")
