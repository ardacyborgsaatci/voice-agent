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


STT_PROVIDER = os.getenv("STT_PROVIDER", "groq")  # "groq" (default), "openrouter" or "fish"
OPENROUTER_STT_MODEL = os.getenv("OPENROUTER_STT_MODEL", "openai/whisper-large-v3")
FISH_STT_MODEL = os.getenv("FISH_STT_MODEL", "transcribe-1-pro")


def transcribe(data: bytes, filename: str) -> str:
    if STT_PROVIDER == "openrouter":
        return _transcribe_openrouter(data, filename)
    if STT_PROVIDER == "fish":
        return _transcribe_fish(data, filename)
    result = client().audio.transcriptions.create(model=GROQ_STT_MODEL, file=(filename, data))
    return result.text.strip()


def _transcribe_fish(data: bytes, filename: str) -> str:
    """Fish Audio's speech-to-text: the model goes in a header, the answer is JSON with the transcript in `text`."""
    import httpx

    resp = httpx.post(
        "https://api.fish.audio/v1/asr",
        headers={"Authorization": f"Bearer {os.environ['FISH_API_KEY']}", "model": FISH_STT_MODEL},
        data={"ignore_timestamps": "true", "tag_audio_events": "false", "diarize": "false"},
        files={"audio": (filename, data)},
        timeout=60,
    )
    resp.raise_for_status()
    return str(resp.json().get("text", "")).strip()


def _transcribe_openrouter(data: bytes, filename: str) -> str:
    """OpenRouter's speech-to-text endpoint: multipart upload, JSON answer with the transcript in `text`."""
    import httpx

    resp = httpx.post(
        "https://openrouter.ai/api/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
        data={"model": OPENROUTER_STT_MODEL},
        files={"file": (filename, data)},
        timeout=60,
    )
    resp.raise_for_status()
    return str(resp.json().get("text", "")).strip()


def ask_model(messages: list[dict], system: str) -> str:
    resp = client().chat.completions.create(
        model=GROQ_MODEL,
        max_tokens=MAX_TOKENS,
        reasoning_effort="low",  # gpt-oss thinks before answering; keep that short for a voice call
        messages=[{"role": "system", "content": system}, *messages],
    )
    return (resp.choices[0].message.content or "").strip()
