"""What the voice line decides without asking a model, and the check every model answer goes through.

Why: a language model can be talked into advice, or just get it wrong. For a law firm the wrong answer is a
person told "you have a strong case" or "your deadline is Friday". So the sensitive turns never reach the model:
urgent wording, requests for advice, requests to change a booking and requests for a person get fixed words
written here from the firm's own data. The model answers only the plain questions, and its answer is checked
before it is spoken. A blocked answer is replaced, never repaired.

Nothing here gives legal advice, reads a date, or promises a response time.
"""
import re

import firmdata
from firmdata import FIRM

PHONE = FIRM["phone"]
HOURS = firmdata.office_hours_text()
EMERGENCY = FIRM["language"]["emergency"]


def _norm(text) -> str:
    low = re.sub(r"['’]", "", str(text or "").lower())
    return re.sub(r"\s+", " ", low).strip()


# ---------------------------------------------------------------- what the caller is asking for

_INFO_OPENERS = re.compile(
    r"^(?:how|what|whats|when|where|why|who|which|do you|does|is there|are there|is it|are you|tell me (?:about|how|what))\b"
)
_BOOK_REQUEST = re.compile(
    r"\b(?:book|schedule|set up|setup|arrange|make|get|want|need|like|request|reserve|have|start)\b[^.?!]{0,40}"
    r"\b(?:consultation|consult|appointment|meeting|slot)\b"
    r"|\b(?:see|speak to|speak with|talk to|talk with|meet with|meet|consult)\s+(?:a|an|the|one of your|your)?\s*(?:lawyer|attorney)\b"
    r"|\bbook me\b|\bbook (?:it|that|one|a time|now)\b|^book\b|^(?:please )?book\b"
)
_NOT_BOOK = re.compile(r"\b(?:dont|do not|not|no longer|never)\b[^.?!]{0,15}\b(?:want|need|wish)\b[^.?!]{0,15}\b(?:book|appointment|consultation)\b")
_CHANGE = re.compile(
    r"\b(?:cancel|reschedule|rebook|change|move|postpone|push back|update)\b[^.?!]{0,30}\b(?:appointment|booking|consultation|meeting|time)\b"
    r"|\bmy (?:appointment|booking|consultation)\b[^.?!]{0,30}\b(?:cancel|change|move|reschedule)\b"
)
_PERSON = re.compile(
    r"\b(?:speak|talk|connect|transfer|put me through|get me|let me (?:speak|talk)|can i (?:speak|talk))\b[^.?!]{0,25}"
    r"\b(?:person|human|someone|somebody|receptionist|representative|operator|real)\b"
    r"|\b(?:receptionist|operator|human being|real person|real human)\b|\bhuman\b(?!\s+resources)"
)
_ADVICE = re.compile(
    r"\bdo (?:i|we) have a (?:case|claim)\b|\bis there a case\b|\b(?:can|should|could|may) (?:i|we) (?:sue|countersue|appeal|fight)\b|"
    r"\bwill (?:i|we) win\b|\b(?:my|our) (?:chances|odds)\b|\bwhat are my (?:chances|odds|rights|options)\b|"
    r"\b(?:how much|what) is my case worth\b|\bwhat is my case worth\b|\bis (?:it|that|this) (?:legal|illegal|lawful|allowed)\b|"
    r"\bam i entitled\b|\bhow long do i have\b|\b(?:when|what) is my deadline\b|\bwhats my deadline\b|\bwhen is it due\b|"
    r"\bwhat should i do (?:about|if|with|now)\b|\bshould i (?:sign|settle|accept|reject|plead|file|respond|answer|ignore|hire)\b|"
    r"\bcan they (?:do|take|make) (?:that|this|me)\b|\bwhat happens if i (?:dont|do not|ignore|miss)\b|\bam i (?:liable|guilty|at fault)\b|"
    r"\bwho(?:s| is) at fault\b|\bwill (?:i|he|she|they) get (?:custody|the house|alimony|paid|compensation)\b"
)


def wants_booking(text) -> bool:
    low = _norm(text)
    if not low or _NOT_BOOK.search(low) or _CHANGE.search(low):
        return False
    if _INFO_OPENERS.match(low) and not re.match(r"^(?:do you|does)\b.*\b(?:book|schedule)\b.*\bfor me\b", low):
        return False
    return bool(_BOOK_REQUEST.search(low))


def wants_change(text) -> bool:
    return bool(_CHANGE.search(_norm(text)))


def wants_person(text) -> bool:
    return bool(_PERSON.search(_norm(text)))


def asks_for_advice(text) -> bool:
    return bool(_ADVICE.search(_norm(text)))


# ---------------------------------------------------------------- fixed replies

_REFERRAL_ONLY = {"detained", "lockout"}


def urgent_reply(flags: list[dict]) -> str:
    ids = {f["id"] for f in flags}
    parts = []
    if "safety" in ids:
        parts.append(EMERGENCY)
    parts.append("That sounds time-sensitive.")
    needs_attorney = bool(ids - _REFERRAL_ONLY)
    if needs_attorney:
        parts.append("I can't tell you what a deadline is or what to do, so please speak to an attorney as soon as you can.")
        parts.append(
            f"If you are already a client, email {firmdata.firm_email('urgent')} and call the office at {PHONE} during office hours, {HOURS}. "
            "Online booking needs some notice, so for something this close the office is the quickest way."
        )
    if ids & _REFERRAL_ONLY:
        parts.append(
            "The firm doesn't handle criminal or housing matters. Please contact your local bar association's lawyer referral service, "
            "or a public defender in a criminal case, and do it quickly if a time limit may be running."
        )
    parts.append("I'm an automated assistant, not a lawyer, and I can't promise how fast an attorney will reply.")
    if needs_attorney:
        parts.append("If you'd like a first consultation, say book.")
    return " ".join(parts)


def advice_reply() -> str:
    return (
        "I can't give legal advice, say whether you have a case, or predict an outcome. "
        "An attorney can talk it through at a first consultation. "
        f"Say book and I'll find a time, or call the office at {PHONE}."
    )


def person_reply() -> str:
    return (
        f"I'm the firm's automated assistant. The receptionist is in during office hours, {HOURS}, on {PHONE}. "
        f"Outside those hours you can email {firmdata.firm_email('general')}, or say book and I'll arrange a first consultation."
    )


def change_reply() -> str:
    hours = firmdata.AVAIL["booking"]["cancelNoticeHours"]
    return (
        "I can't change or cancel a booking by voice. "
        f"Please call the office at {PHONE} or email {firmdata.firm_email('general')} at least {hours} hours before your appointment."
    )


def blocked_reply() -> str:
    return (
        "I'm not able to answer that one safely. An attorney can help at a first consultation, "
        f"so say book if you'd like me to arrange one, or call the office at {PHONE}."
    )


def quiet_reply() -> str:
    """Nothing came back from the model: say so instead of staying silent."""
    return f"Sorry, I didn't catch an answer to that. You can ask again, or call the office at {PHONE}."


# ---------------------------------------------------------------- the check on a model answer

_LEGAL_ACTION = r"(?:sue|file|settle|plead|appeal|countersue|contest|fight|refuse|reject|ignore|accept the offer|sign (?:it|that|the)|answer the (?:complaint|summons)|respond to the (?:complaint|summons))"
_BAD = [
    ("case_opinion", re.compile(r"\byou (?:probably |likely |definitely |certainly |may |might |do |dont |do not )?(?:have|got|lack)\s+(?:a |an |no )?(?:strong |good |solid |weak |valid |great |clear |winning )?(?:case|claim|chance)\b")),
    ("outcome", re.compile(r"\b(?:you|they|he|she) (?:will|would|should) (?:win|lose|recover|be awarded|get (?:custody|alimony|compensation|damages|the house|paid))\b")),
    ("advice", re.compile(r"\byou (?:should|must|need to|ought to|had better) " + _LEGAL_ACTION + r"\b")),
    ("advice", re.compile(r"\bi (?:advise|recommend|suggest|urge) (?:that )?you " + _LEGAL_ACTION + r"\b")),
    ("deadline_as_fact", re.compile(r"\b(?:your|the) (?:deadline|statute of limitations|time limit|filing deadline|response deadline) (?:is|will be|expires|ends|falls)\b")),
    ("deadline_as_fact", re.compile(r"\byou (?:have|must|need to) (?:file|respond|answer)\b[^.?!]{0,30}\b(?:within|by|before)\b")),
    ("promised_time", re.compile(r"\b(?:an? )?(?:attorney|lawyer|someone|the team|we) (?:will|shall) (?:call|respond|reply|contact|get back)[^.?!]{0,30}\b(?:within|by|today|tomorrow|shortly|soon|right away|immediately|\d)")),
    ("legal_conclusion", re.compile(r"\b(?:is|are|was|were) (?:legal|illegal|unlawful|lawful)\b|\byou(?:r)? (?:are|have) (?:entitled|not entitled)\b|\bentitled to\b")),
]
_GUARANTEE = re.compile(r"\bguarantee[sd]?\b")


_CLAUSE_BREAK = re.compile(r"[.!?;,:]|\bbut\b|\bhowever\b|\balthough\b")
_REFUSING = re.compile(r"\b(?:cannot|cant|can not|not|never|no|dont|do not|unable|wont|will not|nor|whether|if|without)\b")


def _refusing(low: str, start: int) -> bool:
    """True when the words just before a match, in the same clause, say the assistant is declining or asking ("I can't say
    whether you have a case"). The check is meant to catch the assistant claiming it, not refusing to."""
    clause = _CLAUSE_BREAK.split(low[max(0, start - 60):start])[-1]
    return bool(_REFUSING.search(clause))


def _firm_domain_ok(host: str) -> bool:
    return host.lower().endswith(FIRM["domain"])


def problems_in(reply: str, allowed_text: str = "") -> list[str]:
    """Reasons a model answer must not be spoken. An empty list means it passed."""
    low = _norm(reply)
    found = []
    for name, pat in _BAD:
        for m in pat.finditer(low):
            if not _refusing(low, m.start()):
                if name not in found:
                    found.append(name)
                break
    for m in _GUARANTEE.finditer(low):
        if not _refusing(low, m.start()):
            found.append("promise")
            break
    allowed = str(allowed_text or "") + " " + " ".join(str(v) for v in FIRM["emails"].values()) + " " + PHONE
    for amount in re.findall(r"\$\s?\d+(?:,\d{3})*(?:\.\d+)?", reply):
        if amount.replace(" ", "") not in allowed.replace(" ", ""):
            found.append("invented_amount")
            break
    email_re = r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+"
    for email in re.findall(email_re, reply):
        if email.lower() not in allowed.lower():
            found.append("unknown_email")
            break
    without_emails = re.sub(email_re, " ", low)  # an address is judged as a whole, not by its host
    for host in re.findall(r"\b(?:[a-z0-9\-]+\.)+(?:com|org|net|edu|gov|io|us|example)\b", without_emails):
        if not _firm_domain_ok(host):
            found.append("outside_link")
            break
    for phone in re.findall(r"\b\d{3}[-. ]\d{4}\b", reply):
        if phone.replace(".", "-").replace(" ", "-") not in allowed:
            found.append("unknown_phone")
            break
    return found


MAX_SENTENCES = 4


def plain(reply: str) -> str:
    """The reply as plain spoken text: no markdown, no more than MAX_SENTENCES sentences."""
    text = re.sub(r"[*_`#>]+", "", str(reply or ""))
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(sentences[:MAX_SENTENCES]).strip()
