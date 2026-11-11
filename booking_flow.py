"""Booking a first consultation on the voice line.

This is a plain question-and-answer dialog written as rules, not something a model improvises. A model is good at
conversation and bad at being exact; booking has to be exact (a person's name, phone and email, one time slot, a conflict
check). So the words the caller hears are fixed, every value is read back, and the only things that can write a booking
are the booking rules in n8n (`booking/slots` and `booking/book`), which do their own checks again.

The dialog keeps no memory of its own: its whole state travels with each turn (sealed by the server, see state.py), so
it works on a serverless host. The state holds what the caller said for this booking; the model never sees it.

Steps:  area -> times -> name -> phone -> email -> email_check -> other -> confirm -> (booked)
        any step can go to `fix` after a "no" at the end, and any step ends in `fallback` after two failures.
"""
import copy
import re
from dataclasses import dataclass, field

import firmdata
import spoken
from firmdata import FIRM

PHONE = FIRM["phone"]
HOURS = firmdata.office_hours_text()
MAX_TRIES = 2          # failed attempts at one question before the caller is sent to the website
LIST_LIMIT = 3         # times read out at once (a phone call cannot hold more)
POOL = 40              # times asked for, so three well-spread ones can be picked (the list is in 15-minute steps)
SPREAD_MINUTES = 120   # offered times are at least this far apart where there is a choice
ORDINALS = ["first", "second", "third"]


class BookingError(Exception):
    """The booking service could not be reached or answered something unreadable."""


@dataclass
class Reply:
    text: str                       # what the page shows
    state: dict | None              # the dialog's state for the next turn; None when the dialog is over
    spell: list = field(default_factory=list)   # emails, numbers and references to read one character at a time
    link: dict | None = None        # {"label", "url"} the page may show
    ended: bool = False


# ---------------------------------------------------------------- the booking service

class HttpBookingApi:
    """Calls the two n8n endpoints from the server, with the shared secret. The browser never sees either."""

    def __init__(self, base_url: str, secret: str, header: str = "X-Booking-Secret", client=None, timeout: float = 15.0):
        self.base = base_url.rstrip("/")
        self.secret = secret
        self.header = header
        self.timeout = timeout
        self.client = client

    def _post(self, path: str, body: dict) -> dict:
        import httpx

        client = self.client or httpx
        try:
            r = client.post(f"{self.base}/{path}", json=body, headers={self.header: self.secret}, timeout=self.timeout)
            if r.status_code != 200:
                raise BookingError(f"booking service answered {r.status_code}")
            data = r.json()
        except BookingError:
            raise
        except Exception as exc:  # network, timeout, bad JSON: report the kind only, never the content
            raise BookingError(type(exc).__name__) from None
        if not isinstance(data, dict):
            raise BookingError("unreadable answer")
        return data

    def slots(self, area: str, after: str | None = None, limit: int = POOL) -> dict:
        body = {"area": area, "limit": limit}
        if after:
            body["after"] = after
        return self._post("booking/slots", body)

    def book(self, payload: dict) -> dict:
        return self._post("booking/book", payload)


# ---------------------------------------------------------------- words the caller hears

def _zone(name: str | None) -> str:
    n = re.sub(r"\s+(?:Standard|Daylight)\s+Time$", " time", str(name or "").strip())
    return n or "the firm's local time"


def _offer_line(state: dict) -> str:
    slots = state["slots"]
    zone = _zone(state.get("tzName"))
    head = f"The first consultation for {state['areaName']} is {state['minutes']} minutes. {state['fee']} "
    if len(slots) == 1:
        s = slots[0]
        return head + f"The only time I can see is {spoken.say_when(s['date'], s['time'])}, {zone}. Would you like it? Say yes, or say later for other times."
    listed = "; ".join(f"{ORDINALS[i]}, {spoken.say_when(s['date'], s['time'])}" for i, s in enumerate(slots))
    names = ", ".join(ORDINALS[: len(slots) - 1]) + f" or {ORDINALS[len(slots) - 1]}"
    return head + f"The next available times are: {listed}. All times are {zone}. Which would you like? Say {names}, or say later for other times."


def _summary(state: dict) -> tuple[str, list]:
    s = state["slot"]
    phone = state.get("phone") or ""
    parts = [
        f"Let me confirm. A {state['areaName']} consultation, {spoken.say_when(s['date'], s['time'])}, about {state['minutes']} minutes, "
        "at the office or by video.",
        f"Name: {state['name']}.",
        f"Phone: {phone}." if phone else "No phone number.",
        f"Email: {state['email']}.",
        "By saying yes you agree that the firm may keep these details to arrange this consultation and contact you about it. "
        "I'm an automated assistant, not a lawyer, and booking creates no attorney-client relationship.",
        "Shall I book it? Say yes or no.",
    ]
    return " ".join(parts), [x for x in (phone, state["email"]) if x]


def prompt(state: dict) -> Reply:
    """The question for the current step. Also used to repeat it when the caller asks."""
    step = state["step"]
    spell: list = []
    if step == "area":
        text = "Which kind of matter is it: family law, personal injury, employment, or estate planning and probate? Please just give the type, not the details."
    elif step == "times":
        text = _offer_line(state)
    elif step == "name":
        text = "What is your full name?"
    elif step == "phone":
        text = "What is the best phone number to reach you? You can also say skip."
    elif step == "email":
        text = "What is your email address? Say it slowly, for example jane dot doe at example dot com, or spell it letter by letter."
    elif step == "email_check":
        text = f"I heard {state['email_candidate']}. Is that right? Say yes or no."
        spell = [state["email_candidate"]]
    elif step == "other":
        text = ("Is there another person or organization on the other side, for example a spouse, an employer or an insurer? "
                "Just give a name, or say none. Please don't describe what happened.")
    elif step == "confirm":
        text, spell = _summary(state)
    elif step == "fix":
        text = "What would you like to change: the time, your name, your phone number, your email, or the other side? Or say stop."
    else:
        text = "Let's start again. " + prompt({"step": "area"}).text
    return Reply(text, state, spell)


def fallback(why: str = "") -> Reply:
    text = (
        "I'm sorry, I can't finish that booking by voice. Nothing has been saved. "
        f"You can book in about a minute on our website at {FIRM['domain']}/book, or call the office at {PHONE} during office hours, {HOURS}."
    )
    return Reply(text, None, link={"label": "Open the booking page", "url": firmdata.BOOK_URL}, ended=True)


def _end(text: str, spell=None, link=None) -> Reply:
    return Reply(text, None, list(spell or []), link, ended=True)


def stopped() -> Reply:
    return _end("No problem, I've stopped. Nothing was saved. Ask me anything else, or say book to start again.")


# ---------------------------------------------------------------- starting and stepping

def start(text: str, api) -> Reply:
    """Called when the caller asks to book. A matter type said in the same sentence is used."""
    intro = "Happy to help you book a first consultation. I'm an automated assistant, not a lawyer. You can say stop at any time. "
    state = {"step": "area", "tries": 0, "email_tries": 0, "invalid": 0}
    c = firmdata.choose_area(text)
    if c["kind"] in ("area", "out"):
        r = _after_area(state, c, api)
        if not r.ended:  # a booking that ends at once (a matter the firm does not take, the service down) needs no welcome
            r.text = intro + r.text
        return r
    r = prompt(state)
    r.text = intro + r.text
    return r


def _out_of_scope(out: dict) -> Reply:
    return _end(
        f"I'm sorry, the firm doesn't handle {out['name'].lower()} matters. For help, try {out['referral']}. "
        "If a time limit may be running, please contact another lawyer quickly."
    )


def _slots_for(state: dict, api, after: str | None = None) -> dict | None:
    """Fetch times for the state's area. Returns the answer, or None when the service is unreachable."""
    try:
        return api.slots(state["area"], after=after, limit=POOL)
    except BookingError:
        return None


def _minutes(start: str) -> int:
    from datetime import datetime

    return int(datetime.fromisoformat(start.replace("Z", "+00:00")).timestamp() // 60)


def spread(slots: list[dict]) -> list[dict]:
    """Up to LIST_LIMIT times, earliest first, each at least SPREAD_MINUTES after the one before it where the list allows.
    The service lists every 15 minutes, and 'ten, ten fifteen, ten thirty' is not a choice anyone wants read to them."""
    picked: list[dict] = []
    for s in slots:
        if len(picked) == LIST_LIMIT:
            break
        if not picked or _minutes(s["start"]) - _minutes(picked[-1]["start"]) >= SPREAD_MINUTES:
            picked.append(s)
    for s in slots:  # a short list: fill up with what is left, in order
        if len(picked) == LIST_LIMIT:
            break
        if s not in picked:
            picked.append(s)
    return sorted(picked, key=lambda s: s["start"])


def _load_times(state: dict, r: dict) -> None:
    clean = [{"start": s["start"], "date": s["date"], "time": s["time"]} for s in r.get("slots", [])]
    state["slots"] = spread(clean)
    state["more"] = bool(r.get("more"))
    state["minutes"] = r.get("consultMinutes") or state.get("minutes") or 30
    state["fee"] = r.get("fee") or state.get("fee") or ""
    state["tzName"] = r.get("timezoneName") or state.get("tzName") or ""
    state["areaName"] = r.get("areaName") or state.get("areaName") or state["area"]
    state["step"] = "times"
    state["tries"] = 0


def _after_area(state: dict, choice: dict, api) -> Reply:
    if choice["kind"] == "out":
        return _out_of_scope(choice["out"])
    state["area"] = choice["area"]
    state["areaName"] = firmdata.area_by_id(choice["area"])["name"]
    r = _slots_for(state, api)
    if r is None or not r.get("ok"):
        return fallback("unreachable")
    if not r.get("slots"):
        return _end(
            "I don't see any times I can offer online for that right now. "
            f"Please call the office at {PHONE} during office hours, {HOURS}, or try the booking page at {FIRM['domain']}/book.",
            link={"label": "Open the booking page", "url": firmdata.BOOK_URL},
        )
    _load_times(state, r)
    return prompt(state)


def _retry(state: dict, said: str) -> Reply:
    """A step failed to get a usable answer. Ask again, or give up after MAX_TRIES."""
    state["tries"] = state.get("tries", 0) + 1
    if state["tries"] >= MAX_TRIES:
        return fallback("too many tries")
    r = prompt(state)
    r.text = said + " " + r.text
    return r


_SKIP_PHONE = re.compile(r"\b(skip|no phone|rather not|prefer not|dont have|do not have|just email|no number|pass)\b")


def handle(state: dict, text: str, api) -> Reply:
    """One caller turn inside the dialog. The state passed in is never changed; the reply carries the new one."""
    state = copy.deepcopy(state)
    if spoken.wants_stop(text):
        return stopped()
    if spoken.wants_repeat(text):
        return prompt(state)
    step = state["step"]

    if step == "area":
        c = firmdata.choose_area(text)
        if c["kind"] in ("area", "out"):
            return _after_area(state, c, api)
        if c["kind"] == "ambiguous":
            return _retry(state, "I heard more than one kind of matter.")
        return _retry(state, "Sorry, I didn't catch the type of matter.")

    if step == "times":
        slots = state["slots"]
        choice = spoken.parse_choice(text, slots)
        if choice is None and len(slots) == 1 and spoken.parse_yes_no(text) == "yes":
            choice = ("pick", 0)
        if choice and choice[0] == "pick":
            state["slot"] = slots[choice[1]]
            state["tries"] = 0
            if state.get("fixing"):
                state["step"] = "confirm"
                return prompt(state)
            state["step"] = "name"
            r = prompt(state)
            r.text = f"Good, {spoken.say_when(state['slot']['date'], state['slot']['time'])}. " + r.text
            return r
        if choice and choice[0] == "more":
            r = _slots_for(state, api, after=slots[-1]["start"])
            if r is None or not r.get("ok"):
                return fallback("unreachable")
            if not r.get("slots"):
                reply = prompt(state)
                reply.text = "I don't see any later times online. " + reply.text
                return reply
            _load_times(state, r)
            return prompt(state)
        return _retry(state, "Sorry, I didn't catch which time.")

    if step == "name":
        name = spoken.parse_name(text)
        if not name:
            return _retry(state, "Sorry, I need your first and last name.")
        state["name"] = name
        if state.get("fixing"):
            state["step"], state["tries"] = "confirm", 0
            return prompt(state)
        state["step"], state["tries"] = "phone", 0
        r = prompt(state)
        r.text = f"Thanks, {name.split()[0]}. " + r.text
        return r

    if step == "phone":
        if _SKIP_PHONE.search(text.lower()) and not spoken.parse_phone(text):
            state["phone"] = ""
        else:
            phone = spoken.parse_phone(text)
            if not phone:
                return _retry(state, "Sorry, I didn't catch a phone number. Say the digits, or say skip.")
            state["phone"] = phone
        if state.get("fixing"):
            state["step"], state["tries"] = "confirm", 0
            return prompt(state)
        state["step"], state["tries"] = "email", 0
        return prompt(state)

    if step == "email":
        email = spoken.parse_email(text)
        if not email:
            state["email_tries"] = state.get("email_tries", 0) + 1
            if state["email_tries"] >= MAX_TRIES:
                return fallback("email")
            r = prompt(state)
            r.text = "Sorry, I couldn't catch an email address. " + r.text
            return r
        state["email_candidate"] = email
        state["step"], state["tries"] = "email_check", 0
        return prompt(state)

    if step == "email_check":
        answer = spoken.parse_yes_no(text)
        if answer == "yes":
            state["email"] = state.pop("email_candidate")
            state["email_tries"] = 0
            if state.get("fixing"):
                state["step"], state["tries"] = "confirm", 0
                return prompt(state)
            state["step"], state["tries"] = "other", 0
            return prompt(state)
        if answer == "no":
            state["email_tries"] = state.get("email_tries", 0) + 1
            if state["email_tries"] >= MAX_TRIES:
                return fallback("email")
            state["step"] = "email"
            r = prompt(state)
            r.text = "Sorry about that. Let's try once more. " + r.text
            return r
        state["tries"] = state.get("tries", 0) + 1
        if state["tries"] >= MAX_TRIES:
            return fallback("email")
        r = prompt(state)
        r.text = "Sorry, was that a yes or a no? " + r.text
        return r

    if step == "other":
        side = spoken.parse_other_side(text)
        if not side:
            return _retry(state, "Just a name is enough, please.")
        state["other"] = "" if side.get("none") else side["name"]
        state["no_other"] = bool(side.get("none"))
        state["step"], state["tries"] = "confirm", 0
        return prompt(state)

    if step == "confirm":
        answer = spoken.parse_yes_no(text)
        if answer == "yes":
            return _book(state, api)
        if answer == "no":
            state["step"], state["tries"], state["fixing"] = "fix", 0, True
            return prompt(state)
        return _retry(state, "Sorry, was that a yes or a no?")

    if step == "fix":
        low = text.lower()
        target = None
        if re.search(r"\b(time|date|day|slot|appointment)\b", low):
            target = "times"
        elif re.search(r"\b(name)\b", low):
            target = "name"
        elif re.search(r"\b(phone|number|digits)\b", low):
            target = "phone"
        elif re.search(r"\b(email|e-mail|address)\b", low):
            target = "email"
        elif re.search(r"\b(other|side|party|opposing)\b", low):
            target = "other"
        if not target:
            return _retry(state, "Sorry, I didn't catch what to change.")
        state["tries"] = 0
        if target == "times":
            r = _slots_for(state, api)
            if r is None or not r.get("ok") or not r.get("slots"):
                return fallback("unreachable")
            _load_times(state, r)
            return prompt(state)
        state["step"] = target
        if target == "email":
            state["email_tries"] = 0
        return prompt(state)

    return fallback("unknown step")


# ---------------------------------------------------------------- making the booking

def _book(state: dict, api) -> Reply:
    payload = {
        "name": state["name"],
        "email": state["email"],
        "phone": state.get("phone", ""),
        "area": state["area"],
        "otherSide": state.get("other", ""),
        "noOtherSide": bool(state.get("no_other")),
        "consent": True,   # given by the caller's yes to the sentence read out in the summary
        "channel": "voice",
        "start": state["slot"]["start"],
        "website": "",
    }
    try:
        r = api.book(payload)
    except BookingError:
        return fallback("unreachable")
    code = r.get("code")

    if r.get("ok") and code == "booked":
        sent = r.get("confirmationSent", True)
        ref = str(r.get("ref") or "")
        when = spoken.say_when(state["slot"]["date"], state["slot"]["time"])
        first = f"Your first consultation is booked for {when}"
        if r.get("attorney"):
            first += f", with {r['attorney']}"
        parts = [first + f", at {r.get('address') or FIRM['address']}."]
        if sent:
            parts.append(f"A confirmation email is on its way to {state['email']}.")
        else:
            parts.append("I couldn't send the confirmation email, so please note your reference.")
        if ref:
            parts.append(f"Your reference is {ref}.")
        parts.append("Remember, I'm an automated assistant, not a lawyer, and booking creates no attorney-client relationship. Is there anything else I can help with?")
        spell = ([state["email"]] if sent else []) + ([ref] if ref else [])
        return _end(" ".join(parts), spell=spell)

    if code == "slot_unavailable":
        alts = r.get("alternatives") or []
        if not alts:
            return fallback("no alternatives")
        state["invalid"] = state.get("invalid", 0) + 1
        if state["invalid"] > MAX_TRIES:
            return fallback("busy")
        state["slots"] = [{"start": s["start"], "date": s["date"], "time": s["time"]} for s in alts[:LIST_LIMIT]]
        state["step"], state["tries"], state["fixing"] = "times", 0, True
        reply = prompt(state)
        reply.text = "I'm sorry, that time has just gone. " + reply.text
        return reply

    if code == "contact_office":
        return _end(
            f"I can't confirm this booking online. Please call the office at {PHONE} or email {firmdata.firm_email('general')}, and they will help you."
        )

    if code == "invalid":
        errors = r.get("errors") or {}
        state["invalid"] = state.get("invalid", 0) + 1
        if state["invalid"] > MAX_TRIES:
            return fallback("invalid")
        for key, step in (("name", "name"), ("email", "email"), ("phone", "phone"), ("start", "times"), ("otherSide", "other")):
            if key in errors:
                state["step"], state["tries"], state["fixing"] = step, 0, True
                if step == "email":
                    state["email_tries"] = 0
                if step == "times":
                    reply = _slots_for(state, api)
                    if reply is None or not reply.get("ok") or not reply.get("slots"):
                        return fallback("unreachable")
                    _load_times(state, reply)
                out = prompt(state)
                out.text = f"Something wasn't accepted: your {key.replace('otherSide', 'other side').replace('start', 'time')}. " + out.text
                return out
        return fallback("invalid")

    return fallback("unavailable")
