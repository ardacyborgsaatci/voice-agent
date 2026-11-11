"""
Web version of the voice line for Dyzania LLP (a fictional firm).

The browser records the caller's voice and sends each turn here. This server keeps the API keys private, turns the audio
into text (Groq), decides what answers it (router.py: fixed words for sensitive turns, the booking dialog, or the model),
and returns the reply as audio (Fish Audio).

Run:
    py -m uvicorn server:app --port 8000
Then open http://localhost:8000

Nothing a caller says is logged. Errors are logged by type only.
"""

import json
import logging
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

import booking_flow  # noqa: E402
import knowledge  # noqa: E402
import llm  # noqa: E402
import router  # noqa: E402
import spoken  # noqa: E402
import state as sealed  # noqa: E402

FISH_API_KEY = os.environ["FISH_API_KEY"]
FISH_VOICE_ID = os.environ["FISH_VOICE_ID"]
FISH_TTS_MODEL = os.getenv("FISH_TTS_MODEL", "s2.1-pro-free")
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

# The booking service: the two n8n endpoints booking/slots and booking/book. If these are not set the voice line still
# answers questions, and a caller who asks to book is sent to the website instead.
BOOKING_BASE = os.getenv("N8N_BOOKING_BASE", "").strip()
BOOKING_SECRET = os.getenv("N8N_BOOKING_SECRET", "").strip()
BOOKING_HEADER = os.getenv("N8N_BOOKING_HEADER", "X-Booking-Secret").strip()
STATE_KEY = sealed.key_from(os.getenv("VOICE_STATE_SECRET") or GROQ_API_KEY)

BASE_DIR = Path(__file__).parent
MAX_HISTORY = 20            # messages kept per caller
MAX_AUDIO_BYTES = 3_000_000  # a spoken turn is a few seconds; this stops anyone uploading something large
MAX_STATE_CHARS = 8000
TTS_SAMPLE_RATE = 24000     # raw PCM sample rate streamed to the browser

log = logging.getLogger("voice")


class Unavailable:
    """Stands in for the booking service when it is not configured: every call fails, so the dialog sends the caller to the website."""

    def slots(self, *a, **k):
        raise booking_flow.BookingError("not configured")

    book = slots


def booking_api():
    if BOOKING_BASE and BOOKING_SECRET:
        return booking_flow.HttpBookingApi(BOOKING_BASE, BOOKING_SECRET, header=BOOKING_HEADER)
    return Unavailable()


# One shared client, so the connection to Fish stays open and later turns skip the TLS handshake.
fish = httpx.Client(timeout=120.0)

app = FastAPI(title="Voice agent")


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
        status = resp.status_code
        resp.close()
        raise RuntimeError(f"Fish TTS failed ({status})")
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


def read_history(raw: str) -> list[dict]:
    past = json.loads(raw)
    return [
        {"role": m["role"], "content": str(m["content"])[:2000]}
        for m in past
        if m.get("role") in ("user", "assistant")  # never accept a client-supplied system prompt
    ][-MAX_HISTORY:]


@app.get("/api/health")
def health():
    """Lets you check a deployment is up, that the knowledge files were bundled, and whether booking is switched on."""
    return {"ok": True, "knowledge_sections": len(knowledge.CHUNKS), "booking": bool(BOOKING_BASE and BOOKING_SECRET)}


@app.post("/api/turn")
def turn(audio: UploadFile = File(...), history: str = Form("[]"), state: str = Form("")):
    """One conversation turn: caller audio plus past messages in, reply text and streamed audio out.

    The page sends the conversation so far on every turn, and the booking dialog's sealed state, so the server keeps
    no state (serverless functions can't rely on memory between requests).
    """
    data = audio.file.read(MAX_AUDIO_BYTES + 1)
    if not data:
        raise HTTPException(400, "No audio received")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "That recording is too long. Please say a little less at a time.")

    try:
        messages = read_history(history)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(400, "Invalid history")
    dialog = sealed.unseal(state, STATE_KEY) if state and len(state) <= MAX_STATE_CHARS else None

    try:
        user_text = llm.transcribe(data, audio.filename or "speech.webm")
        if len(user_text) < 2:
            return Response(header_line({"user_text": "", "reply": ""}), media_type="application/octet-stream")
        result = router.route(user_text, messages, dialog, api=booking_api(), ask_model=llm.ask_model)
        tts = open_tts_stream(spoken.speakable(result.text, result.spell))
    except Exception as exc:  # the page gets a plain message; the log gets the kind of error and nothing the caller said
        log.error("turn failed: %s", type(exc).__name__)
        raise HTTPException(500, "The assistant could not answer just now. Please try again.")

    # Response body = one JSON line (texts + sample rate), then raw PCM audio as it is generated.
    header = {
        "user_text": user_text,
        "reply": result.text,
        "sample_rate": TTS_SAMPLE_RATE,
        "private": result.private,   # True: the page keeps this exchange out of the history it sends to the chat model
        "state": sealed.seal(result.state, STATE_KEY) if result.state else "",
    }
    if result.link and str(result.link.get("url", "")).startswith("https://"):
        header["link"] = result.link
    return StreamingResponse(
        audio_stream(header, tts),
        media_type="application/octet-stream",
        headers={"Cache-Control": "no-store"},
    )


# Locally, serve the page from public/. On Vercel the platform serves public/ itself
# (mounting it there is not supported), so skip the mount when running on Vercel.
if not os.getenv("VERCEL"):
    app.mount("/", StaticFiles(directory=BASE_DIR / "public", html=True), name="site")
