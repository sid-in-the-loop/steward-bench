"""Take stored sessions out of a model's session file, so that the next run makes them again,
and keep them beside it. Used for sessions that a fault in the runner made wrong: the first
was the question routed to the requester because the agent wrote its addressee's full name
or set the name in markdown emphasis (fixed in `addresses`)."""

from __future__ import annotations

import os
import re
from collections import Counter

from steward.cases.case import Case
from steward.cases.catalogue import former_identifiers, stored_models
from steward.cases.session_commands import load_sessions, sessions_path
from steward.cases.sessions import SessionRun, SessionSpec, addresses
from steward.config import Settings


def addressed_before_fix(message: str, first_name: str) -> bool:
    """The matcher the runner used before the fix: the first name alone, with nothing
    between it and the punctuation."""
    name = re.escape(first_name)
    opens = rf"(^|[.!?\n]\s*)(hi |hey |hello )?{name}\s*[,:\u2014-]"
    closes = rf",\s*{name}\s*[.!?]"
    return re.search(rf"{opens}|@{name}\b|{closes}", message, re.IGNORECASE) is not None


def misrouted(run: SessionRun, case: Case, spec: SessionSpec) -> bool:
    """Whether the agent addressed someone other than the requester in a way the runner
    before the fix did not recognise, so that it gave the question to the requester."""
    people = [p for p in [*case.people, spec.manager] if p.role != "requester"]
    for event in run.events:
        if event.who != "agent" or event.kind != "message":
            continue
        for person in people:
            first = person.name.split()[0]
            if addresses(event.text, person.name) and not addressed_before_fix(event.text, first):
                return True
    return False


def set_aside_misrouted(settings: Settings, models: list[str] | None = None) -> Counter[str]:
    """Move every stored session with a misrouted question from `<model>.jsonl` to
    `set_aside/<model>.jsonl` beside it, appending. Returns how many were moved per model.
    Run it only while no run is writing the session files."""
    cases = {case.id: (case, spec) for case, spec in load_sessions(settings)}
    renamed = former_identifiers(settings)
    moved: Counter[str] = Counter()
    for model in models or stored_models(settings):
        path = sessions_path(settings, model)
        kept: list[str] = []
        aside: list[str] = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            run = SessionRun.model_validate_json(line)
            found = cases.get(renamed.get(run.case, run.case))
            (aside if found and misrouted(run, *found) else kept).append(line)
        if not aside:
            continue
        folder = path.parent / "set_aside"
        folder.mkdir(exist_ok=True)
        with (folder / path.name).open("a") as out:
            out.write("".join(line + "\n" for line in aside))
        partial = path.with_suffix(".jsonl.partial")
        partial.write_text("".join(line + "\n" for line in kept))
        os.replace(partial, path)
        moved[model] = len(aside)
    return moved
