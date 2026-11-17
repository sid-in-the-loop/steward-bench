"""Plan and run the benchmark: every case at every level, for every model, under one limit."""

from __future__ import annotations

import json
import statistics
import threading
from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel

from steward.cases.case import DOMAIN_NAMES, Domain
from steward.cases.commands import cases_root
from steward.cases.level_settings import level_settings
from steward.cases.levels import Level, load_bank
from steward.cases.run import Model
from steward.cases.session_commands import (
    Budget,
    Job,
    SessionCase,
    estimate_usd,
    load_sessions,
    pending,
    run_jobs,
    sessions_path,
    stored_sessions,
)
from steward.cases.sessions import BASELINE, SessionRun, SessionSpec, Setting
from steward.cases.simulator import Simulator
from steward.cases.throttle import Deadline
from steward.config import Settings

LEVELS: tuple[Level, ...] = ("I", "II", "III", "IV", "V")
BENCH_MODELS = ("gpt-6-luna", "gpt-5.6-luna", "claude-haiku-5-5")
# Level I repeats each setting; the drawn levels have many settings and run each once.
LEVEL_ONE_TRIALS = 2
RECORDINGS = "recordings"
# What a session at Level III and above is assumed to cost against one at Level I of the
# same length, for the longer conversation its users make.
DRAWN_USERS_FACTOR = 1.3


def bench_cases(settings: Settings, only: list[str] | None = None) -> list[SessionCase]:
    """The cases of the benchmark: every session case whose world changes only between loops.
    A case with a `during` script changes while the agent works and is left out."""
    return [(case, spec) for case, spec in load_sessions(settings, only) if not spec.during]


def load_recordings(directory: Path) -> list[list[str]]:
    """The recorded owners of a case, in order: each a list of commands."""
    folder = directory / RECORDINGS
    if not folder.is_dir():
        return []
    return [json.loads(path.read_text())["commands"] for path in sorted(folder.glob("*.json"))]


def supports(spec: SessionSpec, setting: Setting) -> bool:
    """Whether a case provides what a setting needs."""
    if setting.second and (spec.second is None or setting.second not in spec.second.kinds):
        return False
    if setting.carried and not spec.carries:
        return False
    return setting.generated is None or spec.generated is not None


def plan(
    settings: Settings,
    cases: list[SessionCase],
    levels: list[Level],
    counts: dict[Level, int] | None = None,
) -> tuple[list[Job], list[str]]:
    """Every session of the chosen levels, and what was left out and why. `counts` gives the
    number of settings per case at a drawn level, in place of its default."""
    jobs: list[Job] = []
    left_out: list[str] = []
    root = cases_root(settings)
    for case, spec in cases:
        bank = load_bank(root / case.id)
        recordings = load_recordings(root / case.id)
        for level in levels:
            drawn = level_settings(level, (counts or {}).get(level), len(recordings))
            usable = [s for s in drawn if supports(spec, s)]
            if level == "III" and bank is None:
                usable = []
            if bank is not None and not bank.exchanges:
                usable = [s for s in usable if not s.activity]
            if not usable:
                left_out.append(f"{case.id}: nothing to run at Level {level}")
                continue
            for setting in usable:
                recording = recordings[setting.generated] if setting.generated is not None else None
                for trial in range(LEVEL_ONE_TRIALS if level == "I" else 1):
                    jobs.append(
                        Job(
                            case=case,
                            spec=spec,
                            setting=setting,
                            trial=trial,
                            bank=bank,
                            recording=recording,
                        )
                    )
    return jobs, left_out


def measured_cost(runs: list[SessionRun]) -> dict[int, float]:
    """The mean cost of a model's stored sessions, by number of loops."""
    by_items: dict[int, list[float]] = defaultdict(list)
    for run in runs:
        by_items[run.setting.items].append(run.cost_usd)
    return {items: statistics.mean(costs) for items, costs in by_items.items()}


def expected_cost(model: Model, job: Job, measured: dict[int, float]) -> float:
    """What a session is expected to cost: the mean of the model's stored sessions of that
    length where there are some, otherwise the rough estimate."""
    setting = job.setting
    base = measured.get(setting.items, estimate_usd(model, setting))
    return base * (DRAWN_USERS_FACTOR if setting.level in ("III", "IV", "V") else 1.0)


def parse_counts(given: list[str] | None) -> dict[Level, int]:
    """Counts written as LEVEL=N, such as III=20; a bare N applies to every drawn level."""
    counts: dict[Level, int] = {}
    for entry in given or []:
        level, _, number = entry.rpartition("=")
        targets: list[Level] = [level] if level else list(LEVELS[1:])  # type: ignore[list-item]
        if any(target not in LEVELS[1:] for target in targets) or not number.isdigit():
            raise ValueError(f"a count is LEVEL=N for a drawn level, or N; not {entry!r}")
        for target in targets:
            counts[target] = int(number)
    return counts


class Line(BaseModel):
    """One row of the plan: a model and a level."""

    model: str
    level: Level
    planned: int
    pending: int
    usd: float


def summarise(settings: Settings, models: list[Model], jobs: list[Job]) -> list[Line]:
    lines = []
    for model in models:
        path = sessions_path(settings, model.name)
        measured = measured_cost(stored_sessions(path))
        todo = pending(path, jobs)
        for level in LEVELS:
            planned = [job for job in jobs if job.setting.level == level]
            left = [job for job in todo if job.setting.level == level]
            if planned:
                usd = sum(expected_cost(model, job, measured) for job in left)
                lines.append(
                    Line(
                        model=model.name,
                        level=level,
                        planned=len(planned),
                        pending=len(left),
                        usd=usd,
                    )
                )
    return lines


def pilot_jobs(jobs: list[Job]) -> list[Job]:
    """A small run over everything: for each domain's first case, the baseline at Level I and
    the first setting of every other level it has."""
    first_case: dict[Domain, str] = {}
    for job in jobs:
        first_case.setdefault(job.case.domain, job.case.id)
    chosen: dict[tuple[str, Level], Job] = {}
    for job in jobs:
        if job.case.id != first_case[job.case.domain] or job.trial != 0:
            continue
        setting = job.setting
        if setting.level == "I" and setting.id != BASELINE.id:
            continue
        chosen.setdefault((job.case.id, setting.level), job)
    return list(chosen.values())


def problems(run: SessionRun) -> list[str]:
    """What keeps a stored session from being a complete record, if anything."""
    found = []
    if len(run.items) != run.setting.items:
        found.append(f"{len(run.items)} of {run.setting.items} loops recorded")
    for item in run.items:
        if item.changed and (item.harm is None or "harm" not in item.check_output):
            found.append(f"loop {item.item}: no harm check")
        if item.done is None:
            found.append(f"loop {item.item}: no done check")
    recorded = {event.item for event in run.events}
    missing = [item.item for item in run.items if item.item not in recorded]
    if missing:
        found.append(f"no events for loops {missing}")
    if run.setting.voices and run.bank is None and any(v.rate > 0 for v in run.setting.voices):
        found.append("no message bank version")
    return found


class PilotSummary(BaseModel):
    """What a pilot measured, kept so that a full run can require one."""

    sessions: int
    complete: int
    usd: float
    usd_by_model: dict[str, float]
    cached_share_by_model: dict[str, float]
    by_level: dict[str, int]
    by_domain: dict[str, int]


def spend_limit_path(settings: Settings) -> Path:
    """A file holding one number: the most every stored session together may have cost, set
    aside ones included. Each job of a chain or relay reads it before it starts."""
    return settings.paths.data / "cases" / "spend_limit_usd"


def spent_usd(settings: Settings) -> float:
    """What every stored session has cost, set aside ones included, from their records."""
    folder = sessions_path(settings, "_").parent
    total = 0.0
    for path in [*folder.glob("*.jsonl"), *folder.glob("set_aside/*.jsonl")]:
        for line in path.read_text().splitlines(keepends=True):
            if line.strip() and line.endswith("\n"):
                total += json.loads(line)["cost_usd"]
    return total


def pilot_path(settings: Settings) -> Path:
    return settings.paths.data / "cases" / "pilot.json"


def summarise_pilot(runs: list[SessionRun], domains: dict[str, Domain]) -> PilotSummary:
    usd: dict[str, float] = defaultdict(float)
    prompt: dict[str, int] = defaultdict(int)
    cached: dict[str, int] = defaultdict(int)
    by_level: dict[str, int] = defaultdict(int)
    by_domain: dict[str, int] = defaultdict(int)
    for run in runs:
        usd[run.model] += run.cost_usd
        prompt[run.model] += run.usage.prompt_tokens
        cached[run.model] += run.usage.cached_tokens
        by_level[run.setting.level] += 1
        by_domain[DOMAIN_NAMES[domains[run.case]]] += 1
    return PilotSummary(
        sessions=len(runs),
        complete=sum(not problems(run) for run in runs),
        usd=sum(usd.values()),
        usd_by_model=dict(usd),
        cached_share_by_model={m: cached[m] / prompt[m] if prompt[m] else 0.0 for m in prompt},
        by_level=dict(by_level),
        by_domain=dict(by_domain),
    )


def parse_workers(given: list[str] | None, models: list[str], default: int) -> dict[str, int]:
    """The most sessions at once per model, from entries N (every model) or MODEL=N."""
    workers = dict.fromkeys(models, default)
    for entry in given or []:
        name, _, number = entry.rpartition("=")
        if not number.isdigit() or int(number) < 1 or (name and name not in workers):
            raise ValueError(f"workers are N or MODEL=N for a model being run; not {entry!r}")
        for target in [name] if name else models:
            workers[target] = int(number)
    return workers


def run_bench(
    settings: Settings,
    models: list[Model],
    jobs: list[Job],
    workers: dict[str, int],
    limit: float | None,
    simulator: Simulator | None = None,
    start: int | None = None,
    minutes: float | None = None,
) -> list[SessionRun]:
    """Run the jobs for every model at once, each model with its own sessions at once, all
    under one limit in dollars and one deadline."""
    budget = Budget(limit)
    deadline = Deadline.after(minutes)
    finished: list[SessionRun] = []
    lock = threading.Lock()

    def one(model: Model) -> None:
        most = workers[model.name]
        first = min(start, most) if start else most
        runs = run_jobs(settings, model, jobs, most, simulator, budget, first, deadline)
        with lock:
            finished.extend(runs)

    threads = [threading.Thread(target=one, args=(model,)) for model in models]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return finished
