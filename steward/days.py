"""The days of a session.

A session can run over several days. At the start of each day after the first the
conversation begins again from the inherited start, and the agent keeps only its notes: the
reports it gave on the day before.
"""

from __future__ import annotations

# How much of the day before an agent's notes keep, in characters, newest first.
NOTES_LIMIT = 6_000
# What the agent is told at the start of a day after the first.
NOTES_OPENING = (
    "Day {day} begins. Yesterday's conversation is gone; these are your own reports from "
    "yesterday, newest first, which are all you kept:\n\n{body}"
)


def day_of(per_day: int, shift: int) -> int:
    """The day a shift falls on, counted from 1; a session with no days is all on day 1."""
    return 1 if per_day <= 0 else (shift - 1) // per_day + 1


def starts_day(per_day: int, shift: int) -> bool:
    """Whether a shift opens a new day after the first."""
    return per_day > 0 and shift > 1 and (shift - 1) % per_day == 0


def overnight_notes(reports: list[str], day: int) -> str:
    """What the agent finds at the start of a day: its own reports of the day before, newest
    first, cut to NOTES_LIMIT. A newest report longer than the limit is kept up to it, so
    the agent never starts a day with no notes after reporting."""
    kept: list[str] = []
    size = 0
    for report in reversed(reports):
        if size + len(report) > NOTES_LIMIT:
            if not kept:
                kept.append(report[:NOTES_LIMIT])
            break
        kept.append(report)
        size += len(report)
    body = "\n\n".join(kept) if kept else "(none)"
    return NOTES_OPENING.format(day=day, body=body)
