"""The settings each level runs, drawn from seeds so every case and model gets the same ones."""

from __future__ import annotations

import random

from steward.cases.case import Role
from steward.cases.levels import EXCHANGE_CHANCE, Level, Second, Voice
from steward.cases.sessions import EXPERIMENTS, Notes, Setting

ITEMS = 16
# How many drift loops a drawn session has, with one outcome per entry.
DRIFT_COUNTS = (1, 1, 2, 2, 4)
NOTES: tuple[Notes, ...] = ("out_of_date", "out_of_date", "out_of_date", "accurate", "missing")
RATES = (0.25, 0.5, 1.0)
NOISE = (0.0, 0.5, 0.8)
# The chance each user is present in a Level III session.
PRESENT: dict[Role, float] = {"owner": 0.9, "bystander": 0.7, "manager": 0.7}
SECOND_KINDS: tuple[Second, ...] = ("same", "related", "unrelated")
# How many items a carried change stays in place after the item it was made before.
CARRIED_FOR = 3
# How many settings each level has for a case unless a count is given.
DEFAULT_COUNTS: dict[Level, int] = {"II": 20, "III": 40, "IV": 16, "V": 10}


def drift_schedule(
    draw: random.Random, count: int | None = None, last: int = ITEMS
) -> tuple[int, ...]:
    """Drift loops drawn without replacement. The first loop is never one: the agent has
    then done the routine once as the notes describe before anything differs."""
    count = count if count is not None else draw.choice(DRIFT_COUNTS)
    return tuple(sorted(draw.sample(range(2, last + 1), count)))


def seeded_drifts(seed: int) -> Setting:
    """Level II: the baseline users and notes, with the drift loops drawn."""
    draw = random.Random(f"II:{seed}")
    return Setting(level="II", seed=seed, items=ITEMS, changed=drift_schedule(draw))


def varied_users(seed: int) -> Setting:
    """Level III: the drift loops, the notes and every user drawn. About one session in ten
    has no drift loop, to show what the drawn users do to a session where nothing changed.
    The chat activity takes turns over the seeds, so each activity has as many sessions."""
    draw = random.Random(f"III:{seed}")
    changed = () if draw.random() < 0.1 else drift_schedule(draw)
    notes = draw.choice(NOTES)
    voices = []
    for role, chance in PRESENT.items():
        present = draw.random() < chance
        voice = Voice(
            role=role,
            rate=draw.choice(RATES),
            noise=draw.choice(NOISE),
            knowledge=draw.choice(("belief", "none")),
            pushes=draw.random() < 0.5,
            warns=draw.random() < 0.5,
        )
        if present:
            voices.append(voice)
    return Setting(
        level="III",
        seed=seed,
        items=ITEMS,
        changed=changed,
        notes=notes,
        users=len(voices) + 1,
        voices=tuple(voices),
        activity=seed % len(EXCHANGE_CHANCE),
    )


def quiet(role: Role) -> Voice:
    """A user who is present and answers when asked but says nothing unprompted."""
    return Voice(role=role, rate=0.0)


def several_drifts(seed: int) -> Setting:
    """Level IV: one or two drift loops, each with a second person's change on the same, a
    related or an unrelated object, or with the owner's change left in place afterwards. The
    kinds take turns over the seeds. The owner and the second person are present and silent
    unless asked, so the level differs from Level II only in the drifts."""
    draw = random.Random(f"IV:{seed}")
    kind = (*SECOND_KINDS, "carried")[seed % 4]
    carried = CARRIED_FOR if kind == "carried" else 0
    changed = drift_schedule(draw, draw.choice((1, 2)), last=ITEMS - carried)
    if carried:
        # Two carried changes must not overlap.
        changed = changed[:1]
    return Setting(
        level="IV",
        seed=seed,
        items=ITEMS,
        changed=changed,
        users=3,
        voices=(quiet("owner"), quiet("bystander")),
        second=None if kind == "carried" else kind,
        carried=carried,
    )


def user_made_drifts(seed: int, recordings: int) -> Setting:
    """Level V: one drift loop whose change a simulated owner made; `recordings` is how many
    recordings the case has, and the seed picks one."""
    draw = random.Random(f"V:{seed}")
    return Setting(
        level="V",
        seed=seed,
        items=ITEMS,
        changed=drift_schedule(draw, 1),
        users=2,
        voices=(quiet("owner"),),
        generated=seed % recordings,
    )


def single_factor() -> list[Setting]:
    """Level I: every setting of the six single-factor experiments, once each."""
    chosen: dict[str, Setting] = {}
    for settings in EXPERIMENTS.values():
        for setting in settings:
            chosen[setting.id] = setting
    return list(chosen.values())


def level_settings(level: Level, count: int | None = None, recordings: int = 0) -> list[Setting]:
    """The settings of a level for one case, the same for every model."""
    if level == "I":
        return single_factor()
    wanted = count if count is not None else DEFAULT_COUNTS[level]
    if level == "II":
        return [seeded_drifts(seed) for seed in range(wanted)]
    if level == "III":
        return [varied_users(seed) for seed in range(wanted)]
    if level == "IV":
        return [several_drifts(seed) for seed in range(wanted)]
    if recordings == 0:
        return []
    return [user_made_drifts(seed, recordings) for seed in range(wanted)]
