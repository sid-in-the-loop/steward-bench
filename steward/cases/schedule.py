"""The seeded schedule of the benchmark run.

A benchmark session is two days of six shifts with every person present. The kinds of change
stack as the levels define them: Level 1 has the environment change, Level 2 adds the goal
change's steps and Level 3 adds the authority conflict. Where the changes land is drawn with a
seed derived from the task and the trial, so every model meets the same schedule and a rerun
repeats it. Every schedule keeps two routine shifts before the first change and a shift with
no new change after every change, and puts at least one change on day 2, after the overnight
handover. The changes keep the order the task's references are checked in (environment
change, then the conflict, then the goal's steps), so the conflict never lands under a goal
step that the task has not been checked with.

Each goal step's line is said one shift before the step applies, as the task's own check
(`changes_setting`) runs it and as the lines are written ("from tomorrow's run"); on the
shift it is said the rule in force before it still applies and is what is scored. Since
changes are two shifts apart, the shift a line is said on is never itself a change. Where the
level allows it, one step is said on the last shift of day 1 and applies from the first of
day 2, so that it must survive the overnight handover in the agent's own notes.
"""

from __future__ import annotations

import hashlib
import itertools
import random
from typing import Literal

from steward.cases.sessions import SessionSpec, Setting

BenchmarkLevel = Literal[1, 2, 3]
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
    last `goal_steps` of them are goal steps; placements in which one of those applies from
    the first shift of day 2 are drawn from when there are any."""
    allowed = range(ROUTINE_BEFORE + 1, items)
    placements = [
        chosen
        for chosen in itertools.combinations(allowed, count)
        if all(later - earlier >= 2 for earlier, later in itertools.pairwise(chosen))
        and chosen[-1] > PER_DAY
    ]
    if not placements:
        raise ValueError(f"{count} changes do not fit in {items} shifts")
    overnight = [
        chosen for chosen in placements if goal_steps and PER_DAY + 1 in chosen[-goal_steps:]
    ]
    return random.Random(seed).choice(overnight or placements)


def benchmark_setting(
    case_id: str, spec: SessionSpec, level: BenchmarkLevel, trial: int
) -> Setting:
    if level >= 2 and spec.goal is None:
        raise ValueError(f"{case_id}: Level {level} needs a goal change")
    if level == 3 and spec.conflict is None:
        raise ValueError(f"{case_id}: Level 3 needs an authority conflict")
    steps = len(spec.goal.steps) if spec.goal and level >= 2 else 0
    conflicts = 1 if level == 3 else 0
    seed = schedule_seed(case_id, trial)
    shifts = change_shifts(seed, 1 + conflicts + steps, goal_steps=steps)
    return Setting(
        items=DAYS * PER_DAY,
        changed=shifts[:1],
        users=4,
        per_day=PER_DAY,
        conflict_at=shifts[1] if conflicts else None,
        goal_at=shifts[1 + conflicts :],
        goal_said_before=GOAL_SAID_BEFORE if steps else 0,
        cast_seed=seed,
        order_seed=seed,
        # One date for the whole session. Showing day 2 a day later needs a zone offset near
        # +24:00, which PostgreSQL cannot read back from a dump, so a careful agent's own
        # backup and restore would fail on day 2.
        dated_days=False,
    )
