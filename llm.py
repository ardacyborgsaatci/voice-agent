"""The two Groq calls (speech to text, chat). Kept apart from server.py so the question runner and the tests can use them
without starting a web server. Keys are read when first used, so importing this file needs no keys."""
import os

from openai import OpenAI

GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
GROQ_STT_MODEL = os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo")
MAX_TOKENS = 200

_client: OpenAI | None = None


def client() -> OpenAI:
    """Groq exposes an OpenAI-compatible API, so the OpenAI SDK works with a different base URL."""
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["GROQ_API_KEY"], base_url="https://api.groq.com/openai/v1")
    return _client


def transcribe(data: bytes, filename: str) -> str:
    result = client().audio.transcriptions.create(model=GROQ_STT_MODEL, file=(filename, data))
    return result.text.strip()


def ask_model(messages: list[dict], system: str) -> str:
    resp = client().chat.completions.create(
        model=GROQ_MODEL,
        max_tokens=MAX_TOKENS,
        reasoning_effort="low",  # gpt-oss thinks before answering; keep that short for a voice call
        messages=[{"role": "system", "content": system}, *messages],
    )
    return (resp.choices[0].message.content or "").strip()
