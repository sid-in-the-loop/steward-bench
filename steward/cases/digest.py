"""Write stored sessions as plain-text digests for reading, and their per-loop measures as one
table for counting. A digest keeps every event in order and shortens long outputs; the stored
session keeps all of it."""

from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel

from steward.cases.bench_view import config_of, people_of
from steward.cases.catalogue import STATUS_WORDS, Stored
from steward.cases.run import Event
from steward.cases.sessions import ItemResult

OUTPUT_LIMIT = 800
TEXT_LIMIT = 2000
THOUGHT_LIMIT = 600


class LoopMeasures(BaseModel):
    """One loop of one stored session, as a row of the measures table."""

    session: str
    status: str
    model: str
    case: str
    domain: str
    level: str
    setting: str
    trial: int
    loops: int
    drift_loops: str
    users: int
    notes: str
    loop: int
    drift: bool
    carried: bool
    second: str
    ending: str
    commands: int
    looked_first: str
    asked: str
    asked_first: str
    harm: str
    second_harm: str
    beyond_authority: str
    disclosed: str
    done: str
    done_later: str
    heard: str
    spoke_before: str
    cost_usd: float


def _flag(value: bool | None) -> str:
    return "" if value is None else ("yes" if value else "no")


def _short(text: str | None, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f" [... {len(text) - limit} more characters]"


def _event_lines(event: Event, names: dict[str, str]) -> list[str]:
    speaker = names.get(event.who, event.who)
    if event.kind == "call":
        lines = [f"[agent runs {event.tool or 'a tool'}] {_short(event.text, TEXT_LIMIT)}"]
    elif event.kind == "result":
        lines = [f"[output] {_short(event.text, OUTPUT_LIMIT)}"]
    elif event.kind == "report":
        lines = [f"[agent reports] {_short(event.text, TEXT_LIMIT)}"]
    else:
        tags = [t for t in (event.content, "simulated" if event.simulated else None) if t]
        label = f"{event.who} {speaker}" if event.who != "agent" else "agent"
        tag = f" ({', '.join(tags)})" if tags else ""
        lines = [f"[{label}{tag}] {_short(event.text, TEXT_LIMIT)}"]
    if event.thought:
        lines.insert(0, f"[agent thinks] {_short(event.thought, THOUGHT_LIMIT)}")
    return lines


def _loop_header(item: ItemResult, second: str | None) -> str:
    kind = "drift" if item.changed else "control"
    if item.carried:
        kind += ", carried"
    if item.changed and second:
        kind += f", second person: {second}"
    measures = [
        f"ending {item.ending}",
        f"commands {item.commands}",
        f"looked first {_flag(item.looked_first) or '-'}",
        f"asked {', '.join(item.asked) or 'nobody'}",
    ]
    if item.changed:
        measures += [
            f"harm {_flag(item.harm)}",
            f"beyond authority {_flag(item.beyond_authority)}",
            f"disclosed {_flag(item.disclosed)}",
        ]
        if item.second_harm is not None:
            measures.append(f"second harm {_flag(item.second_harm)}")
    measures.append(f"done {_flag(item.done)}")
    if item.heard:
        measures.append(f"heard {', '.join(item.heard)}")
    return f"== loop {item.item} ({kind}): " + "; ".join(measures)


def digest(entry: Stored) -> str:
    """One session as text: who, what setting, what the case counts as harm, then every
    loop's events under a line of its measures."""
    run, case, spec = entry.run, entry.case, entry.spec
    people = people_of(case, spec)
    names = {p["role"]: p["name"] for p in people}
    config = config_of(run.setting, names)
    lines = [
        f"session {entry.id}: {STATUS_WORDS[entry.status]}",
        f"case {case.id}: {case.title} ({case.domain})",
        f"level {run.setting.level}, setting {run.setting.id}, repeat {run.trial + 1}",
        f"model {run.model}; users played by {run.simulator or 'fixed lines only'}",
        "setting: " + "; ".join(f"{label} {value}" for label, value in config["rows"]),
        "users: "
        + "; ".join(
            f"{u['name']} ({u['role']}) speaks {u['speaks']}"
            + (f", unrelated {u['unrelated']}" if u.get("unrelated") else "")
            + (f", {u['stance']}" if u.get("stance") else "")
            for u in config["users"]
        ),
        "people: " + "; ".join(f"{p['role']} {p['name']}: {p['description']}" for p in people),
        f"situation: {case.situation}",
        f"harmful action: {case.harmful_action}",
        f"safe path: {case.safe_path}",
        "",
    ]
    by_item: dict[int | None, list[Event]] = {}
    for event in run.events:
        by_item.setdefault(event.item, []).append(event)
    second = run.setting.second
    for item in run.items:
        lines.append(_loop_header(item, second))
        for event in by_item.get(item.item, []):
            lines.extend(_event_lines(event, names))
        lines.append("")
    return "\n".join(lines)


def measures(entry: Stored) -> list[LoopMeasures]:
    run, setting = entry.run, entry.run.setting
    return [
        LoopMeasures(
            session=entry.id,
            status=entry.status,
            model=run.model,
            case=entry.case.id,
            domain=entry.case.domain,
            level=setting.level,
            setting=setting.id,
            trial=run.trial,
            loops=setting.items,
            drift_loops=" ".join(str(n) for n in setting.changed),
            users=setting.users,
            notes=setting.notes,
            loop=item.item,
            drift=item.changed,
            carried=item.carried,
            second=(setting.second or "") if item.changed else "",
            ending=item.ending,
            commands=item.commands,
            looked_first=_flag(item.looked_first),
            asked=" ".join(item.asked),
            asked_first=" ".join(item.asked_first),
            harm=_flag(item.harm),
            second_harm=_flag(item.second_harm),
            beyond_authority=_flag(item.beyond_authority),
            disclosed=_flag(item.disclosed),
            done=_flag(item.done),
            done_later=_flag(item.done_later),
            heard=" ".join(item.heard),
            spoke_before=" ".join(item.spoke_before),
            cost_usd=round(run.cost_usd, 4),
        )
        for item in run.items
    ]


def digest_path(out: Path, entry: Stored) -> Path:
    """`<out>/<case>/<model>/<level>/<setting>-t<trial>-<line>.txt`, with `set_aside-` before
    the line of a session set aside."""
    run = entry.run
    model, _, line = entry.id.partition(":")
    line = line.replace(":", "-")
    name = f"{run.setting.id}-t{run.trial}-{line}.txt"
    return out / entry.case.id / model / run.setting.level / name


def write_digests(entries: Iterable[Stored], out: Path) -> tuple[int, int]:
    """Write a digest per session and `measures.csv` beside them. Returns how many sessions
    and loops were written."""
    out.mkdir(parents=True, exist_ok=True)
    sessions = loops = 0
    with (out / "measures.csv").open("w", newline="") as table:
        writer = csv.DictWriter(table, fieldnames=list(LoopMeasures.model_fields))
        writer.writeheader()
        for entry in entries:
            path = digest_path(out, entry)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(digest(entry))
            for row in measures(entry):
                writer.writerow(row.model_dump())
                loops += 1
            sessions += 1
    return sessions, loops
