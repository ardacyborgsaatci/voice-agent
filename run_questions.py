"""Runs questions.json through the real router with the real Groq chat model. Text only: no microphone, no speech to
text, no voice. Needs GROQ_API_KEY (in .env or the environment).

    python run_questions.py                 # all questions
    python run_questions.py --set holdout   # only the holdout set
    python run_questions.py --out results/2026-11-20-live.json

Prints one line per question and a summary, and writes every answer to the output file. The words check is rough (see
questions.py); read the answers too. One answer per question per run, and the model is not deterministic.
"""
import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import booking_flow  # noqa: E402
import llm  # noqa: E402
import questions  # noqa: E402
import router  # noqa: E402


class CannedBooking:
    """The booking service, answered from a fixed list so a question about booking can be run without n8n."""

    def slots(self, area, after=None, limit=40):
        times = [("2026-11-12", "10:00", "2026-11-12T16:00:00Z"), ("2026-11-12", "14:00", "2026-11-12T20:00:00Z"), ("2026-11-13", "10:30", "2026-11-13T16:30:00Z")]
        return {"ok": True, "area": area, "areaName": "Family law", "timezoneName": "Central Standard Time", "consultMinutes": 45,
                "fee": "$150 for this first consultation, credited toward the retainer if you hire the firm.",
                "slots": [{"start": s, "date": d, "time": t, "label": ""} for d, t, s in times], "more": False}

    def book(self, payload):
        raise booking_flow.BookingError("not used in the question run")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=["dev", "holdout", "all"], default="all")
    ap.add_argument("--out", default=f"results/{date.today().isoformat()}-live.json")
    ap.add_argument("--pause", type=float, default=1.5, help="seconds between model calls (Groq's free tier is rate limited)")
    args = ap.parse_args()

    chosen = questions.QUESTIONS if args.set == "all" else questions.by_set(args.set)
    results, passed = [], 0
    for q in chosen:
        try:
            turn = router.route(q["ask"], [], None, api=CannedBooking(), ask_model=llm.ask_model)
            answer, source, error = turn.text, turn.source, ""
            if source == "model":
                time.sleep(args.pause)
        except Exception as exc:  # report the kind of error, keep going
            answer, source, error = "", "error", type(exc).__name__
        problems = questions.check_answer(q, answer) if not error else ["error: " + error]
        if q["source"] != source and not error:
            problems.append(f"answered by {source}, expected {q['source']}")
        ok = not problems
        passed += ok
        results.append({"id": q["id"], "set": q["set"], "ask": q["ask"], "source": source, "answer": answer, "problems": problems})
        print(("ok   " if ok else "FAIL ") + q["id"], "|", answer[:110].replace("\n", " "), "" if ok else "| " + "; ".join(problems))
    blocked = sum(1 for r in results if r["source"] == "blocked")
    print(f"\n{passed} of {len(results)} pass the words check; {blocked} answers were replaced by the answer check")
    out = Path(__file__).parent / args.out
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"date": date.today().isoformat(), "model": llm.GROQ_MODEL, "results": results}, indent=2), encoding="utf-8")
    print("answers written to", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
