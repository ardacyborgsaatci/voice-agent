"""Turning what a caller says into values, and values into words that read well aloud.

A speech recogniser writes an email address as "jane dot doe at example dot com" or as "jane.doe@example.com", a phone
number as digits or as words, and a "yes" in a dozen ways. Everything here is a plain function with no model and no I/O,
so each rule can be tested. A wrong guess is never silently accepted: the booking dialog reads every value back.
"""
import re

_DIGIT_WORDS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
_LETTER_NAMES = {"double": 2, "triple": 3}


# ---------------------------------------------------------------- yes / no / stop / repeat

_YES = re.compile(
    r"\b(yes|yeah|yep|yup|correct|right|sure|ok|okay|absolutely|definitely|affirmative|confirm|confirmed|go ahead|"
    r"book it|please do|do it|sounds good|that is right|thats right|thats correct|that is correct|exactly)\b"
)
_NO = re.compile(
    r"\b(no|nope|nah|wrong|incorrect|negative|not right|not correct|not quite|thats not|that is not|isnt right|"
    r"isnt correct|dont|do not|wait|hold on)\b"
)


def parse_yes_no(text) -> str | None:
    """'yes', 'no', or None when it is unclear or says both."""
    low = re.sub(r"['’]", "", str(text or "").lower())
    low = re.sub(r"[^a-z0-9 ]", " ", low)
    positive = re.sub(r"\b(?:not|isnt|aint|never|no)\s+(?:quite\s+)?(?:right|correct|sure|ok|okay)\b", " ", low)
    yes, no = bool(_YES.search(positive)), bool(_NO.search(low))
    if yes and not no:
        return "yes"
    if no and not yes:
        return "no"
    return None


def wants_stop(text) -> bool:
    low = re.sub(r"['’]", "", str(text or "").lower())
    return bool(re.search(r"\b(stop|cancel|never ?mind|forget it|forget about it|abort|quit|exit|hang up|leave it)\b", low))


def wants_repeat(text) -> bool:
    low = re.sub(r"['’]", "", str(text or "").lower()).strip(" .?!")
    return bool(
        re.search(r"\b(repeat|say that again|say it again|come again|pardon|sorry what|what was that|once more)\b", low)
        or low in {"what", "huh", "again", "sorry"}
    )


# ---------------------------------------------------------------- phone numbers

def parse_phone(text) -> str | None:
    """Digits of the longest run of spoken or typed digits, 7 to 15 of them, or None."""
    tokens = re.findall(r"[a-z]+|\d+", str(text or "").lower())
    runs, current, i = [], "", 0
    while i < len(tokens):
        t = tokens[i]
        if t.isdigit():
            current += t
        elif t in _DIGIT_WORDS:
            current += _DIGIT_WORDS[t]
        elif t in _LETTER_NAMES and i + 1 < len(tokens) and (tokens[i + 1] in _DIGIT_WORDS or tokens[i + 1].isdigit()):
            nxt = tokens[i + 1]
            current += (_DIGIT_WORDS.get(nxt) or nxt[0]) * _LETTER_NAMES[t]
            i += 1
        else:
            if current:
                runs.append(current)
            current = ""
        i += 1
    if current:
        runs.append(current)
    if not runs:
        return None
    best = max(runs, key=len)
    return best if 7 <= len(best) <= 15 else None


def digits_aloud(digits: str) -> str:
    """'5550142' -> '5 5 5, 0 1 4 2': grouped so it can be copied down."""
    d = re.sub(r"\D", "", digits)
    if len(d) == 10:
        groups = [d[:3], d[3:6], d[6:]]
    elif len(d) == 11:
        groups = [d[:1], d[1:4], d[4:7], d[7:]]
    elif len(d) == 7:
        groups = [d[:3], d[3:]]
    else:
        groups = [d[i:i + 3] for i in range(0, len(d), 3)]
    return ", ".join(" ".join(g) for g in groups)


# ---------------------------------------------------------------- email addresses

_EMAIL_FILLER = {
    "is", "its", "it", "my", "email", "emails", "address", "um", "uh", "okay", "ok", "so", "yes", "yeah", "sure",
    "and", "the", "that", "thats", "please", "well", "here", "you", "can", "use", "contact", "reach", "me",
    "at", "mine", "for", "now", "have", "e-mail", "send", "it's",
}
_EMAIL_END = {"please", "thanks", "thank", "that", "thats", "right", "okay", "ok", "yes", "my", "phone", "number", "and", "is", "it", "the", "so"}
_SYMBOLS = {"dot": ".", "period": ".", "point": ".", "dash": "-", "hyphen": "-", "underscore": "_", "plus": "+"}
_EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9\-]+(?:\.[a-z0-9\-]+)*\.[a-z]{2,}$")
_TYPED_EMAIL = re.compile(r"[a-z0-9._%+\-]+@[a-z0-9\-]+(?:\.[a-z0-9\-]+)+")


def _valid_email(e: str) -> bool:
    if len(e) > 254 or not _EMAIL_RE.match(e) or ".." in e:
        return False
    local = e.split("@", 1)[0]
    return not (local.startswith(".") or local.endswith("."))


def _expand_token(tok: str) -> str:
    """One spoken word -> the characters it stands for. 'j-o-h-n' -> 'john', 'dot' -> '.', 'five' -> '5'."""
    if tok in _SYMBOLS:
        return _SYMBOLS[tok]
    if tok in _DIGIT_WORDS:
        return _DIGIT_WORDS[tok]
    if re.fullmatch(r"(?:[a-z0-9]-)+[a-z0-9]", tok):
        return tok.replace("-", "")
    return tok


def parse_email(text) -> str | None:
    """An email address from typed or spoken text, or None. The caller always hears it read back."""
    low = str(text or "").lower().strip()
    typed = _TYPED_EMAIL.search(re.sub(r"[,;!?]", " ", low))
    if typed:
        cand = typed.group(0).rstrip(".")
        if _valid_email(cand):
            return cand
    tokens = [t for t in re.split(r"\s+", re.sub(r"[,;!?]", " ", low)) if t]
    tokens = [t.rstrip(".") if not re.search(r"[a-z0-9]\.[a-z]", t) else t for t in tokens]
    tokens = [t for t in tokens if t]
    # the spoken "at" that is followed by a "dot" later on is the @ sign (the last such one wins)
    at_positions = [i for i, t in enumerate(tokens) if t in ("at", "@") and any(x in _SYMBOLS and _SYMBOLS[x] == "." or "." in x for x in tokens[i + 1:])]
    for at in reversed(at_positions):
        local_tokens = []
        j = at - 1
        while j >= 0 and tokens[j] not in _EMAIL_FILLER:
            local_tokens.append(tokens[j])
            j -= 1
        local_tokens.reverse()
        domain_tokens = []
        for t in tokens[at + 1:]:
            if t in _EMAIL_END:
                break
            domain_tokens.append(t)
        local = "".join(_expand_token(t) for t in local_tokens)
        domain = "".join(_expand_token(t) for t in domain_tokens)
        cand = f"{local}@{domain}".strip(".")
        if _valid_email(cand):
            return cand
    return None


_SAID_AS_WORD = {"gmail", "yahoo", "outlook", "hotmail", "icloud", "aol", "protonmail", "live", "msn", "comcast"}
_TLD_AS_WORD = {"com", "org", "net", "edu", "gov", "example"}
_SPOKEN_CHAR = {".": "dot", "_": "underscore", "-": "dash", "+": "plus", "%": "percent"}


def _spell(s: str) -> str:
    return ", ".join(_SPOKEN_CHAR.get(c, c.upper() if c.isalpha() else c) for c in s)


def email_aloud(email: str) -> str:
    """'jane.doe@gmail.com' -> 'J, A, N, E, dot, D, O, E, at, gmail dot com': letters one by one, common words whole."""
    local, _, domain = email.lower().partition("@")
    labels = domain.split(".")
    spoken_labels = [l if (l in _SAID_AS_WORD or (i == len(labels) - 1 and l in _TLD_AS_WORD) or (i > 0 and l in _TLD_AS_WORD)) else _spell(l) for i, l in enumerate(labels)]
    return _spell(local) + ", at, " + " dot ".join(spoken_labels)


# ---------------------------------------------------------------- names

_NAME_LEAD = re.compile(
    r"^(?:(?:um+|uh+|yes|yeah|okay|ok|sure|so|well|hi|hello|hey|just)[\s,.!]+)*"
    r"(?:(?:my (?:full |legal )?name(?: is|s)|the name is|name is|it is|its|it's|this is|i am|i'm|im|call me|you can call me|"
    r"my name's|the name's)\s+)?",
    re.IGNORECASE,
)
_NAME_TAIL = re.compile(r"[\s,.]+(?:and|my|please|thanks|thank you|i|im|i'm|the|phone|email)\b.*$", re.IGNORECASE)


def parse_name(text) -> str | None:
    """A first and last name, or None. Names are never guessed from a longer story: more than five words is refused."""
    raw = str(text or "").strip()
    raw = _NAME_LEAD.sub("", raw)
    raw = _NAME_TAIL.sub("", raw)
    raw = raw.strip(" .,!?\"'")
    if not raw or "@" in raw or re.search(r"\d", raw):
        return None
    words = raw.split()
    if not 2 <= len(words) <= 5:
        return None
    for w in words:
        if not re.fullmatch(r"[^\W\d_]+(?:['’.\-][^\W\d_]+)*\.?", w):
            return None
    name = " ".join(words)
    if name == name.lower() or name == name.upper():
        name = " ".join(w[:1].upper() + w[1:].lower() if not re.match(r"^(?:mc|o'|de|van|von)", w.lower()) else w.title() for w in words)
    return name[:100]


# ---------------------------------------------------------------- the other side

NOT_KNOWN = "Not known to caller"
_NONE_RE = re.compile(
    r"^(?:no|none|nobody|no one|noone|nope|nothing|n/?a|not applicable|no other (?:side|party|person|one)|"
    r"there (?:is|isnt|isn't) (?:no |not |nobody|anyone|one)|there is none|there isnt one|theres none|theres no one)\b",
    re.IGNORECASE,
)
_UNKNOWN_RE = re.compile(r"\b(?:i )?(?:dont|do not|don't) know\b|\bnot sure\b|\bunsure\b|\bi cant say\b", re.IGNORECASE)
_OTHER_LEAD = re.compile(
    r"^(?:(?:um+|uh+|yes|yeah|okay|ok|sure|so|well)[\s,.!]+)*"
    r"(?:(?:the )?(?:other side|other party|other person|opposing party) (?:is|would be|will be)|it(?:'s| is)|its|against|"
    r"its my|it's my|it is my|that would be|that is|thats)\s+",
    re.IGNORECASE,
)


def parse_other_side(text) -> dict | None:
    """{'none': True} | {'name': str} | None. A long answer is a story, not a name, so it is refused."""
    raw = str(text or "").strip().strip(" .!?")
    if not raw:
        return None
    if _NONE_RE.search(raw) and len(raw.split()) <= 8:
        return {"none": True}
    if _UNKNOWN_RE.search(raw) and len(raw.split()) <= 8:
        return {"name": NOT_KNOWN}
    name = _OTHER_LEAD.sub("", raw).strip(" .,!?")
    if not name or "?" in raw or len(name.split()) > 12 or len(name) < 2:
        return None
    return {"name": name[:200]}


# ---------------------------------------------------------------- choosing one of the offered times

_ORDINALS = [
    (0, r"\b(first|1st|number one|option one|the earliest|earliest)\b"),
    (1, r"\b(second|2nd|number two|option two)\b"),
    (2, r"\b(third|3rd|number three|option three)\b"),
]
_MORE = re.compile(r"\b(later|other|another|different|more|none|neither|something else|next|further|none of those)\b")
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_HOUR_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}


def _clock_mentions(low: str) -> list[tuple[int, int, str | None]]:
    """(hour, minute, 'am'|'pm'|None) for every clock time said: '10 am', '2:30 pm', 'ten o clock', 'noon'."""
    out = []
    for m in re.finditer(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?\s?m\.?|p\.?\s?m\.?|o ?clock|in the morning|in the afternoon|in the evening)?", low):
        hour, minute, mer = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "")
        if m.group(2) is None and not mer and hour <= 3:
            continue  # a bare 1, 2 or 3 is an option number, not a time
        if hour > 23 or minute > 59:
            continue
        flag = "am" if mer.startswith("a") or "morning" in mer else "pm" if mer.startswith("p") or "afternoon" in mer or "evening" in mer else None
        out.append((hour, minute, flag))
    for m in re.finditer(r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+(?:o ?clock|a\.?\s?m\.?|p\.?\s?m\.?|in the (?:morning|afternoon|evening))", low):
        flag ="am" if re.search(r"a\.?\s?m|morning", m.group(0)) else "pm" if re.search(r"p\.?\s?m|afternoon|evening", m.group(0)) else None
        out.append((_HOUR_WORDS[m.group(1)], 0, flag))
    if re.search(r"\bnoon\b", low):
        out.append((12, 0, "pm"))
    return out


def parse_choice(text, slots: list[dict]):
    """('pick', index) | ('more', None) | None. slots carry 'date' (YYYY-MM-DD) and 'time' (HH:MM) in the firm's own time."""
    import datetime

    low = re.sub(r"['’]", "", str(text or "").lower())
    low = re.sub(r"[^a-z0-9:. ]", " ", low)
    said = low.strip(" .")
    n = len(slots)
    for idx, pat in _ORDINALS:
        if idx < n and re.search(pat, low):
            return ("pick", idx)
    if re.search(r"\b(last|latest)\b", low) and n:
        return ("pick", n - 1)
    # a date said aloud ("November 12", "the 12th") is not a clock time
    low = re.sub(r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}(?:st|nd|rd|th)?\b", " ", low)
    low = re.sub(r"\b\d{1,2}(?:st|nd|rd|th)\b", " ", low)
    # a clock time, or a weekday, that matches exactly one of the offered slots
    matched = set()
    for hour, minute, mer in _clock_mentions(low):
        for i, s in enumerate(slots):
            sh, sm = (int(x) for x in s["time"].split(":"))
            if sm != minute:
                continue
            if mer == "am" and sh == hour % 12 or mer == "pm" and sh == hour % 12 + 12 or mer is None and (sh == hour or sh % 12 == hour % 12):
                matched.add(i)
    day_hits = set()
    for i, s in enumerate(slots):
        wd = _WEEKDAYS[datetime.date.fromisoformat(s["date"]).weekday()]
        if re.search(r"\b" + wd + r"\b", low):
            day_hits.add(i)
    if matched:
        if len(matched) > 1 and day_hits:
            matched &= day_hits  # "ten am on friday": the time fits two days, the weekday settles it
        return ("pick", next(iter(matched))) if len(matched) == 1 else None
    if len(day_hits) == 1:
        return ("pick", next(iter(day_hits)))
    if _MORE.search(low):
        return ("more", None)
    # a bare "one", "two" or "3" is an option number only in a very short answer or after "take"/"choose" ("the one on Friday" is not)
    if len(said.split()) <= 2 or re.search(r"\b(?:take|choose|pick|want|go with)\s+(?:the\s+)?(?:one|two|three|1|2|3)\b", said):
        for idx, word in ((0, r"\bone\b|\b1\b"), (1, r"\btwo\b|\b2\b"), (2, r"\bthree\b|\b3\b")):
            if idx < n and re.search(word, low):
                return ("pick", idx)
    return None


# ---------------------------------------------------------------- reading a date and time aloud

_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def say_when(date_iso: str, hhmm: str) -> str:
    """'2026-11-12', '10:00' -> 'Thursday, November 12 at 10 AM'. The date and time are the firm's own local ones."""
    import datetime

    d = datetime.date.fromisoformat(date_iso)
    h, m = (int(x) for x in hhmm.split(":"))
    clock = f"{h % 12 or 12}:{m:02d} {'AM' if h < 12 else 'PM'}" if m else f"{h % 12 or 12} {'AM' if h < 12 else 'PM'}"
    return f"{_DAYS[d.weekday()]}, {_MONTHS[d.month - 1]} {d.day} at {clock}"


# ---------------------------------------------------------------- making text read well aloud

_EMAIL_IN_TEXT = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
_URL_IN_TEXT = re.compile(r"(?:https?://)?(?:[a-z0-9\-]+\.)+(?:com|org|net|edu|gov|example)(?:/[A-Za-z0-9\-_./]*)?", re.IGNORECASE)
_OFFICE_PHONE = re.compile(r"\b(\d{3})-(\d{4})\b")


def speakable(text: str, spell: list[str] | None = None) -> str:
    """Text for the voice. The page shows the original; this only changes how it is read.
    `spell` lists emails and digit strings that must be read one character at a time."""
    out = str(text or "")
    out = re.sub(r"[*_`#>]+", "", out)
    out = re.sub(r"^\s*[-•]\s+", "", out, flags=re.MULTILINE)
    for item in spell or []:
        if not item:
            continue
        said = email_aloud(item) if "@" in item else digits_aloud(item) if item.isdigit() else _spell(item)
        out = out.replace(item, "\u0000" + said + "\u0000")
    parts = out.split("\u0000")
    for i in range(0, len(parts), 2):  # only the parts that are not already spelled
        p = parts[i]
        p = _EMAIL_IN_TEXT.sub(lambda m: m.group(0).replace("@", " at ").replace(".", " dot "), p)
        p = _OFFICE_PHONE.sub(lambda m: digits_aloud(m.group(1) + m.group(2)), p)
        p = _URL_IN_TEXT.sub(lambda m: re.sub(r"^https?://", "", m.group(0).rstrip(".")).replace(".", " dot ").replace("/", " slash ") + ("." if m.group(0).endswith(".") else ""), p)
        p = re.sub(r"\$(\d[\d,]*)", r"\1 dollars", p)
        p = p.replace("&", " and ").replace("LLP", "L L P")
        parts[i] = p
    return re.sub(r"[ \t]{2,}", " ", "".join(parts)).strip()
