# Voice line

The phone-style voice assistant for **Dyzania LLP**, a fictional law firm made for a portfolio project. You talk to it like a phone call. It answers questions about the firm, and it can book a first consultation by voice through the same booking service the website form uses.

It is an automated assistant, not a lawyer. It never gives legal advice, never says whether someone has a case, never predicts an outcome, and never states a deadline. Demo use only: do not say real personal or case details to it.

- `public/index.html` is the web page (microphone, turn detection, playback, the booking link when one is offered).
- `server.py` is the FastAPI backend. It keeps the API keys private and handles one spoken turn per request.
- `voice_agent.py` is a terminal version of the same agent.

## How one turn works

The server keeps no memory between requests (it runs as a serverless function), so each turn sends the audio, the earlier chat messages, and a sealed copy of the booking dialog's state.

```
audio -> Groq Whisper (speech to text) -> router.py -> Fish Audio (text to speech) -> audio
```

`router.py` decides what answers, most careful first:

1. **Time-sensitive wording** (court date, served papers, deadline, lockout, arrest, safety): a fixed reply that points to the office and the urgent email. Even in the middle of a booking, which is paused and not saved.
2. **A booking already in progress**: the booking dialog (`booking_flow.py`).
3. **Change or cancel a booking**: a fixed reply (the voice line cannot do that).
4. **Wants to book**: starts the booking dialog.
5. **Asks for legal advice**, or **asks for a person**: a fixed reply.
6. **Everything else**: the Groq chat model, with only the matching knowledge sections, and then an answer check (`guards.py`) that blocks a reply containing advice, a case opinion, an outcome promise, a deadline stated as fact, a promised response time, or a fee, email, link or phone number that is not in the knowledge it was given.

Only turns the model answered go into the history the model sees. Booking, urgent and advice turns are marked `private` and the page keeps them out of the history, so a name, phone number or email said during booking is never sent to the chat model.

### The booking dialog

Practice area, then three times to choose from (spread out, not back to back), name, phone (can be skipped), email read back letter by letter (two failed tries and the caller is sent to the website), whether there is an other side to check for conflicts, a spoken summary and the consent sentence, then the booking. A caller can say "stop" or "repeat" at any point, and can correct any answer. If the booking service is down, or two answers in a row cannot be understood, the caller is given the website booking page and nothing is saved.

The dialog runs the same rules as the website (4 hours notice, 30 day horizon, one active booking per email and phone, conflict check) because it calls the same two n8n endpoints, `booking/slots` and `booking/book`. See `../automations/booking/README.md`.

### Sealed state

The dialog state travels through the page, so it is sealed: HMAC-SHA256 signed, 30 minutes maximum age, and a state the server did not make is ignored (the turn is handled as a first turn). The key is `VOICE_STATE_SECRET`, or one derived from `GROQ_API_KEY` if that is not set. Set your own in production.

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
| `N8N_BOOKING_BASE` | Base URL of the n8n booking webhooks, for example `https://your-n8n/webhook` (the server adds `/booking/slots` and `/booking/book`). Leave empty to turn booking off: a caller who asks to book is sent to the website. |
| `N8N_BOOKING_SECRET` | The header-auth secret the two booking webhooks expect |
| `N8N_BOOKING_HEADER` | Header name for that secret (default `X-Booking-Secret`) |
| `VOICE_STATE_SECRET` | Signing key for the sealed dialog state |
| `GROQ_MODEL`, `GROQ_STT_MODEL`, `FISH_TTS_MODEL` | Optional model overrides |

`/api/health` reports whether the knowledge was bundled and whether booking is switched on.

## Knowledge base and firm data

Every `.md` file in `knowledge/` is split into sections at each `## ` heading. On every turn only the sections that match the question are sent to the model, which keeps replies fast and under Groq's free token-per-minute limit. These files, and the firm data in `firm/` (practice areas, urgency wording, availability), are **copies** of the shared `../knowledge/` and `../firm/` folders. Edit them there, then run:

```bash
node ../scripts/sync-knowledge.mjs          # copy
node ../scripts/sync-knowledge.mjs --check  # fail if the copies are out of date
```

`firmdata.py` is the Python mirror of the website's matching (`firm/lib/text.js`, `areas.js`, `urgency.js`). `tests/parity.js` runs the real JavaScript and the Python on the same sentences, so the two cannot drift apart without a test failing.

## Tests

```bash
python -m unittest discover -s tests
```

124 tests, no keys and no network. The booking tests run the **real** `booking.js` rules through a small Node bridge (`tests/bridge.js`), including a double booking race, a repeat booking, a stale slot, a service outage and a failed confirmation email. Needs Node on the path.

## The question set

`questions.json` holds 54 questions: a **dev** set (39) used while tuning, and a **holdout** set (15) that was not used to tune anything. Each has what a good answer must contain and must not contain, and an ideal answer.

```bash
python run_questions.py               # all questions, live, needs GROQ_API_KEY
python run_questions.py --set holdout
```

It writes every answer to `results/<date>-live.json`. See `RESULTS.md` for what has and has not been measured, and `MANUAL-CALL.md` for the by-hand call script that covers what text tests cannot (microphone, speech recognition, the voice, interruptions, latency).

## Deploy

Deployed to Vercel as is: `public/` is served as static files and `server.py` runs as a Python function (`vercel.json` sets a 60 second limit). Add the environment variables above in the Vercel project settings.

Before this is public, decide on access. The voice page has no login, and each turn costs a speech-to-text call and a voice call. Use Vercel Authentication, or a Firewall rate limit rule, or both. A stateless function cannot rate limit itself.

## Terminal version

```bash
pip install -r requirements-terminal.txt
python voice_agent.py
```

Same router, answer checks and booking dialog; the dialog state is kept in memory instead of being sealed. Use headphones, or set `ALLOW_BARGE_IN=false`.

## Limits, honestly

- Same-author question sets. The questions and the knowledge were written by the same person, so scores are friendlier than real callers would give.
- The chat model's answers have not been measured yet. Retrieval and the fixed paths are tested offline; the model run (`run_questions.py`) needs a Groq key and is still owed.
- Speech recognition and the voice are only tested by a real call (`MANUAL-CALL.md`).
- English only, and the demo line does not verify who is calling.
- Nothing a caller says is logged. Errors are logged by type only.
