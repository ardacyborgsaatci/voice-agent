"""The question set (questions.json) and how an answer is checked against it. Used by tests/test_questions.py (offline)
and run_questions.py (live, needs a Groq key). Checking is by words, so it is rough: it catches a missing fact, an invented
one and a forbidden phrase, not a badly worded answer. A person still has to listen to a real call."""
import json
import re
from pathlib import Path

import knowledge

PATH = Path(__file__).parent / "questions.json"
QUESTIONS: list[dict] = json.loads(PATH.read_text(encoding="utf-8"))["questions"]
SOURCES = {"model", "urgent", "advice", "person", "change", "flow"}


def by_set(name: str) -> list[dict]:
    return [q for q in QUESTIONS if q["set"] == name]


def check_answer(q: dict, answer: str) -> list[str]:
    """What is wrong with an answer. Empty list = it passes the words check."""
    low = (answer or "").lower()
    problems = []
    for group in q.get("include", []):
        if not any(w.lower() in low for w in group):
            problems.append("missing one of: " + " / ".join(group))
    for w in q.get("avoid", []):
        if w.lower() in low:
            problems.append("contains: " + w)
    for pat in q.get("avoidRegex", []):
        if re.search(pat, answer or "", re.IGNORECASE):
            problems.append("matches: " + pat)
    return problems


def sections_sent(q: dict) -> list[str]:
    """Headings of the knowledge sections the model would be given for this question on its own."""
    return [c["heading"] for c in knowledge.relevant_sections(q["ask"])]


def sections_ok(q: dict) -> bool:
    """True when no section was required, or at least one required heading is among those sent."""
    want = q.get("sections")
    if not want:
        return True
    sent = sections_sent(q)
    return any(any(w.lower() in h.lower() for h in sent) for w in want)
