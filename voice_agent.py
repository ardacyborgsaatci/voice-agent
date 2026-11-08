"""
Hands-free voice call with an AI agent.

Just talk. The agent detects when you stop, answers out loud, and you can
interrupt it by talking over it. Use headphones so it doesn't hear itself
(or set ALLOW_BARGE_IN=false in .env).

Pipeline: mic -> energy VAD (end-of-speech detection) -> Groq Whisper (speech to text)
          -> Groq LLM (reply) -> Fish Audio TTS (text to speech) -> speakers

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
from openai import OpenAI

load_dotenv()

FISH_API_KEY = os.environ["FISH_API_KEY"]
FISH_VOICE_ID = os.environ["FISH_VOICE_ID"]  # a voice model ID from fish.audio
FISH_TTS_MODEL = os.getenv("FISH_TTS_MODEL", "s2.1-pro-free")
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
GROQ_STT_MODEL = os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo")
ALLOW_BARGE_IN = os.getenv("ALLOW_BARGE_IN", "true").lower() == "true"

SAMPLE_RATE = 16000
FRAME_SAMPLES = 480                      # 30 ms at 16 kHz
FRAME_MS = 30
CALIBRATION_FRAMES = 33                  # ~1 s of silence to learn background noise
SILENCE_END_FRAMES = 800 // FRAME_MS     # 0.8 s of quiet ends your turn
MIN_SPEECH_FRAMES = 120 // FRAME_MS      # 120 ms of sound counts as speech
BARGE_IN_FRAMES = 600 // FRAME_MS        # 600 ms of sound interrupts the agent

SYSTEM_PROMPT = (
    "You are a friendly voice assistant on a phone call. Keep replies short "
    "(1-3 sentences) and plain text with no markdown, since your reply will be spoken aloud."
)

KNOWLEDGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge")


def load_knowledge() -> str:
    """Read every .txt/.md file in the knowledge folder into one block of text."""
    if not os.path.isdir(KNOWLEDGE_DIR):
        return ""
    parts = []
    for name in sorted(os.listdir(KNOWLEDGE_DIR)):
        if name.lower().endswith((".txt", ".md")):
            with open(os.path.join(KNOWLEDGE_DIR, name), encoding="utf-8") as f:
                parts.append(f"## {name}\n{f.read().strip()}")
    return "\n\n".join(parts)


KNOWLEDGE = load_knowledge()
print(f"Loaded knowledge base: {len(KNOWLEDGE.split())} words" if KNOWLEDGE else "No knowledge base files found.")

# Groq exposes an OpenAI-compatible API, so the OpenAI SDK works with a different base URL.
llm = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")
history = []

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
    result = llm.audio.transcriptions.create(
        model=GROQ_STT_MODEL,
        file=("speech.wav", buf.getvalue()),
    )
    return result.text.strip()


def ask_llm(user_text: str) -> str:
    """Send the transcript to Groq's LLM, keeping the conversation history."""
    history.append({"role": "user", "content": user_text})
    system = SYSTEM_PROMPT
    if KNOWLEDGE:
        system += (
            "\n\nUse the knowledge base below to answer questions. If the answer is not "
            "in it, say you don't know rather than guessing.\n\n" + KNOWLEDGE
        )
    resp = llm.chat.completions.create(
        model=GROQ_MODEL,
        max_tokens=300,
        messages=[{"role": "system", "content": system}, *history],
    )
    reply = resp.choices[0].message.content
    history.append({"role": "assistant", "content": reply})
    return reply


def speak(text: str) -> None:
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

                reply = ask_llm(user_text)
                print(f"Agent: {reply}")
                speak(reply)
            except KeyboardInterrupt:
                print("\nCall ended.")
                break
            except Exception as exc:  # keep the call alive on API/network errors
                print(f"Error: {exc}")


if __name__ == "__main__":
    main()
