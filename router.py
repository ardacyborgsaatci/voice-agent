"""Decides what answers each thing the caller says. Pure: the model and the booking service are passed in.

Order matters, most careful first:
  1. time-sensitive wording (court date, served papers, deadline, safety, arrest, eviction)  -> fixed words
  2. a booking already in progress                                                          -> the booking dialog
  3. a request to change a booking, to book, for advice, for a person                        -> fixed words / the dialog
  4. everything else                                                                         -> the model, then the answer check

Only turns the model answered are added to the history the model sees. The rest stay out of it, so what a caller
said while booking (name, phone, email) or about an urgent matter is never sent to the chat model.
"""
from dataclasses import dataclass, field

import booking_flow
import firmdata
import guards
import knowledge


@dataclass
class Turn:
    text: str                        # shown on the page
    state: dict | None = None        # booking dialog state for the next turn
    spell: list = field(default_factory=list)
    link: dict | None = None
    source: str = "model"            # model | blocked | urgent | advice | person | change | flow | quiet
    private: bool = False            # True: keep this exchange out of the model's history
    problems: list = field(default_factory=list)


def _from_flow(reply: booking_flow.Reply) -> Turn:
    return Turn(reply.text, reply.state, reply.spell, reply.link, source="flow", private=True)


def route(user_text: str, history: list[dict], state: dict | None, *, api, ask_model) -> Turn:
    """history: earlier turns the model may see (user/assistant dicts). ask_model(messages, system) -> str."""
    flags = firmdata.urgent_flags(user_text)

    if flags:
        text = guards.urgent_reply(flags)
        if state:
            text += " I've paused the booking, and nothing was saved."
        return Turn(text, None, source="urgent", private=True)

    if state:
        return _from_flow(booking_flow.handle(state, user_text, api))

    if guards.wants_change(user_text):
        return Turn(guards.change_reply(), source="change", private=True)
    if guards.wants_booking(user_text):
        return _from_flow(booking_flow.start(user_text, api))
    if guards.asks_for_advice(user_text):
        return Turn(guards.advice_reply(), source="advice", private=True)
    if guards.wants_person(user_text):
        return Turn(guards.person_reply(), source="person", private=True)

    messages = [*history, {"role": "user", "content": user_text}]
    system, allowed = knowledge.build_system(messages)
    reply = guards.plain(ask_model(messages, system))
    if not reply:
        return Turn(guards.quiet_reply(), source="quiet", private=True)
    problems = guards.problems_in(reply, allowed)
    if problems:
        return Turn(guards.blocked_reply(), source="blocked", private=True, problems=problems)
    return Turn(reply, source="model")
