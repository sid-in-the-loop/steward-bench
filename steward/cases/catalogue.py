"""Every stored session, whatever plan it was run under, with the case it belongs to and
whether it counts in the benchmark as it stands."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from steward.cases.bench import BENCH_MODELS, LEVELS, plan
from steward.cases.case import Case
from steward.cases.session_commands import load_sessions, sessions_path, stored_key
from steward.cases.sessions import SessionRun, SessionSpec
from steward.config import Settings

Status = Literal["current", "replaced", "earlier", "outside"]
STATUS_WORDS: dict[Status, str] = {
    "current": "counts in the benchmark",
    "replaced": "replaced by a later run of the same setting",
    "earlier": "run on an earlier version of the case",
    "outside": "a case that changes during a loop, kept outside the benchmark",
}


@dataclass(frozen=True)
class Stored:
    """One stored session. `id` is `{model}:{line}`, the line of the model's session file it
    is stored on, counted from 1."""

    id: str
    run: SessionRun
    case: Case
    spec: SessionSpec
    status: Status


def former_identifiers(settings: Settings) -> dict[str, str]:
    path = settings.paths.data / "cases" / "former_identifiers.json"
    if not path.exists():
        return {}
    names: dict[str, str] = json.loads(path.read_text())
    return names


def stored_models(settings: Settings) -> list[str]:
    """The models with a session file: the benchmark's first, then any others by name."""
    folder = sessions_path(settings, "_").parent
    found = sorted(path.stem for path in folder.glob("*.jsonl")) if folder.exists() else []
    return [m for m in BENCH_MODELS if m in found] + [m for m in found if m not in BENCH_MODELS]


def _runs(path: Path) -> Iterator[tuple[int, SessionRun]]:
    with path.open() as lines:
        for number, line in enumerate(lines, start=1):
            if line.strip():
                yield number, SessionRun.model_validate_json(line)


def catalogue(settings: Settings, models: list[str] | None = None) -> Iterator[Stored]:
    """Every stored session of the chosen models, in the order it was stored. A session whose
    case has since been renamed is given the case's current identifier; one whose case no
    longer exists is left out."""
    cases = {case.id: (case, spec) for case, spec in load_sessions(settings)}
    renamed = former_identifiers(settings)
    jobs, _ = plan(settings, [(c, s) for c, s in cases.values() if not s.during], list(LEVELS))
    planned = {job.key for job in jobs}
    for model in models or stored_models(settings):
        path = sessions_path(settings, model)
        if not path.exists():
            continue
        latest = {stored_key(run): number for number, run in _runs(path)}
        for number, run in _runs(path):
            name = renamed.get(run.case, run.case)
            if name not in cases:
                continue
            case, spec = cases[name]
            key = stored_key(run)
            status: Status
            if spec.during:
                status = "outside"
            elif key not in planned:
                status = "earlier"
            elif latest[key] != number:
                status = "replaced"
            else:
                status = "current"
            yield Stored(f"{model}:{number}", run, case, spec, status)
