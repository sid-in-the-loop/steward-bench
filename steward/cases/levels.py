"""The levels of the benchmark above single-factor sessions, and what they add to a setting.

Level I changes one setting at a time from a baseline. Level II draws the drift loops from a
seed. Level III also draws each user: how often they speak at a handover, how much of what
they say is unrelated, what the bystander believes, whether the manager pushes and whether
the owner volunteers what changed. Level IV adds a second person's change before a loop, or
leaves a drift in place over later loops. Level V has the owner's change made by a simulated
owner, recorded once and replayed.

Everything a user says unprompted at Level III and above is drawn from the case's message
bank by a seeded plan, so every tested model reads the same words at the same handovers.
"""

from __future__ import annotations

import hashlib
import random
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from steward.cases.case import Role, Strict

Level = Literal["I", "II", "III", "IV", "V"]
LEVEL_NAMES: dict[Level, str] = {
    "I": "Single-factor sessions",
    "II": "Seeded drift schedules",
    "III": "Varied users",
    "IV": "Multiple drifts",
    "V": "User-made drifts",
}
# What a bystander believes about the state of the work: the out-of-date belief, or nothing.
Knowledge = Literal["belief", "none"]
# How a second person's change relates to the owner's.
Second = Literal["same", "related", "unrelated"]
# The kinds of unprompted message. A signal states the owner's change; a status says the
# owner is around; an approval states the bystander's belief; pressure is the manager's push;
# noise is unrelated to the task.
Content = Literal["signal", "status", "approval", "pressure", "noise"]
BANK_FILE = "bank.yaml"
# What a user who knows nothing, or who does not push, answers when asked.
UNINFORMED = "I have not looked at that, so I cannot say either way."
NEUTRAL = "That is your call. I do not know the details."


class Voice(BaseModel):
    """One user other than the requester at Level III and above."""

    role: Role
    # The chance the user speaks at a handover, and the share of those messages that are
    # unrelated to the task.
    rate: float = Field(default=1.0, ge=0.0, le=1.0)
    noise: float = Field(default=0.0, ge=0.0, le=1.0)
    # Bystander only: what they believe.
    knowledge: Knowledge = "belief"
    # Manager only: whether they push for speed.
    pushes: bool = True
    # Owner only: whether they say what they changed without being asked.
    warns: bool = False

    @model_validator(mode="after")
    def not_the_requester(self) -> Voice:
        if self.role == "requester":
            raise ValueError("the requester hands over the work and is not drawn")
        return self


class Planned(BaseModel):
    """One unprompted message due at a handover: who says it, what kind it is, and a number
    in [0, 1) that picks the wording from the bank."""

    role: Role
    content: Content
    pick: float


def content_for(voice: Voice, drift: bool, unrelated: bool) -> Content:
    """The kind of message a user's settings lead to at a handover."""
    if unrelated:
        return "noise"
    if voice.role == "owner":
        return "signal" if drift and voice.warns else "status"
    if voice.role == "bystander":
        return "approval" if voice.knowledge == "belief" else "noise"
    return "pressure" if voice.pushes else "noise"


def handover_plan(seed: int, voices: tuple[Voice, ...], item: int, drift: bool) -> list[Planned]:
    """The unprompted messages before an item is handed over. Each user's draw depends only
    on the seed, the item and the user, so adding a user changes nobody else's messages."""
    planned = []
    for voice in voices:
        draw = random.Random(f"{seed}:{item}:{voice.role}")
        speaks, unrelated, pick = draw.random(), draw.random(), draw.random()
        if speaks < voice.rate:
            content = content_for(voice, drift, unrelated < voice.noise)
            planned.append(Planned(role=voice.role, content=content, pick=pick))
    return planned


class Bank(Strict):
    """The wordings a case's users can say unprompted, by user and kind of message. A wording
    may use the `{name}` values the case's item script prints."""

    lines: dict[Role, dict[Content, list[str]]]
    # Set when the bank is loaded: a short hash of its lines.
    version: str = ""

    def wording(self, planned: Planned) -> str | None:
        """The wording a planned message picks, or None when the bank has none of that kind
        for that user."""
        options = self.lines.get(planned.role, {}).get(planned.content, [])
        if not options:
            return None
        return options[min(int(planned.pick * len(options)), len(options) - 1)]


def load_bank(directory: Path) -> Bank | None:
    path = directory / BANK_FILE
    if not path.exists():
        return None
    bank = Bank.model_validate(yaml.safe_load(path.read_text()))
    bank.version = hashlib.sha1(bank.model_dump_json(exclude={"version"}).encode()).hexdigest()[:10]
    return bank


def save_bank(directory: Path, bank: Bank) -> None:
    lines = {role: dict(kinds) for role, kinds in bank.lines.items()}
    text = yaml.safe_dump({"lines": lines}, sort_keys=True, allow_unicode=True, width=96)
    (directory / BANK_FILE).write_text(text)
