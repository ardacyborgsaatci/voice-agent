"""
Hands-free voice call with the Dyzania LLP voice line (a fictional firm), in the terminal.

Just talk. The agent detects when you stop, answers out loud, and you can
interrupt it by talking over it. Use headphones so it doesn't hear itself
(or set ALLOW_BARGE_IN=false in .env).

Pipeline: mic -> energy VAD (end-of-speech detection) -> Groq Whisper (speech to text)
          -> router.py (fixed words for urgent / advice / change turns, the booking dialog, or the Groq chat model
             with the firm's knowledge) -> Fish Audio TTS (text to speech) -> speakers

It uses the same router, answer checks and booking dialog as the web version (server.py). The dialog state is kept in
this process instead of being sealed and sent back and forth. Booking works when N8N_BOOKING_BASE and
N8N_BOOKING_SECRET are set; without them a caller who asks to book is sent to the website. Nothing is logged.

Run:
    pip install -r requirements-terminal.txt
    copy .env.example .env      # then fill in your keys
    python voice_agent.py
"""

import io
import os
import queue
import threading
import time

import httpx
import numpy as np
import sounddevice as sd
import soundfile as sf
from dotenv import load_dotenv

load_dotenv()

import booking_flow  # noqa: E402
import llm  # noqa: E402
import router  # noqa: E402
import spoken  # noqa: E402

FISH_API_KEY = os.environ["FISH_API_KEY"]
FISH_VOICE_ID = os.environ["FISH_VOICE_ID"]  # a voice model ID from fish.audio
FISH_TTS_MODEL = os.getenv("FISH_TTS_MODEL", "s2.1-pro-free")
ALLOW_BARGE_IN = os.getenv("ALLOW_BARGE_IN", "true").lower() == "true"
BOOKING_BASE = os.getenv("N8N_BOOKING_BASE", "").strip()
BOOKING_SECRET = os.getenv("N8N_BOOKING_SECRET", "").strip()
BOOKING_HEADER = os.getenv("N8N_BOOKING_HEADER", "X-Booking-Secret").strip()

SAMPLE_RATE = 16000
FRAME_SAMPLES = 480                      # 30 ms at 16 kHz
FRAME_MS = 30
CALIBRATION_FRAMES = 33                  # ~1 s of silence to learn background noise
SILENCE_END_FRAMES = 800 // FRAME_MS     # 0.8 s of quiet ends your turn
MIN_SPEECH_FRAMES = 120 // FRAME_MS      # 120 ms of sound counts as speech
BARGE_IN_FRAMES = 600 // FRAME_MS        # 600 ms of sound interrupts the agent

class NoBooking:
    """Stands in for the booking service when it is not configured: every call fails, so the dialog sends the caller to the website."""

    def slots(self, *args, **kwargs):
        raise booking_flow.BookingError("not configured")

    book = slots


def booking_api():
    if BOOKING_BASE and BOOKING_SECRET:
        return booking_flow.HttpBookingApi(BOOKING_BASE, BOOKING_SECRET, header=BOOKING_HEADER)
    return NoBooking()


history = []     # only turns the chat model answered; booking, urgent and advice turns stay out of it
dialog = None    # the booking dialog's state between turns

frames_q = queue.Queue()        # raw 30 ms mic frames, filled by the audio callback
utterances_q = queue.Queue()    # finished user utterances, filled by the VAD thread
calibrated = threading.Event()
agent_speaking = threading.Event()
barge_in = threading.Event()


def on_audio(indata, _frames, _time, _status):
    frames_q.put(indata[:, 0].copy())


def rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))


def vad_loop() -> None:
    """Split the live mic stream into utterances: start on speech, end after silence."""
    # 1) Learn the background noise level so the threshold fits this room and mic.
    noise = [rms(frames_q.get()) for _ in range(CALIBRATION_FRAMES)]
    threshold = max(float(np.median(noise)) * 3, 300.0)
    calibrated.set()

    buffer = []
    speech_run = 0
    silence_run = 0
    talking = False
    barge_run = 0

    while True:
        frame = frames_q.get()
        is_speech = rms(frame) > threshold

        # 2) While the agent talks, only watch for a sustained interruption.
        if agent_speaking.is_set():
            barge_run = barge_run + 1 if is_speech else 0
            if ALLOW_BARGE_IN and barge_run >= BARGE_IN_FRAMES:
                barge_in.set()
            buffer, speech_run, silence_run, talking = [], 0, 0, False
            continue
        barge_run = 0

        # 3) Normal listening: collect speech, cut the utterance after silence.
        if is_speech:
            speech_run += 1
            silence_run = 0
            buffer.append(frame)
            if speech_run >= MIN_SPEECH_FRAMES:
                talking = True
        elif talking:
            silence_run += 1
            buffer.append(frame)
            if silence_run >= SILENCE_END_FRAMES:
                utterances_q.put(np.concatenate(buffer))
                buffer, speech_run, silence_run, talking = [], 0, 0, False
        else:
            buffer, speech_run = [], 0


def transcribe(audio: np.ndarray) -> str:
    """Speech to text with Groq Whisper."""
    buf = io.BytesIO()
    sf.write(buf, audio, SAMPLE_RATE, format="WAV")
    return llm.transcribe(buf.getvalue(), "speech.wav")


def answer(user_text: str) -> router.Turn:
    """Decide what answers this turn, and keep the model's history and the booking state up to date."""
    global dialog
    turn = router.route(user_text, history, dialog, api=booking_api(), ask_model=llm.ask_model)
    dialog = turn.state
    if not turn.private:
        history.extend([{"role": "user", "content": user_text}, {"role": "assistant", "content": turn.text}])
        del history[:-20]
    return turn


def speak(text: str) -> None:  # text as it should be said: see spoken.speakable
    """Text to speech with Fish Audio TTS, played until done or interrupted."""
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
    data, rate = sf.read(io.BytesIO(resp.content), dtype="float32")

    barge_in.clear()
    agent_speaking.set()
    try:
        sd.play(data, rate)
        end = time.time() + len(data) / rate
        while time.time() < end and not barge_in.is_set():
            time.sleep(0.05)
        if barge_in.is_set():
            print("(interrupted)")
    finally:
        sd.stop()
        agent_speaking.clear()


def main() -> None:
    print("Calibrating: stay quiet for about one second...")
    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        blocksize=FRAME_SAMPLES,
        callback=on_audio,
    ):
        threading.Thread(target=vad_loop, daemon=True).start()
        calibrated.wait()
        print("Call connected. Just start talking. Ctrl+C to hang up.")

        while True:
            try:
                try:
                    audio = utterances_q.get(timeout=0.5)
                except queue.Empty:
                    continue  # keeps Ctrl+C responsive while waiting

                user_text = transcribe(audio)
                if len(user_text) < 2:
                    continue
                print(f"You: {user_text}")

                turn = answer(user_text)
                print(f"Agent: {turn.text}")
                if turn.link:
                    print(f"       ({turn.link.get('label', 'link')}: {turn.link.get('url', '')})")
                speak(spoken.speakable(turn.text, turn.spell))
            except KeyboardInterrupt:
                print("\nCall ended.")
                break
            except Exception as exc:  # keep the call alive on API/network errors
                print(f"Error: {exc}")


if __name__ == "__main__":
    main()
