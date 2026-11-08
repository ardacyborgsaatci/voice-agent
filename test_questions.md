# Test questions for the Marlow & Pine agent

Kept outside the knowledge folder on purpose, so these answers are not loaded into the agent. Ask the questions out loud and check the answers against the expected column.

| # | Ask the agent | Expected answer (from the knowledge base) |
|---|---|---|
| 1 | What's the Harbor Blend price? | 14 dollars for a 250 g bag. |
| 2 | Are you open on Sunday? | Yes, 8:00 to 14:00. Closed Sunday afternoons. |
| 3 | Where's the Annex? | 88 Shore Road, Harrow Point. Open every day, 9:00 to 18:00. |
| 4 | How does the loyalty card work? | Buy 9 drinks, the 10th is free. Any drink up to 6 dollars. No fee. |
| 5 | What's the winter drink? | Winter spice latte, 5.50 dollars, December 1 to February 15. |
| 6 | When is the Tuesday cupping? | 8:30 to 9:30 at the main café. Free, no booking needed. |
| 7 | How much is shipping? | 5 dollars, free on orders over 60 dollars. 3 to 5 business days. |
| 8 | Can I return a bag I opened? | Not for taste reasons, but they'll suggest a different blend. |
| 9 | Do you have vegan food? | Vegan banana bread, 3.75 dollars. Contains wheat. |
| 10 | Is there a tasting I can book? | Saturday at 11:00 at the roastery, 12 dollars per person, book in advance. |
| 11 | Do you ship to Canada? | No, US addresses only. |
| 12 | What's your founder's name? | Elena Marlow. |
| 13 | Can I bring my dog? | Yes, dogs are welcome on the patio. |
| 14 | What's the weather like today? | Should say it doesn't know. Nothing in the knowledge base covers live information. |
| 15 | Do you do jazz nights? | Should say it doesn't know. The events list doesn't mention jazz. |

## What to check
- **Facts:** the answer matches the expected column.
- **Refusals:** questions 14 and 15 should get an honest "I don't know" rather than a guess. If the agent makes something up, tighten the instruction in `voice_agent.py`.
- **Length:** answers should be 1 to 3 sentences, since they are spoken aloud.
