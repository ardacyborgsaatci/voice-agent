"""Seals the booking dialog's state so it can ride in the browser between turns.

The server keeps no memory (it may run on a different instance each turn), so the dialog's state goes back and forth
with the page. Signing it means a page cannot hand the server a state it never produced (for example one that skips
the email read-back). It is not encryption: the page can read its own state, which holds only what the caller said.
A state older than MAX_AGE is ignored, so an abandoned call cannot be resumed hours later.
"""
import base64
import hashlib
import hmac
import json
import time

MAX_AGE = 30 * 60


def key_from(secret: str) -> bytes:
    return hashlib.sha256(("voice-state:" + secret).encode("utf-8")).digest()


def seal(state: dict, key: bytes, now: float | None = None) -> str:
    body = json.dumps({"s": state, "t": int(now if now is not None else time.time())}, separators=(",", ":"), sort_keys=True)
    payload = base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii").rstrip("=")
    sig = hmac.new(key, payload.encode("ascii"), hashlib.sha256).hexdigest()[:32]
    return payload + "." + sig


def unseal(token, key: bytes, now: float | None = None) -> dict | None:
    """The state, or None for anything that is missing, altered, malformed or too old."""
    if not isinstance(token, str) or token.count(".") != 1:
        return None
    payload, sig = token.split(".")
    want = hmac.new(key, payload.encode("ascii", "ignore"), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, want):
        return None
    try:
        body = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode("utf-8"))
        state, issued = body["s"], int(body["t"])
    except (ValueError, KeyError, TypeError):
        return None
    current = now if now is not None else time.time()
    if not isinstance(state, dict) or current - issued > MAX_AGE or issued > current + 60:
        return None
    return state
