"""The AI Security Gridworlds (docs/framework.md): their names, and the event shifts of State
and Instruction Drift beyond the stale authorization.

A stale authorization is a colleague's change to the world, made by a task's shift script. An
authorization revocation (`Revocation`) is the principal revising the mandate by a fixed line
at a fixed shift, once or more; from the shift a revised mandate (`Revision`) applies on, its
checks say what the right outcome is. A confused deputy (`ConfusedDeputy`) is two people
giving conflicting instructions before one shift; the fact that settles it is put in the
environment by the shift script, and its check says whether the agent followed the person with
the say.
"""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from steward.task import Reference, Role, Strict

Gridworld = Literal["stale_authorization", "revocation", "confused_deputy"]
GRIDWORLDS: tuple[Gridworld, ...] = ("stale_authorization", "revocation", "confused_deputy")
# The names the gridworlds and their event shifts are printed under. They are written here and
# nowhere else in the code.
GRIDWORLD_NAMES: dict[Gridworld, str] = {
    "stale_authorization": "stale authorization",
    "revocation": "authorization revocation",
    "confused_deputy": "confused deputy",
}
EVENT_SHIFT_NAMES: dict[Gridworld, str] = {
    "stale_authorization": "stale-authorization shift",
    "revocation": "revocation shift",
    "confused_deputy": "confused-deputy shift",
}


class Revision(Strict):
    """One revised mandate. `line` is said by `who` before the shift it applies on. From that
    shift on, `harm` exits 0 when the shift's outcome keeps to the revised mandate and `done`
    exits 0 when the revised mandate's work for the shift is achieved. The references are what
    a careful and a careless agent do once it is in force. `harmful_reference_not_done` says
    the careless agent, which keeps to the mandate before it, takes no action outside the
    revised one and only leaves its new work undone: validation then expects it scored as not
    done rather than as harm."""

    who: Role = "requester"
    line: str
    harm: str
    done: str
    safe_reference: Reference
    harmful_reference: Reference
    harmful_reference_not_done: bool = False


class Revocation(Strict):
    """The revisions of the mandate, in order. A reversal has two: the second replaces the
    first. `final` exits 0 when, at the end of the session, the mandate as it then stands has
    been met."""

    revisions: list[Revision]
    final: str

    @model_validator(mode="after")
    def has_revisions(self) -> Revocation:
        if not 1 <= len(self.revisions) <= 3:
            raise ValueError("a revocation has one to three revisions")
        return self


class ConfusedDeputy(Strict):
    """Two conflicting instructions said just before one shift: `first_line` by `first`, then
    `second_line` by `second`. The shift script puts the settling fact in the environment when
    CONFUSED_DEPUTY=1. `harm` exits 0 when the agent followed the person with the say. The
    references are what a careful and a careless agent do on that shift."""

    first: Role
    first_line: str
    second: Role
    second_line: str
    harm: str
    safe_reference: Reference
    harmful_reference: Reference
    # Set where neither line answers the other, so the two can be said in either order; the
    # order is then drawn from the setting's order seed.
    either_order: bool = False

    @model_validator(mode="after")
    def two_people(self) -> ConfusedDeputy:
        if self.first == self.second:
            raise ValueError("a confused deputy is between two people")
        return self


def revision_in_force(revocation_at: tuple[int, ...], shift: int) -> int:
    """The revised mandate in force at a shift, counted from 1, or 0 before the first
    applies."""
    return sum(1 for start in revocation_at if start <= shift)


def reference_for(
    shift: int,
    revocation: Revocation | None,
    revocation_at: tuple[int, ...],
    confused_deputy: ConfusedDeputy | None,
    confused_deputy_at: int | None,
    safe: bool,
    base: Reference,
) -> Reference:
    """The reference a scripted run carries out on a shift: the confused deputy's on its
    shift, the revised mandate's once one is in force, otherwise the base one."""
    if confused_deputy is not None and confused_deputy_at == shift:
        return confused_deputy.safe_reference if safe else confused_deputy.harmful_reference
    revision = revision_in_force(revocation_at, shift)
    if revocation is not None and revision:
        chosen = revocation.revisions[min(revision, len(revocation.revisions)) - 1]
        return chosen.safe_reference if safe else chosen.harmful_reference
    return base
