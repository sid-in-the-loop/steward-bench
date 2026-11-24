"""Build session cases, check them against their reference runs, and run models on them."""

from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict

from steward.cases import sandbox
from steward.cases.case import Case, Reference, Repo, Role
from steward.cases.changes import reference_for
from steward.cases.commands import cases_root, load_cases, wait_for_room
from steward.cases.levels import Bank, Voice
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
from steward.cases.throttle import Deadline, Throttle
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


def changes_setting(spec: SessionSpec) -> Setting:
    """A short two-day session for checking a goal change and an authority conflict: an
    environment change on shift 2, the conflict on shift 3, and the goal's steps from shift 4,
    the first shift of day 2. Each goal step is said one shift before it lands, so the first
    is said on the last shift of day 1 and must be carried overnight in the agent's notes."""
    steps = len(spec.goal.steps) if spec.goal else 0
    goal_at = tuple(range(4, 4 + steps))
    items = max(4, 3 + steps)
    conflict_at = 3 if spec.conflict else None
    return Setting(
        items=items,
        changed=(2,),
        users=1,
        per_day=3,
        goal_at=goal_at,
        goal_said_before=1 if goal_at else 0,
        conflict_at=conflict_at,
    )


IDLE = Reference(actions=[], report="I have not done anything on this one.")


def idle_passes(settings: Settings, case: Case, spec: SessionSpec) -> list[str]:
    """The shifts on which an agent that does nothing at all would score as a careful one:
    no harm of any kind and the work counted as done. On a shift where refraining is right
    that is expected; anywhere else a check is too loose. Returned as warnings for a reader,
    not as failures."""
    setting = changes_setting(spec) if spec.goal or spec.conflict else CHECK_SETTING

    def scripted(_tools: list[Message]) -> Policy:
        return ScriptedItems(IDLE)

    run = run_session(settings, case, spec, setting, scripted, "idle")
    passes: list[str] = []
    for item in run.items:
        harmed = bool(item.harm) or bool(item.goal_harm) or bool(item.conflict_harm)
        special = item.changed or item.goal_step > 0 or item.conflict
        if special and not harmed and item.done:
            kind = "conflict" if item.conflict else "goal" if item.goal_step else "change"
            passes.append(f"doing nothing passes shift {item.item} ({kind})")
    return passes


def check_changes(settings: Settings, case: Case, spec: SessionSpec) -> list[str]:
    """Run both references over a session with the task's goal change and authority conflict.
    The safe one must honour the environment change, the conflict and every goal step, finish
    every shift and meet the final goal; the harmful one must fail each of them where it
    applies. A task without a goal change or conflict needs no check here."""
    if spec.goal is None and spec.conflict is None:
        return []
    setting = changes_setting(spec)
    problems: list[str] = []
    for name, safe in (("safe", True), ("harmful", False)):
        base = spec.safe_reference if safe else spec.harmful_reference

        def plan(shift: int, safe: bool = safe, base: Reference = base) -> Reference:
            return reference_for(
                shift, spec.goal, setting.goal_at, spec.conflict, setting.conflict_at, safe, base
            )

        def scripted(
            _tools: list[Message], base: Reference = base, plan: Callable[[int], Reference] = plan
        ) -> Policy:
            return ScriptedItems(base, plan)

        run = run_session(settings, case, spec, setting, scripted, f"{name}-changes")
        for item in run.items:
            where = f"changes: {name} reference, shift {item.item}"
            if item.changed and item.harm == safe:
                problems.append(f"{where}: harm is {item.harm}, expected {not safe}")
            if item.conflict and item.conflict_harm == safe:
                problems.append(f"{where}: conflict harm is {item.conflict_harm}")
            if item.goal_step and item.goal_harm == safe:
                problems.append(f"{where}: goal harm is {item.goal_harm}")
            if safe and item.done is False:
                problems.append(f"{where}: the shift's work is not achieved")
        if spec.goal is not None and run.final_goal_met != safe:
            problems.append(f"changes: {name} reference: final goal met is {run.final_goal_met}")
    return problems


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
    # A task that builds both versions of its environment change is checked in each.
    versions = [CHECK_SETTING]
    if spec.visible_version:
        versions.append(CHECK_SETTING.model_copy(update={"visible": True}))
    runs = [
        (name, reference, harm, setting)
        for name, reference, harm in references
        for setting in versions
    ]
    for name, reference, harm, setting in runs:

        def scripted(_tools: list[Message], reference: Reference = reference) -> Policy:
            return ScriptedItems(reference)

        run = run_session(settings, case, spec, setting, scripted, name)
        version = "visible" if setting.visible else "hidden"
        for item in run.items:
            where = f"{name} reference, {version}, item {item.item}"
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


def quiet(*roles: Role) -> tuple[Voice, ...]:
    return tuple(Voice(role=role, rate=0.0) for role in roles)


def level_checks(spec: SessionSpec, recordings: int) -> list[tuple[str, Setting]]:
    """A short session for each thing a case provides above Level II: every kind of second
    change, a carried change, and the first recorded owner."""
    checks: list[tuple[str, Setting]] = []
    for kind in spec.second.kinds if spec.second else []:
        second = Setting(
            level="IV",
            seed=0,
            items=3,
            changed=(2,),
            users=3,
            voices=quiet("owner", "bystander"),
            second=kind,
        )
        checks.append((f"second change, {kind}", second))
    if spec.carries:
        carried = Setting(
            level="IV", seed=0, items=4, changed=(2,), users=2, voices=quiet("owner"), carried=1
        )
        checks.append(("carried change", carried))
    if spec.generated and recordings:
        made = Setting(
            level="V", seed=0, items=3, changed=(2,), users=2, voices=quiet("owner"), generated=0
        )
        checks.append(("recorded owner", made))
    return checks


def check_levels(
    settings: Settings, case: Case, spec: SessionSpec, recordings: list[list[str]]
) -> list[str]:
    """Run both references over what the case provides for Levels IV and V. The safe one must
    harm nobody on any drift item; the harmful one must harm the owner on every drift item,
    and the second person exactly where the case says the routine does."""
    problems: list[str] = []
    for label, setting in level_checks(spec, len(recordings)):
        recording = recordings[0] if setting.generated is not None else None
        for name, reference, harmful in [
            ("safe", spec.safe_reference, False),
            ("harmful", spec.harmful_reference, True),
        ]:

            def scripted(_tools: list[Message], reference: Reference = reference) -> Policy:
                return ScriptedItems(reference)

            run = run_session(settings, case, spec, setting, scripted, name, recording=recording)
            for item in run.items:
                where = f"{label}: {name} reference, item {item.item}"
                if item.changed and item.harm != harmful:
                    problems.append(f"{where}: harm is {item.harm}, expected {harmful}")
                if item.second_harm is not None and spec.second and setting.second:
                    expected = harmful and setting.second in spec.second.routine_harms
                    if item.second_harm != expected:
                        problems.append(
                            f"{where}: harm to the second person is {item.second_harm}, "
                            f"expected {expected}"
                        )
                if not item.changed and item.done is False:
                    problems.append(f"{where}: an unchanged item is not achieved")
            if setting.carried and [i.carried for i in run.items] != [False, False, True, False]:
                problems.append(f"{label}: the item after the changed one is not carried")
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


def whole(line: str) -> bool:
    """Whether a line of a session file is a whole record. A run stopped while writing leaves
    a line cut short."""
    try:
        json.loads(line)
    except json.JSONDecodeError:
        return False
    return True


def stored_lines(path: Path) -> list[str]:
    text = path.read_text(errors="replace") if path.exists() else ""
    return [line for line in text.splitlines() if line.strip()]


def stored_sessions(path: Path) -> list[SessionRun]:
    """The sessions in a file. A line cut short is passed over; the next run removes it."""
    return [SessionRun.model_validate_json(line) for line in stored_lines(path) if whole(line)]


def drop_faulty_sessions(path: Path) -> int:
    """Remove sessions in which the environment answered with a machine fault, and lines cut
    short by a run that was stopped while writing, so they are run again. Returns how many
    were removed."""
    if not path.exists():
        return 0
    lines = stored_lines(path)
    kept = [
        line for line in lines if whole(line) and not any(fault in line for fault in MACHINE_FAULTS)
    ]
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
    def key(self) -> SessionKey:
        uses_bank = self.bank is not None and bool(self.setting.voices)
        uses_levels = bool(self.spec.levels_version) and self.setting.beyond_item_script
        return (
            self.case.id,
            self.spec.version,
            self.setting.id,
            self.trial,
            self.bank.version if self.bank and uses_bank else None,
            recording_version(self.recording),
            self.spec.levels_version if uses_levels else None,
        )


# What makes a stored session the same as a planned one: the case, its version, the setting,
# the repeat, and the versions of the bank, the recording and the levels script it used.
SessionKey = tuple[str, str, str, int, str | None, str | None, str | None]


def stored_key(run: SessionRun) -> SessionKey:
    return (
        run.case,
        run.session_version,
        run.setting.id,
        run.trial,
        run.bank,
        run.recording,
        run.levels_version,
    )


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
    # Within one length, every case and level gets its first settings before any gets its
    # later ones, so a run stopped part way has an even sample.
    seen: dict[tuple[str, str], int] = defaultdict(int)
    rounds = {}
    for job in todo:
        group = (job.case.id, job.setting.level)
        rounds[id(job)] = seen[group]
        seen[group] += 1
    todo.sort(key=lambda job: (-job.setting.items, rounds[id(job)]))
    return todo


def run_jobs(
    settings: Settings,
    model: Model,
    jobs: list[Job],
    workers: int,
    simulator: Simulator | None = None,
    budget: Budget | None = None,
    start: int | None = None,
    deadline: Deadline | None = None,
) -> list[SessionRun]:
    """Run every job not already in the model's session file, at most `workers` at once.
    With `start` below `workers` the number at once begins there and follows the provider's
    rate limits and the machine's load (`Throttle`); Anthropic requests go through their own
    client, so for those only the load counts. A job that would not finish before the
    deadline is not started. Each finished session is appended at once, so an interrupted
    command resumes where it stopped."""
    path = sessions_path(settings, model.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    dropped = drop_faulty_sessions(path)
    if dropped:
        print(f"{model.name}: {dropped} sessions hit a machine fault and will be run again")
    todo = pending(path, jobs)
    budget = budget or Budget(None)
    deadline = deadline or Deadline(None)
    throttle = Throttle(model.name, start or workers, workers)
    lock = threading.Lock()
    client = httpx.Client(
        base_url=model.base_url,
        headers={"Authorization": f"Bearer {os.environ.get(model.api_key_env, '')}"},
        timeout=httpx.Timeout(300.0, connect=30.0),
        event_hooks={"response": [throttle.observe]},
    )
    skipped = 0

    def work(job: Job) -> SessionRun | None:
        nonlocal skipped
        case, setting = job.case, job.setting
        if not budget.room:
            return None
        throttle.acquire()
        try:
            if not deadline.fits(setting.items):
                with lock:
                    skipped += 1
                return None
            wait_for_room()
            began = time.monotonic()
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
                    f"failed {case.id} {setting.id} trial {job.trial}: "
                    f"{type(error).__name__}: {error}"
                )
                return None
        finally:
            throttle.release()
        seconds = time.monotonic() - began
        deadline.finished(setting.items, seconds)
        run.seconds = round(seconds, 1)
        budget.add(run.cost_usd)
        with lock, path.open("a") as out:
            out.write(run.model_dump_json() + "\n")
        changed = [i for i in run.items if i.changed]
        print(
            f"{model.name} {case.id} {setting.id} t{job.trial}: "
            f"harm on {sum(bool(i.harm) for i in changed)} of {len(changed)} changed items, "
            f"{sum(i.ending == 'reported' for i in run.items)} of {len(run.items)} reported, "
            f"{run.usage.requests} requests, ${run.cost_usd:.4f}, {seconds:.0f} s",
            flush=True,
        )
        return run

    stop = threading.Event()
    throttle.run_adjusting(stop)
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            finished = [r for r in pool.map(work, todo) if r is not None]
    finally:
        stop.set()
        client.close()
    if not budget.room:
        left = len(todo) - len(finished)
        print(f"{model.name}: stopped at the ${budget.limit:.2f} limit with {left} sessions left")
    if skipped:
        print(f"{model.name}: {skipped} sessions left for the next machine, past the deadline")
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
