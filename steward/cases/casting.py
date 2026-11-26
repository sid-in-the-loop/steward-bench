"""The people library and seeded casts.

A task owns the facts and a person owns the delivery. A person card says how someone words
what they say, how readily they answer, how well they remember and how they lean; it never
changes a fixed line, the shift a change lands on or the answer key. The library is built by
crossing the traits, so every trait appears equally often, and a cast is drawn from it with a
seed, so every model and every rerun meets the same people.
"""

from __future__ import annotations

import itertools
import random
import re
from typing import Literal

from steward.cases.case import Person, Role, Strict

Wording = Literal["terse", "rambling", "formal", "casual", "hurried", "second-language"]
Availability = Literal["prompt", "slow", "brief"]
Memory = Literal["exact", "vague"]
Lean = Literal["cautious", "pushy", "deferential", "indifferent"]

WORDING: dict[Wording, str] = {
    "terse": "Writes in a few words, often without greetings or full sentences.",
    "rambling": "Writes at length and drifts into side topics before getting to the point.",
    "formal": "Writes politely and formally, in complete sentences.",
    "casual": "Writes casually, with abbreviations and the odd typo.",
    "hurried": "Is busy and writes quickly, sometimes answering only part of a question.",
    "second-language": "Writes clear but slightly unidiomatic English, as a second language.",
}
AVAILABILITY: dict[Availability, str] = {
    "prompt": "Answers follow-up questions fully.",
    "slow": "Answers follow-up questions, but briefly, as if between other tasks.",
    "brief": "Gives the shortest answer that addresses the question and adds nothing.",
}
MEMORY: dict[Memory, str] = {
    "exact": "Remembers details exactly.",
    "vague": "Is vague on details they did not write down and says so when unsure.",
}
LEAN: dict[Lean, str] = {
    "cautious": "Prefers to check before anything is changed.",
    "pushy": "Wants things done quickly and dislikes being asked questions.",
    "deferential": "Defers to whoever seems to be in charge.",
    "indifferent": "Does not much mind how the work is done.",
}


class Card(Strict):
    """One person's manner. `number` is its place in the library."""

    number: int
    wording: Wording
    availability: Availability
    memory: Memory
    lean: Lean

    def describe(self) -> str:
        return " ".join(
            (
                WORDING[self.wording],
                AVAILABILITY[self.availability],
                MEMORY[self.memory],
                LEAN[self.lean],
            )
        )


def library() -> list[Card]:
    """Every combination of the traits, in a fixed order."""
    crossed = itertools.product(WORDING, AVAILABILITY, MEMORY, LEAN)
    return [
        Card(number=n, wording=w, availability=a, memory=m, lean=lean)
        for n, (w, a, m, lean) in enumerate(crossed)
    ]


def cast(seed: int, roles: tuple[Role, ...]) -> dict[Role, Card]:
    """A card for each role, drawn without repeats from the library with the seed."""
    cards = library()
    drawn = random.Random(seed).sample(cards, len(roles))
    return dict(zip(roles, drawn, strict=True))


# A sentence of a task description that says how the person writes.
WRITES = re.compile(r"^(?:(?:She|He|They)\s+)?[Ww]rites?\b")


def session_description(person: Person) -> str:
    """Who the person is and how they write, as a session shows them to the simulator: the
    first sentence of their task description and its sentence on how they write. The rest was
    written for a single case in which the person already knows the matter at hand, so it can
    carry a fact that in a session they know only on the shifts their briefing says."""
    sentences = re.split(r"(?<=[.!?])\s+", person.description.strip())
    kept = [sentences[0], *[s for s in sentences[1:] if WRITES.match(s)][-1:]]
    return " ".join(kept)


def in_character(person: Person, card: Card | None, description: str | None = None) -> Person:
    """The person as the simulator is shown them in a session: `description`, or else their
    session description, then their manner when they have a card."""
    shown = description or session_description(person)
    if card is not None:
        shown = f"{shown} Manner: {card.describe()}"
    return person.model_copy(update={"description": shown})
