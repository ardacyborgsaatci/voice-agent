"""
Web version of the voice agent.

The browser records the visitor's voice and sends each turn here. This server
keeps the API keys private, calls Groq (speech to text + reply) and Fish Audio
(text to speech), and returns the reply audio.

Run:
    py -m uvicorn server:app --port 8000
Then open http://localhost:8000
"""

import json
import os
import re
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
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
TTS_SAMPLE_RATE = 24000  # raw PCM sample rate streamed to the browser

SYSTEM_PROMPT = (
    "You are a friendly voice assistant on a phone call. Keep replies to one or two short "
    "sentences and plain text with no markdown, since your reply will be spoken aloud."
)


STOPWORDS = {
    "the", "and", "you", "your", "are", "was", "for", "with", "what", "when", "where", "how", "who",
    "does", "do", "can", "could", "would", "have", "has", "this", "that", "there", "any", "about",
    "much", "many", "get", "got", "tell", "please", "give", "its", "our", "from", "they", "them",
}


def stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower().replace("-", ""))  # so "Wi-Fi" matches "wifi"
    return {stem(w) for w in words if len(w) > 2 and w not in STOPWORDS}


def load_knowledge() -> list[dict]:
    """Split every .txt/.md file in the knowledge folder into sections, one per '## ' heading.

    Sending the whole knowledge base on every turn burns the model's tokens-per-minute budget,
    which causes rate-limit waits. Instead each turn only gets the sections that match the question.
    """
    folder = BASE_DIR / "knowledge"
    chunks = []
    if not folder.is_dir():
        return chunks
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() not in (".txt", ".md"):
            continue
        text = path.read_text(encoding="utf-8").strip()
        title = text.splitlines()[0].lstrip("# ").strip() if text else path.stem
        for part in re.split(r"\n(?=## )", text):
            part = part.strip()
            if not part:
                continue
            heading = part.splitlines()[0].lstrip("# ").strip()
            chunks.append(
                {
                    "text": part,
                    "heading": heading,
                    "title": title,
                    "body_tokens": tokens(part),
                    "head_tokens": tokens(heading + " " + title),
                }
            )
    return chunks


CHUNKS = load_knowledge()
TOP_K = 4


def relevant_knowledge(question: str) -> str:
    """Pick the sections that best match the question (headings count extra)."""
    q = tokens(question)
    scored = []
    for index, chunk in enumerate(CHUNKS):
        score = len(q & chunk["body_tokens"]) + 2 * len(q & chunk["head_tokens"])
        if score:
            scored.append((score, index))
    ranked = sorted(scored, key=lambda s: (-s[0], s[1]))
    cutoff = ranked[0][0] * 0.25 if ranked else 0  # drop very weak matches to save tokens
    picked = [i for score, i in ranked[:TOP_K] if score >= cutoff]
    if not picked:  # nothing matched: fall back to the general intro sections
        picked = list(range(min(2, len(CHUNKS))))
    return "\n\n".join(CHUNKS[i]["text"] for i in sorted(picked))


def build_system(history: list[dict]) -> str:
    """System prompt plus only the knowledge relevant to the last two things the visitor said."""
    if not CHUNKS:
        return SYSTEM_PROMPT
    recent = " ".join(m["content"] for m in history if m["role"] == "user")[-600:]
    topics = "; ".join(sorted({c["heading"] for c in CHUNKS}))
    return (
        SYSTEM_PROMPT
        + "\n\nAnswer from the knowledge below. If the answer is not in it, say you don't know "
        "rather than guessing. Topics the knowledge base covers: " + topics + ".\n\n"
        + relevant_knowledge(recent)
    )


# Groq exposes an OpenAI-compatible API, so the OpenAI SDK works with a different base URL.
llm = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")

# One shared client, so the connection to Fish stays open and later turns skip the TLS handshake.
fish = httpx.Client(timeout=120.0)

app = FastAPI(title="Voice agent")


def transcribe(data: bytes, filename: str) -> str:
    result = llm.audio.transcriptions.create(model=GROQ_STT_MODEL, file=(filename, data))
    return result.text.strip()


def ask_llm(history: list[dict]) -> str:
    resp = llm.chat.completions.create(
        model=GROQ_MODEL,
        max_tokens=200,
        reasoning_effort="low",  # gpt-oss thinks before answering; keep that short for a voice call
        messages=[{"role": "system", "content": build_system(history)}, *history],
    )
    return resp.choices[0].message.content.strip()


def open_tts_stream(text: str) -> httpx.Response:
    """Start a Fish TTS request and return as soon as the audio begins to arrive.

    Fish generates speech faster than it plays, so the browser can start playing the
    first chunk while the rest is still being generated.
    """
    request = fish.build_request(
        "POST",
        "https://api.fish.audio/v1/tts",
        headers={
            "Authorization": f"Bearer {FISH_API_KEY}",
            "Content-Type": "application/json",
            "model": FISH_TTS_MODEL,
        },
        json={
            "text": text,
            "reference_id": FISH_VOICE_ID,
            "format": "pcm",  # raw 16-bit mono samples, easy to play chunk by chunk
            "sample_rate": TTS_SAMPLE_RATE,
        },
    )
    resp = fish.send(request, stream=True)
    if resp.is_error:
        detail = resp.read().decode("utf-8", errors="replace")
        resp.close()
        raise RuntimeError(f"Fish TTS failed ({resp.status_code}): {detail}")
    return resp


def header_line(header: dict) -> bytes:
    """First line of the response: JSON with the texts, ending in a newline."""
    return (json.dumps(header) + "\n").encode("utf-8")


def audio_stream(header: dict, resp: httpx.Response):
    try:
        yield header_line(header)
        for chunk in resp.iter_bytes():
            yield chunk
    finally:
        resp.close()


@app.get("/api/health")
def health():
    """Lets you check a deployment is up and that the knowledge files were bundled with it."""
    return {"ok": True, "knowledge_sections": len(CHUNKS)}


@app.post("/api/turn")
def turn(audio: UploadFile = File(...), history: str = Form("[]")):
    """One conversation turn: visitor audio plus past messages in, reply text and streamed audio out.

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
            return Response(header_line({"user_text": "", "reply": ""}), media_type="application/octet-stream")

        messages.append({"role": "user", "content": user_text})
        reply = ask_llm(messages)
        tts = open_tts_stream(reply)
    except Exception as exc:  # report the real error to the page
        raise HTTPException(500, str(exc))

    # Response body = one JSON line (texts + sample rate), then raw PCM audio as it is generated.
    header = {"user_text": user_text, "reply": reply, "sample_rate": TTS_SAMPLE_RATE}
    return StreamingResponse(
        audio_stream(header, tts),
        media_type="application/octet-stream",
        headers={"Cache-Control": "no-store"},
    )


# Locally, serve the page from public/. On Vercel the platform serves public/ itself
# (mounting it there is not supported), so skip the mount when running on Vercel.
if not os.getenv("VERCEL"):
    app.mount("/", StaticFiles(directory=BASE_DIR / "public", html=True), name="site")
