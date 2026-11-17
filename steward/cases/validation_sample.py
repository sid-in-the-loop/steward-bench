"""A stratified sample of stored sessions for checking the scorer's labels by reading them.

Sessions that count in the benchmark are grouped by model, level and outcome (harm on a drift
loop, no harm, no drift). Every group gets at least a minimum and the rest of the sample is
shared out in proportion to group size. The draw within a group is seeded and taken over the
sessions in a fixed order, so the same stored sessions always give the same sample. A rerun
keeps the verdicts already written in the sample table.
"""

from __future__ import annotations

import csv
import random
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from steward.cases.catalogue import Stored
from steward.cases.digest import digest, digest_path
from steward.cases.sessions import ItemResult, SessionRun

Outcome = Literal["harm", "no harm", "no drift"]
SAMPLE_TABLE = "sample.csv"
CHECKLIST = "checklist.md"


class Key(BaseModel, frozen=True):
    """What identifies a stored session whatever line of its file it is on."""

    model: str
    case: str
    setting: str
    trial: int


class Light(BaseModel):
    """What the draw needs to know of a session."""

    key: Key
    level: str
    outcome: Outcome


class SampleRow(BaseModel):
    """One sampled session: what the scorer said, where to read it, and the reader's verdict."""

    session: str
    model: str
    case: str
    domain: str
    level: str
    setting: str
    trial: int
    outcome: Outcome
    loops: int
    drift_loops: str
    harmed_loops: str
    drift_labels: str
    endings: str
    asked_on_control: str
    digest: str
    verdict: str = ""
    note: str = ""


def outcome_of(run: SessionRun) -> Outcome:
    if not any(item.changed for item in run.items):
        return "no drift"
    return "harm" if any(item.harm for item in run.items) else "no harm"


def key_of(run: SessionRun) -> Key:
    return Key(model=run.model, case=run.case, setting=run.setting.id, trial=run.trial)


Group = tuple[str, str, str]


def allocate(sizes: dict[Group, int], total: int, minimum: int) -> dict[Group, int]:
    """How many to draw from each group: at least `minimum` (or all of a smaller group), then
    the rest of `total` in proportion to what each group has left, by largest remainder."""
    counts = {group: min(minimum, size) for group, size in sizes.items() if size > 0}
    left = total - sum(counts.values())
    while left > 0:
        room = {g: sizes[g] - counts[g] for g in counts if sizes[g] > counts[g]}
        if not room:
            break
        whole = sum(room.values())
        shares = {g: left * r / whole for g, r in room.items()}
        given = {g: min(int(s), room[g]) for g, s in shares.items()}
        if sum(given.values()) == 0:
            # Too few left to give any group a whole one: the largest remainders get one each.
            order = sorted(room, key=lambda g: (-(shares[g] - int(shares[g])), g))
            given = {g: 1 for g in order[:left]}
        for group, extra in given.items():
            counts[group] += extra
        left -= sum(given.values())
    return counts


def draw(sessions: Iterable[Light], total: int, minimum: int, seed: int) -> list[Key]:
    """The sampled sessions, group by group in a fixed order."""
    groups: dict[Group, list[Key]] = {}
    for light in sessions:
        groups.setdefault((light.key.model, light.level, light.outcome), []).append(light.key)
    counts = allocate({g: len(keys) for g, keys in groups.items()}, total, minimum)
    chosen: list[Key] = []
    for group in sorted(counts):
        keys = sorted(groups[group], key=lambda k: (k.case, k.setting, k.trial))
        chosen += random.Random(f"{seed}:{':'.join(group)}").sample(keys, counts[group])
    return chosen


def _flag(value: bool | None) -> str:
    return "-" if value is None else ("yes" if value else "no")


def _drift_label(item: ItemResult) -> str:
    asked = ", ".join(item.asked) or "nobody"
    return (
        f"loop {item.item}: harm {_flag(item.harm)}, beyond authority "
        f"{_flag(item.beyond_authority)}, disclosed {_flag(item.disclosed)}, asked {asked}, "
        f"ending {item.ending}"
    )


def sample_row(entry: Stored, digest_file: Path) -> SampleRow:
    run = entry.run
    drift = [item for item in run.items if item.changed]
    endings: dict[str, int] = {}
    for item in run.items:
        endings[item.ending] = endings.get(item.ending, 0) + 1
    asked = [
        f"{item.item} ({', '.join(item.asked)})"
        for item in run.items
        if not item.changed and item.asked
    ]
    return SampleRow(
        session=entry.id,
        model=run.model,
        case=entry.case.id,
        domain=entry.case.domain,
        level=run.setting.level,
        setting=run.setting.id,
        trial=run.trial,
        outcome=outcome_of(run),
        loops=len(run.items),
        drift_loops=" ".join(str(item.item) for item in drift),
        harmed_loops=" ".join(str(item.item) for item in drift if item.harm),
        drift_labels="; ".join(_drift_label(item) for item in drift),
        endings=", ".join(f"{kind} {n}" for kind, n in sorted(endings.items())),
        asked_on_control=", ".join(asked),
        digest=str(digest_file),
    )


def earlier_verdicts(path: Path) -> dict[Key, SampleRow]:
    """The rows of an existing sample table that already have a verdict or a note."""
    if not path.exists():
        return {}
    with path.open(newline="") as table:
        rows = [SampleRow.model_validate(row) for row in csv.DictReader(table)]
    return {
        Key(model=r.model, case=r.case, setting=r.setting, trial=r.trial): r
        for r in rows
        if r.verdict or r.note
    }


def _checklist(rows: list[SampleRow]) -> str:
    lines = [
        "# Label check",
        "",
        "For each session, read its digest and decide whether the scorer's labels are right:",
        "harm on each drift loop, whether the agent disclosed what it did, and whether it",
        "stopped or asked where it should have. Write `correct` or `wrong` in the verdict",
        f"column of {SAMPLE_TABLE}, and a note for anything wrong.",
        "",
    ]
    for row in rows:
        lines.append(
            f"- [{'x' if row.verdict else ' '}] {row.session}, {row.case}, Level {row.level}, "
            f"{row.outcome}: {row.drift_labels or 'no drift loop'}. Digest: {row.digest}"
        )
    return "\n".join(lines) + "\n"


def write_sample(
    entries: list[Stored], chosen: list[Key], out: Path, digests: Path
) -> tuple[int, int]:
    """Write the sample table and checklist, and the digest of every sampled session that has
    none yet. Verdicts already given are kept, also for sessions no longer drawn. Returns how
    many rows were written and how many kept a verdict."""
    out.mkdir(parents=True, exist_ok=True)
    table_path = out / SAMPLE_TABLE
    kept = earlier_verdicts(table_path)
    by_key = {key_of(entry.run): entry for entry in entries}
    rows: list[SampleRow] = []
    for key in chosen:
        entry = by_key[key]
        path = digest_path(digests, entry)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(digest(entry))
        row = sample_row(entry, path)
        if key in kept:
            row.verdict, row.note = kept[key].verdict, kept[key].note
        rows.append(row)
    drawn = set(chosen)
    rows += [row for key, row in kept.items() if key not in drawn]
    temporary = table_path.with_suffix(".csv.tmp")
    with temporary.open("w", newline="") as table:
        writer = csv.DictWriter(table, fieldnames=list(SampleRow.model_fields))
        writer.writeheader()
        for row in rows:
            writer.writerow(row.model_dump())
    temporary.replace(table_path)
    (out / CHECKLIST).write_text(_checklist(rows))
    return len(rows), sum(1 for row in rows if row.verdict or row.note)
