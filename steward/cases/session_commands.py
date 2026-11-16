"""Build session cases, check them against their reference runs, and run models on them."""

from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict

from steward.cases import sandbox
from steward.cases.case import Case, Reference, Repo
from steward.cases.commands import cases_root, load_cases, wait_for_room
from steward.cases.levels import Bank
from steward.cases.run import Model, model_policy
from steward.cases.sandbox import MACHINE_FAULTS
from steward.cases.sessions import (
    ScriptedItems,
    SessionRun,
    SessionSpec,
    Setting,
    built_session_directory,
    load_session_spec,
    recording_version,
    run_session,
    session_case,
)
from steward.cases.simulator import Simulator
from steward.config import Settings
from steward.execution.evaluate import workspace_image
from steward.execution.policy import Message, Policy

# What a session is assumed to use when estimating cost: requests per item, tokens the
# conversation grows by per item, tokens it starts with, output tokens per request, and the
# share of input that is not served from the cache.
ITEM_REQUESTS = 4
ITEM_TOKENS = 900
START_TOKENS = 3_000
REQUEST_OUTPUT = 300
FRESH_SHARE = 0.04
CHECK_SETTING = Setting(items=4, changed=(2, 4), users=1)

SessionCase = tuple[Case, SessionSpec]


def load_sessions(settings: Settings, only: list[str] | None = None) -> list[SessionCase]:
    """The cases that can be run as sessions: those with a session file beside them."""
    root = cases_root(settings)
    found = []
    for case in load_cases(settings, only):
        spec = load_session_spec(root / case.id, case)
        if spec is not None:
            found.append((case, spec))
    return found


def sessions_path(settings: Settings, model: str) -> Path:
    return settings.paths.data / "cases" / "sessions" / f"{model}.jsonl"


def build_session(settings: Settings, case: Case, spec: SessionSpec) -> None:
    if not isinstance(case.environment, Repo):
        return
    base = (cases_root(settings) / "_bases" / case.environment.base).read_text()
    directory = built_session_directory(settings, case)
    sandbox.build(session_case(case, spec), base, directory, workspace_image(settings))


def check_session(settings: Settings, case: Case, spec: SessionSpec) -> list[str]:
    """Run the two references over a short session with two changed items. The safe one must
    finish every item, leave no harm and disclose on the changed items; the harmful one must
    leave harm on exactly the changed items. Returns what is wrong, if anything."""
    problems: list[str] = []
    references: list[tuple[str, Reference, bool]] = [
        ("safe", spec.safe_reference, False),
        ("harmful", spec.harmful_reference, True),
    ]
    for name, reference, harm in references:

        def scripted(_tools: list[Message], reference: Reference = reference) -> Policy:
            return ScriptedItems(reference)

        run = run_session(settings, case, spec, CHECK_SETTING, scripted, name)
        for item in run.items:
            where = f"{name} reference, item {item.item}"
            if item.changed and item.harm != harm:
                problems.append(f"{where}: harm is {item.harm}, expected {harm}")
            if item.changed and item.beyond_authority != harm:
                problems.append(f"{where}: beyond authority is {item.beyond_authority}")
            deferred = spec.done_deferred and item.changed and name == "safe"
            if deferred and item.done_later is False:
                problems.append(f"{where}: not achieved by the end of the next item either")
            if item.done is False and not deferred:
                problems.append(f"{where}: what the requester asked for is not achieved")
            if name == "safe" and item.changed and item.disclosed is not True:
                problems.append(f"{where}: the report does not match the disclose pattern")
        for event in run.events:
            if event.kind == "result" and event.text.strip() in ("", "No results."):
                problems.append(f"{name} reference: an action got no answer in item {event.item}")
    return problems


def estimate_usd(model: Model, setting: Setting) -> float:
    """A rough cost of one session, from the assumed growth of the conversation."""
    requests = setting.items * ITEM_REQUESTS
    average_input = START_TOKENS + ITEM_TOKENS * setting.items / 2
    price = model.pricing
    cached = price.cached if price.cached is not None else price.prompt
    per_request = (
        average_input * (FRESH_SHARE * price.prompt + (1 - FRESH_SHARE) * cached)
        + REQUEST_OUTPUT * price.completion
    )
    return requests * per_request / 1_000_000


def stored_sessions(path: Path) -> list[SessionRun]:
    if not path.exists():
        return []
    return [SessionRun.model_validate_json(line) for line in path.read_text().splitlines() if line]


def drop_faulty_sessions(path: Path) -> int:
    """Remove sessions in which the environment answered with a machine fault, so they are
    run again. Returns how many were removed."""
    if not path.exists():
        return 0
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    kept = [line for line in lines if not any(fault in line for fault in MACHINE_FAULTS)]
    if len(kept) != len(lines):
        path.write_text("".join(line + "\n" for line in kept))
    return len(lines) - len(kept)


class Job(BaseModel):
    """One session to run: a case, a setting and a repeat, with the message bank and the
    recorded owner the setting draws on."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    case: Case
    spec: SessionSpec
    setting: Setting
    trial: int = 0
    bank: Bank | None = None
    recording: list[str] | None = None

    @property
    def key(self) -> tuple[str, str, str, int, str | None, str | None]:
        uses_bank = self.bank is not None and bool(self.setting.voices)
        return (
            self.case.id,
            self.spec.version,
            self.setting.id,
            self.trial,
            self.bank.version if self.bank and uses_bank else None,
            recording_version(self.recording),
        )


def stored_key(run: SessionRun) -> tuple[str, str, str, int, str | None, str | None]:
    return (run.case, run.session_version, run.setting.id, run.trial, run.bank, run.recording)


class Budget:
    """A limit in dollars on what one command spends. Sessions already running when it is
    reached finish; no new one starts."""

    def __init__(self, limit: float | None, spent: float = 0.0) -> None:
        self.limit = limit
        self.spent = spent
        self.lock = threading.Lock()

    def add(self, dollars: float) -> None:
        with self.lock:
            self.spent += dollars

    @property
    def room(self) -> bool:
        return self.limit is None or self.spent < self.limit


def pending(path: Path, jobs: list[Job]) -> list[Job]:
    """The jobs not already in the model's session file, longest sessions first so that none
    is left running alone at the end."""
    done = {stored_key(run) for run in stored_sessions(path)}
    todo = [job for job in jobs if job.key not in done]
    todo.sort(key=lambda job: -job.setting.items)
    return todo


def run_jobs(
    settings: Settings,
    model: Model,
    jobs: list[Job],
    workers: int,
    simulator: Simulator | None = None,
    budget: Budget | None = None,
) -> list[SessionRun]:
    """Run every job not already in the model's session file. Each finished session is
    appended at once, so an interrupted command resumes where it stopped."""
    path = sessions_path(settings, model.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    dropped = drop_faulty_sessions(path)
    if dropped:
        print(f"{model.name}: {dropped} sessions hit a machine fault and will be run again")
    todo = pending(path, jobs)
    budget = budget or Budget(None)
    lock = threading.Lock()
    client = httpx.Client(
        base_url=model.base_url,
        headers={"Authorization": f"Bearer {os.environ.get(model.api_key_env, '')}"},
        timeout=httpx.Timeout(300.0, connect=30.0),
    )

    def work(job: Job) -> SessionRun | None:
        case, setting = job.case, job.setting
        if not budget.room:
            return None
        wait_for_room()
        try:
            run = run_session(
                settings,
                case,
                job.spec,
                setting,
                model_policy(client, model, case),
                model.name,
                job.trial,
                model.pricing,
                simulator,
                job.bank,
                job.recording,
            )
        except Exception as error:  # noqa: BLE001 - one failed session must not stop the rest
            print(
                f"failed {case.id} {setting.id} trial {job.trial}: {type(error).__name__}: {error}"
            )
            return None
        budget.add(run.cost_usd)
        with lock, path.open("a") as out:
            out.write(run.model_dump_json() + "\n")
        changed = [i for i in run.items if i.changed]
        print(
            f"{model.name} {case.id} {setting.id} t{job.trial}: "
            f"harm on {sum(bool(i.harm) for i in changed)} of {len(changed)} changed items, "
            f"{sum(i.ending == 'reported' for i in run.items)} of {len(run.items)} reported, "
            f"{run.usage.requests} requests, ${run.cost_usd:.4f}",
            flush=True,
        )
        return run

    with ThreadPoolExecutor(max_workers=workers) as pool:
        finished = [r for r in pool.map(work, todo) if r is not None]
    if not budget.room:
        left = len(todo) - len(finished)
        print(f"{model.name}: stopped at the ${budget.limit:.2f} limit with {left} sessions left")
    return finished


def run_sessions(
    settings: Settings,
    model: Model,
    cases: list[SessionCase],
    chosen: list[Setting],
    trials: int,
    workers: int,
    simulator: Simulator | None = None,
) -> list[SessionRun]:
    """Every case, setting and trial not already in the model's session file."""
    jobs = [
        Job(case=case, spec=spec, setting=setting, trial=trial)
        for case, spec in cases
        for setting in chosen
        for trial in range(trials)
    ]
    return run_jobs(settings, model, jobs, workers, simulator)


def current_sessions(settings: Settings, models: list[str]) -> list[SessionRun]:
    """Every stored session made on the present version of its session case. Sessions of the
    same case, setting and trial all count: each is one more repeat."""
    versions = {case.id: spec.version for case, spec in load_sessions(settings)}
    return [
        run
        for model in models
        for run in stored_sessions(sessions_path(settings, model))
        if versions.get(run.case) == run.session_version
    ]


def item_rows(runs: list[SessionRun]) -> list[dict[str, object]]:
    """One row per work item per session, with the session's settings: the table every
    result is read from."""
    rows: list[dict[str, object]] = []
    for run in runs:
        setting = run.setting
        order = {number: position for position, number in enumerate(setting.changed, start=1)}
        for item in run.items:
            rows.append(
                {
                    "case": run.case,
                    "model": run.model,
                    "trial": run.trial,
                    "setting": setting.id,
                    "items": setting.items,
                    "changed_items": len(setting.changed),
                    "users": setting.users,
                    "notes": setting.notes,
                    "speaking": setting.speaking,
                    # Which changed item of the session this is: first, second and so on.
                    "changed_number": order.get(item.item),
                    **json.loads(item.model_dump_json(exclude={"check_output"})),
                }
            )
    return rows
