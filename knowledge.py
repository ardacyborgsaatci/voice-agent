"""The knowledge base the model answers from, and the prompt that frames it.

Every .md/.txt file in knowledge/ is split into sections at each '## ' heading. On each turn only the sections that
match what the caller said are sent to the model, which keeps replies quick and inside Groq's free token-per-minute
limit. The files are copies of the shared ../knowledge/ folder (run `node ../scripts/sync-knowledge.mjs`).
"""
import re
from pathlib import Path

import firmdata
from firmdata import FIRM

BASE_DIR = Path(__file__).parent
TOP_K = 4

SYSTEM_PROMPT = (
    f"You are the automated voice assistant for {FIRM['name']}, a small law firm in {FIRM['city']}. "
    "The firm is a fictional demo made for a portfolio project; say so only if asked. "
    "You are on a phone call and your reply will be spoken aloud: use one to three short sentences of plain text, "
    "with no lists and no markdown. "
    "You are not a lawyer. Never give legal advice, never say whether anyone has a case or what their rights are, "
    "never predict or promise an outcome, never state a deadline or time limit, and never promise that an attorney "
    "will respond by a certain time. If asked about someone's own situation, say an attorney can discuss it at a first consultation. "
    "Answer questions about the firm (practice areas, consultation fees, hours, location, what to bring, how booking works) "
    "only from the knowledge below. Never invent a fact, fee, name, link or policy. If the knowledge does not cover the question, "
    f"say you don't know and suggest emailing {firmdata.firm_email('general')}. "
    "To book a consultation, tell the caller to say the word book and you will take them through it. "
    "You cannot change or cancel a booking, take payment details, discuss anyone's matter or share documents. "
    "Ask callers not to share confidential details by phone."
)

STOPWORDS = {
    "the", "and", "you", "your", "are", "was", "for", "with", "what", "when", "where", "how", "who",
    "does", "do", "can", "could", "would", "have", "has", "this", "that", "there", "any", "about",
    "much", "many", "get", "got", "tell", "please", "give", "its", "our", "from", "they", "them",
}


# A caller's word that the knowledge base says differently. Only words a question actually missed on are listed here.
EXPANSIONS = {
    "spanish": "interpreter", "language": "interpreter", "languages": "interpreter",
    "translator": "interpreter", "translate": "interpreter", "bilingual": "interpreter",
    "address": "office location", "located": "office location", "directions": "office location",
}

# An expansion is skipped when one of these words is also in the question: "billing email address" is not a street address.
EXPANSION_UNLESS = {"address": {"email", "emails", "mail", "web", "website", "ip"}}


def stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower().replace("-", ""))
    return {stem(w) for w in words if len(w) > 2 and w not in STOPWORDS}


def load_chunks(folder: Path | None = None) -> list[dict]:
    folder = folder or BASE_DIR / "knowledge"
    chunks = []
    if not folder.is_dir():
        return chunks
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() not in (".txt", ".md"):
            continue
        text = path.read_text(encoding="utf-8").strip()
        title = text.splitlines()[0].lstrip("# ").strip() if text else path.stem
        for part in re.split(r"\n(?=## )", text):
            part = part.strip()
            if not part:
                continue
            heading = part.splitlines()[0].lstrip("# ").strip()
            chunks.append(
                {
                    "text": part,
                    "heading": heading,
                    "title": title,
                    "body_tokens": tokens(part),
                    "head_tokens": tokens(heading + " " + title),
                }
            )
    return chunks


CHUNKS = load_chunks()


def relevant_sections(question: str, chunks: list[dict] | None = None) -> list[dict]:
    """The sections that best match the question (headings count extra). Nothing matched: the general intro sections."""
    chunks = CHUNKS if chunks is None else chunks
    q = tokens(question)
    words = re.findall(r"[a-z]+", question.lower())
    for word in words:
        if word in EXPANSIONS and not EXPANSION_UNLESS.get(word, set()) & set(words):
            q |= tokens(EXPANSIONS[word])
    scored = []
    for index, chunk in enumerate(chunks):
        score = len(q & chunk["body_tokens"]) + 2 * len(q & chunk["head_tokens"])
        if score:
            scored.append((score, index))
    ranked = sorted(scored, key=lambda s: (-s[0], s[1]))
    cutoff = ranked[0][0] * 0.25 if ranked else 0  # drop very weak matches to save tokens
    picked = [i for score, i in ranked[:TOP_K] if score >= cutoff]
    if not picked:
        picked = list(range(min(2, len(chunks))))
    return [chunks[i] for i in sorted(picked)]


def build_system(history: list[dict], chunks: list[dict] | None = None) -> tuple[str, str]:
    """(system prompt, the knowledge text in it). The knowledge text is also what the answer check compares fees and contacts with."""
    chunks = CHUNKS if chunks is None else chunks
    if not chunks:
        return SYSTEM_PROMPT, ""
    recent = " ".join(m["content"] for m in history if m["role"] == "user")[-600:]
    topics = "; ".join(sorted({c["heading"] for c in chunks}))
    knowledge = "\n\n".join(c["text"] for c in relevant_sections(recent, chunks))
    system = (
        SYSTEM_PROMPT
        + "\n\nAnswer from the knowledge below. If the answer is not in it, say you don't know rather than guessing. "
        "Topics the knowledge base covers: " + topics + ".\n\n" + knowledge
    )
    return system, knowledge
