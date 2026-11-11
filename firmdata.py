"""Firm facts and word lists for the voice line.

The data files in firm/ are copies of the shared ../firm/ files (run `node ../scripts/sync-knowledge.mjs`).
The matching below mirrors firm/lib/text.js, areas.js and urgency.js, so a phone caller and a web visitor
are sorted the same way; tests/test_parity.py runs both implementations on the same sentences.

Nothing here calls a model, gives advice, or reads a date.
"""
import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).parent / "firm"


def _load(name: str) -> dict:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


FIRM = _load("firm.json")
AREAS = _load("practice-areas.json")
URGENCY = _load("urgency.json")
AVAIL = _load("availability.json")

AREA_IDS = [a["id"] for a in AREAS["areas"]]


def area_by_id(area_id: str) -> dict | None:
    return next((a for a in AREAS["areas"] if a["id"] == area_id), None)


# ---------- text helpers (same behaviour as firm/lib/text.js) ----------

def lower_text(s) -> str:
    s = "" if s is None else str(s)
    s = re.sub(r"['‘’]", "", s.lower())
    return re.sub(r"\s+", " ", s).strip()


_PHRASE_CACHE: dict[str, re.Pattern] = {}


def _phrase_regex(phrase: str) -> re.Pattern:
    if phrase not in _PHRASE_CACHE:
        words = re.sub(r"[^a-z0-9]+", " ", lower_text(phrase)).strip().split(" ")
        # whole words only; a plural or past-tense ending is allowed (divorce, divorced)
        _PHRASE_CACHE[phrase] = re.compile(
            r"(^|[^a-z0-9])(" + r"[^a-z0-9]+".join(re.escape(w) for w in words) + r")(?:s|es|ed|d)?(?![a-z0-9])"
        )
    return _PHRASE_CACHE[phrase]


def find_phrase(text: str, phrase: str):
    m = _phrase_regex(phrase).search(text)
    if not m:
        return None
    return m.start(2), m.end(2)


def mask_span(text: str, start: int, end: int) -> str:
    return text[:start] + " " * (end - start) + text[end:]


def strip_ambiguous(text: str) -> str:
    text = re.sub(r"\breal estate\b", " ", text)
    return re.sub(r"\b(?:in|police|county|jail) custody\b", " ", text)


def strip_harmless(text: str) -> str:
    """Phrases that look like court dates or served papers but are not. Same length as the input."""
    def blank(m):
        return " " * len(m.group(0))
    for pattern in (
        r"\bhard of hearing\b",
        r"\bhearing (?:aids?|loss|impaired|impairment|problems?)\b",
        r"\b(?:keep|keeps|kept|been|always|often|just|am|was|were) hearing\b",
        r"\bhearing (?:that|about|from|different|conflicting|things|rumou?rs|stories|voices)\b",
        r"\bserved (?:in|as|my country|with the (?:army|navy|air force|marines))\b",
    ):
        text = re.sub(pattern, blank, text)
    return text


# ---------- which practice area (same rules as firm/lib/areas.js with nothing pre-selected) ----------

def classify_area(text) -> dict:
    ids = AREA_IDS
    hits = {i: [] for i in ids}
    out_hits = {o["id"]: [] for o in AREAS["outOfScope"]}
    entries = []
    for a in AREAS["areas"]:
        for k in a["keywords"]:
            entries.append((k, "area", a["id"]))
    for o in AREAS["outOfScope"]:
        for k in o["keywords"]:
            entries.append((k, "out", o["id"]))
    entries.sort(key=lambda e: -len(e[0]))  # longest first; stable, like the JavaScript sort
    work = strip_ambiguous(lower_text(text))
    for k, kind, ident in entries:
        m = find_phrase(work, k)
        if not m:
            continue
        (hits if kind == "area" else out_hits)[ident].append(k)
        while m:
            work = mask_span(work, m[0], m[1])
            m = find_phrase(work, k)

    count = {i: len(hits[i]) for i in ids}
    order = sorted(ids, key=lambda i: (-count[i], ids.index(i)))
    top = order[0]
    top_count = count[top]
    tied = [i for i in ids if count[i] == top_count and top_count > 0]
    out_ranked = sorted(
        ((o, len(out_hits[o["id"]])) for o in AREAS["outOfScope"] if out_hits[o["id"]]),
        key=lambda x: -x[1],
    )
    out_top = out_ranked[0][0] if out_ranked else None

    if top_count == 0 and out_top:
        area, basis = "out_of_scope", "inferred"
    elif top_count == 0:
        area, basis = "unclear", "unclear"
    elif len(tied) == 1:
        area, basis = top, "inferred"
    else:
        area, basis = top, "mixed"
    return {
        "area": area,
        "basis": basis,
        "hits": {i: h for i, h in hits.items() if h},
        "outOfScope": {"id": out_top["id"], "name": out_top["name"], "referral": out_top["referral"]}
        if area == "out_of_scope" and out_top
        else None,
    }


# What a caller says when asked "which kind of matter?". These words are only used at that question, and only
# when exactly one area is named. "will" and "trust" count only in the shapes people actually say them.
_CHOICE_WORDS = {
    "family": [r"\bfamily\b"],
    "injury": [r"\bpersonal injur(?:y|ies)\b", r"\binjur(?:y|ies|ed)\b"],
    "employment": [r"\bemployment\b", r"\bemployer\b", r"\bworkplace\b", r"\bat work\b", r"\bmy (?:job|boss)\b", r"\bfired\b"],
    "estate": [
        r"\bestate\b", r"\bprobate\b", r"\bpower of attorney\b", r"\binheritance\b",
        r"\b(?:a|my|the|make|write|draft|update) will\b", r"\bwills\b",
        r"\b(?:a|my|the|family|living|revocable) trust\b", r"\btrusts\b",
    ],
}


def choose_area(text) -> dict:
    """Answer to the area question. Returns {kind: 'area'|'out'|'ambiguous'|'unclear', ...}."""
    c = classify_area(text)
    low = lower_text(text)
    named = {a for a, pats in _CHOICE_WORDS.items() if any(re.search(p, low) for p in pats)}
    named |= set(c["hits"].keys())
    if len(named) == 1:
        return {"kind": "area", "area": next(iter(named))}
    if len(named) > 1:
        return {"kind": "ambiguous", "areas": sorted(named, key=AREA_IDS.index)}
    if c["outOfScope"]:
        return {"kind": "out", "out": c["outOfScope"]}
    return {"kind": "unclear"}


# ---------- time-sensitive wording (same phrases as firm/lib/urgency.js; the voice line does not read dates) ----------

def urgent_flags(text) -> list[dict]:
    """Categories whose wording appears. On the phone any court, deadline or lockout wording counts as time-sensitive,
    whatever date is mentioned, because a spoken date cannot be checked."""
    low = strip_harmless(str(text or "").lower())
    flags = []
    for cat in URGENCY["categories"]:
        found = [p for p in cat["phrases"] if find_phrase(low, p)]
        if found:
            flags.append({"id": cat["id"], "label": cat["label"], "phrases": found, "alsoShow": cat.get("alsoShow")})
    return flags


# ---------- facts for fixed replies ----------

def _clock(hhmm: str) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {suffix}" if m else f"{h12} {suffix}"


def office_hours_text() -> str:
    hours = FIRM["officeHours"]
    days = hours["days"]
    names = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday", "fri": "Friday", "sat": "Saturday", "sun": "Sunday"}
    span = f"{names[days[0]]} to {names[days[-1]]}" if len(days) > 1 else names[days[0]]
    return f"{span}, {_clock(hours['open'])} to {_clock(hours['close'])}"


def firm_email(kind: str) -> str:
    return FIRM["emails"][kind]


BOOK_URL = f"https://{FIRM['domain']}/book"
