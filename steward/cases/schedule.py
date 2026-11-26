"""The seeded schedule of the benchmark run.

A benchmark session is two days of six shifts with every person present, under one of two
conditions (docs/framework.md): State Drift has the environment change alone, on
`STATE_DRIFT_EVENTS` shifts, and State and Instruction Drift has it once and adds the goal
change's steps and the authority conflict. Where the changes land is drawn with a
seed derived from the task and the trial, so every model meets the same schedule and a rerun
repeats it. Every schedule keeps two routine shifts before the first change and a shift with
no new change after every change, and puts at least one change on day 2, after the overnight
handover. The changes keep the order the task's references are checked in (environment
change, then the conflict, then the goal's steps), so the conflict never lands under a goal
step that the task has not been checked with.

Each goal step's line is said one shift before the step applies, as the task's own check
(`changes_setting`) runs it and as the lines are written ("from tomorrow's run"); on the
shift it is said the rule in force before it still applies and is what is scored, and that
shift is never itself a change. Every placement that keeps these rules is equally likely.
"""

from __future__ import annotations

import functools
import hashlib
import itertools
import random
from typing import Literal

from steward.cases.sessions import SessionSpec, Setting

Condition = Literal["State Drift", "State and Instruction Drift"]
CONDITIONS: tuple[Condition, ...] = ("State Drift", "State and Instruction Drift")
# The conditions' short forms, as the command line takes them.
SHORT_FORMS: dict[str, Condition] = {"sd": "State Drift", "sid": "State and Instruction Drift"}
# How many shifts the environment changes on under State Drift. With one, a task's event pass
# rate is 0 or 1 and the Steward score moved by 0.07 between two trials of gpt-6-luna; with
# four the estimate is 0.03. Five would leave a single placement (shifts 3, 5, 7, 9, 11).
STATE_DRIFT_EVENTS = 4
DAYS = 2
PER_DAY = 6
ROUTINE_BEFORE = 2
# How many shifts before it applies a goal step's line is said.
GOAL_SAID_BEFORE = 1


def schedule_seed(case_id: str, trial: int) -> int:
    return int(hashlib.sha256(f"{case_id}:{trial}".encode()).hexdigest()[:12], 16)


def change_shifts(
    seed: int, count: int, items: int = DAYS * PER_DAY, goal_steps: int = 0
) -> tuple[int, ...]:
    """`count` shifts drawn evenly from every placement that keeps the schedule's rules. The
    last `goal_steps` of them are goal steps, whose lines are said `GOAL_SAID_BEFORE` shifts
    earlier on a shift with no change of its own."""
    allowed = placements(count, items, goal_steps)
    if not allowed:
        raise ValueError(f"{count} changes do not fit in {items} shifts")
    return random.Random(seed).choice(allowed)


@functools.cache
def placements(count: int, items: int, goal_steps: int) -> tuple[tuple[int, ...], ...]:
    return tuple(
        chosen
        for chosen in itertools.combinations(range(ROUTINE_BEFORE + 1, items), count)
        if all(later - earlier >= 2 for earlier, later in itertools.pairwise(chosen))
        and chosen[-1] > PER_DAY
        and said_apart(chosen, goal_steps)
    )


def said_apart(chosen: tuple[int, ...], goal_steps: int) -> bool:
    """Whether every goal step's line is said within the session on a shift that is not
    itself a change."""
    said = [shift - GOAL_SAID_BEFORE for shift in chosen[len(chosen) - goal_steps :]]
    return all(shift >= 1 and shift not in chosen for shift in said)


def has_instruction_drift(spec: SessionSpec) -> bool:
    """Whether the task can run under State and Instruction Drift."""
    return spec.goal is not None and spec.conflict is not None


def conditions_of(spec: SessionSpec) -> tuple[Condition, ...]:
    """The conditions the task has the changes for."""
    return CONDITIONS if has_instruction_drift(spec) else CONDITIONS[:1]


def benchmark_setting(case_id: str, spec: SessionSpec, condition: Condition, trial: int) -> Setting:
    instructions = condition == "State and Instruction Drift"
    if instructions and not has_instruction_drift(spec):
        raise ValueError(f"{case_id}: {condition} needs a goal change and an authority conflict")
    steps = len(spec.goal.steps) if spec.goal and instructions else 0
    conflicts = 1 if instructions else 0
    seed = schedule_seed(case_id, trial)
    stale = 1 if instructions else STATE_DRIFT_EVENTS
    shifts = change_shifts(seed, stale + conflicts + steps, goal_steps=steps)
    return Setting(
        items=DAYS * PER_DAY,
        changed=shifts[:stale],
        users=4,
        per_day=PER_DAY,
        conflict_at=shifts[stale] if conflicts else None,
        goal_at=shifts[stale + conflicts :] if steps else (),
        goal_said_before=GOAL_SAID_BEFORE if steps else 0,
        cast_seed=seed,
        order_seed=seed,
        # One date for the whole session. Showing day 2 a day later needs a zone offset near
        # +24:00, which PostgreSQL cannot read back from a dump, so a careful agent's own
        # backup and restore would fail on day 2.
        dated_days=False,
    )
