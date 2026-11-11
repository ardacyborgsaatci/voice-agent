# Voice results

Status on 2026-11-11, after converting the voice line to Dyzania LLP. **No live model run has been done yet**, so no accuracy number for the chat model is claimed here. What is measured, and what is still owed:

## Measured offline (no keys, no network)

| Check | Result | What it proves |
|---|---|---|
| Python tests (`python -m unittest discover -s tests`) | 124 of 124 pass | The booking dialog, the fixed replies, the answer check, the spoken-text parsers, the sealed state, the server and the question set behave as written. |
| Booking dialog against the real booking rules | passes, through `tests/bridge.js` | The same rules the website form uses, run for real: 4 hours notice, one active booking per email and phone, a double booking race, a stale slot, a service outage, a failed confirmation email, a repeat booking that gets the same words as a conflict hold. |
| Parity with the website's matching (`tests/parity.js`) | 34 of 34 sentences agree | The voice line and the website classify the practice area and the urgent wording the same way. |
| Fixed paths in `questions.json` | 11 of 11 get the expected source and words | Urgent, advice, person, change and booking questions are answered without the model. |
| Retrieval | dev 28 of 28, holdout 15 of 15 send a section that holds the answer | The right knowledge reaches the model. Not the same as the model answering well. |
| Ideal answers through the answer check | all 43 pass unchanged | A good answer is not blocked by the guards. |
| Everything else in the repo | firm 20, intake 26, triage 46, booking 43, notices 49, ops 8, handoffs 8, site 65, finance 9, sales eval 9: all pass | Nothing else broke. |

## How the sets were used

- **dev** (39 questions) was used while tuning: the retrieval words and the guards were changed after seeing misses on it. Two questions (Q27, Q28, the office address) were added after a router test showed that asking for the address did not pull the section that holds it; the fix was an address and "located" expansion in `knowledge.py`.
- **holdout** (15 questions) was written and then not tuned on. Its retrieval was run once, after tuning on dev only, and passed 13 of 13 questions that name a required section on the first run (15 of 15 including two with no required section). It passed again after the address fix, which came from a test and not from the holdout.
- Both sets were written by the same person who wrote the knowledge. Real callers will word things differently.

## Owed

1. **Live text run.** `python run_questions.py` with a `GROQ_API_KEY`. One answer per question, the model is not deterministic, and the words check is rough, so read the answers. When it is done, add a row below with the date, the model, how many pass on each set, and what failed.
2. **A real call.** `MANUAL-CALL.md`. Covers speech recognition, the voice reading emails and phone numbers, interruptions, and delay. None of that is tested by text.
3. **Booking through n8n.** The booking workflow has to be imported, published and given its two tables before the voice line can book for real (Phase 7). Until then, with `N8N_BOOKING_BASE` empty, a caller who asks to book is sent to the website.

## Live runs

| Date | Model | Dev | Holdout | Notes |
|---|---|---|---|---|
| (none yet) | | | | |

## Earlier results

The 2026-11-11 results for the old Wrenbook version (15 questions, 15 of 15 after two prompt changes) were for a different knowledge base and prompt, so they were removed. They are in git history.
