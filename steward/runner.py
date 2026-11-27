"""Running benchmark sessions: the jobs, the stored sessions, the budget and the failures."""

from __future__ import annotations

import fcntl
import json
import os
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict

from steward.config import Settings
from steward.execution.policy import Message, Policy, Usage, remember_drops
from steward.models import Model, model_policy
from steward.rate_log import log_while, rate_log
from steward.schedule import (
    Condition,
    benchmark_setting,
    has_instruction_drift,
)
from steward.session import (
    HARNESS,
    SessionRun,
    SessionSpec,
    Setting,
    load_session_spec,
    run_session,
)
from steward.simulator import Simulator, SimulatorFailedError
from steward.task import Person, Task, load_tasks, tasks_root
from steward.throttle import Deadline, Throttle, wait_for_room

# What a session is assumed to use when estimating cost: requests per item, tokens the
# conversation grows by per item, tokens it starts with, output tokens per request, and the
# share of input that is not served from the cache. Benchmark sessions of gpt-6-luna made 53
# requests in 12 shifts with 90% of prompt tokens cached and cost $0.0163 on average; these
# give $0.0164.
ITEM_REQUESTS = 53 / 12
ITEM_TOKENS = 900
START_TOKENS = 3_000
REQUEST_OUTPUT = 300
FRESH_SHARE = 0.10


def benchmark_jobs(
    tasks: list[tuple[Task, SessionSpec]], conditions: list[Condition], trials: int
) -> list[Job]:
    """The benchmark run: every task that can run under both conditions (one with a goal
    change and an authority conflict), under each condition asked for, each trial on its own
    seeded schedule. Any other task is left out, as the benchmark is scored on these alone."""
    return [
        Job(
            task=task,
            spec=spec,
            setting=benchmark_setting(task.id, spec, condition, trial),
            trial=trial,
        )
        for task, spec in tasks
        if has_instruction_drift(spec)
        for condition in conditions
        for trial in range(trials)
    ]


SessionTask = tuple[Task, SessionSpec]


def load_sessions(settings: Settings, only: list[str] | None = None) -> list[SessionTask]:
    """The tasks that can be run as sessions: those with a session file beside them."""
    root = tasks_root(settings)
    found = []
    for task in load_tasks(settings, only):
        spec = load_session_spec(root / task.id, task)
        if spec is not None:
            found.append((task, spec))
    return found


def sessions_path(settings: Settings, model: str) -> Path:
    return settings.paths.data / "tasks" / "sessions" / f"{model}.jsonl"


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
    """Remove lines cut short by a run that was stopped while writing, so they are run again.
    Returns how many were removed. A session in which the machine faulted is never stored:
    the run confirms a fault with a probe of its own and fails (`RetryingExecutor`), so the
    text of a stored session, which holds what the agent wrote and read, is not searched for
    one."""
    if not path.exists():
        return 0
    lines = stored_lines(path)
    kept = [line for line in lines if whole(line)]
    if len(kept) != len(lines):
        replace_text(path, "".join(line + "\n" for line in kept))
    return len(lines) - len(kept)


def replace_text(path: Path, text: str) -> None:
    """Write a file whole or not at all: a stop part way leaves the old file in place."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text)
    os.replace(temporary, path)


class SessionsBusyError(RuntimeError):
    """Another process is running sessions into the same model's file."""


@contextmanager
def holding(path: Path) -> Iterator[None]:
    """Hold the model's session file for the whole of a run, so that two processes never
    run the same jobs or rewrite the file under each other. Fails at once if it is held."""
    lock = path.with_name(f"{path.name}.lock")
    with lock.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SessionsBusyError(
                f"{path} is in use by another session run; wait for it to finish"
            ) from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class Job(BaseModel):
    """One session to run: a task, a setting and a repeat."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    task: Task
    spec: SessionSpec
    setting: Setting
    trial: int = 0

    @property
    def key(self) -> SessionKey:
        return (self.task.id, self.spec.version, self.setting.id, self.trial, HARNESS)


# What makes a stored session the same as a planned one: the task, its version, the setting,
# the repeat and the harness that ran it.
SessionKey = tuple[str, str, str, int, str | None]


def stored_key(run: SessionRun) -> SessionKey:
    return (run.task, run.session_version, run.setting.id, run.trial, run.harness)


# How many times one run tries a session that fails, and how many of the tested model's own
# failures recorded in the model's failed-session log stop it being tried again.
SESSION_ATTEMPTS = 2
FAILURE_LIMIT = 4
# How much of a failed request's response body is kept.
BODY_EXCERPT = 500
# The failures that say nothing of the tested model: the machine, the process ceiling, the
# database server, the image build, the simulator, the network and the provider's rate limits
# and server errors once every retry is spent. By class name, as the Anthropic SDK is not
# imported here.
INFRASTRUCTURE_ERRORS = frozenset(
    {
        "MachineFaultError",
        "NoRoomError",
        "ServerError",
        "BuildError",
        "SimulatorFailedError",
        "RequestFailedError",
        "TransportError",
        "TimeoutException",
        "TimeoutError",
        "ConnectionError",
        "APIConnectionError",
        "RateLimitError",
        "InternalServerError",
        "OverloadedError",
        "ServiceUnavailableError",
    }
)

FailureKind = Literal["model", "infrastructure"]


def failure_kind(error: BaseException) -> FailureKind:
    """Whether a failed session is the tested model's own error, which counts towards
    `FAILURE_LIMIT`, or the infrastructure's, which is always tried again on the next run."""
    names = {cls.__name__ for cls in type(error).__mro__}
    status = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    if status is None and isinstance(response, httpx.Response):
        status = response.status_code
    transient = isinstance(status, int) and (status == 429 or status >= 500)
    return "infrastructure" if names & INFRASTRUCTURE_ERRORS or transient else "model"


class GuardedSimulator:
    """A simulator whose failures are reported as `SimulatorFailedError`, so that a session
    whose people could not be played is never counted against the tested model. One guards
    one session and sums what its requests used, from the reply each made on its thread."""

    def __init__(self, inner: Simulator) -> None:
        self.inner = inner
        self.model = getattr(inner, "model", None)
        self.usage = Usage()

    def reply(
        self,
        task: Task,
        person: Person,
        stance: str,
        chat: list[str],
        briefing: str | None = None,
    ) -> str | None:
        try:
            return self.inner.reply(task, person, stance, chat, briefing=briefing)
        except SimulatorFailedError:
            raise
        except Exception as error:
            raise SimulatorFailedError(f"{type(error).__name__}: {error}") from error
        finally:
            last = getattr(self.inner, "last", None)
            if isinstance(last, Usage):
                self.usage.merge(last)


class FailedSession(BaseModel):
    """A session that raised before it finished: what failed, whose failure it was and what
    it had spent. Records written before `kind` are classed by their error's name."""

    key: list[str | int | None]
    simulator: str | None
    error: str
    message: str
    body: str
    usage: Usage
    cost_usd: float
    kind: FailureKind | None = None

    @property
    def counts(self) -> bool:
        """Whether the failure counts towards `FAILURE_LIMIT`."""
        kind = self.kind or ("infrastructure" if self.error in INFRASTRUCTURE_ERRORS else "model")
        return kind == "model"


def failed_path(settings: Settings, model: str) -> Path:
    return settings.paths.data / "tasks" / "sessions" / f"{model}.failed.jsonl"


def failures_of(path: Path) -> dict[tuple[object, ...], int]:
    """How many times each session key and simulator is recorded as failed by the tested
    model's own error."""
    counts: dict[tuple[object, ...], int] = defaultdict(int)
    for line in stored_lines(path):
        if whole(line):
            failed = FailedSession.model_validate_json(line)
            if failed.counts:
                counts[(*failed.key, failed.simulator)] += 1
    return dict(counts)


def body_of(error: BaseException) -> str:
    """An excerpt of the response a failed request got, where the error carries one."""
    response = getattr(error, "response", None)
    if isinstance(response, httpx.Response):
        try:
            return response.text[:BODY_EXCERPT]
        except httpx.ResponseNotRead:
            return ""
    return ""


class Budget:
    """A limit in dollars on what one command spends: the tested models' sessions, finished
    or failed, and what `others` reports, such as the simulator's spend so far. A session
    starts only when what it is expected to cost fits beside what is spent and what the
    running sessions hold: each holds the larger of its expected cost and its spend so far.
    So parallel sessions pass the limit only by what they spend beyond their expected cost."""

    def __init__(
        self, limit: float | None, spent: float = 0.0, others: Callable[[], float] | None = None
    ) -> None:
        self.limit = limit
        self.spent = spent
        self.others = others
        self.lock = threading.Lock()
        self.held: dict[int, tuple[float, Callable[[], float]]] = {}
        self.tickets = 0

    def add(self, dollars: float) -> None:
        with self.lock:
            self.spent += dollars

    @property
    def total(self) -> float:
        running = sum(max(dollars, live()) for dollars, live in list(self.held.values()))
        return self.spent + running + (self.others() if self.others is not None else 0.0)

    @property
    def room(self) -> bool:
        return self.limit is None or self.total < self.limit

    def reserve(self, dollars: float, live: Callable[[], float]) -> int | None:
        """Hold the expected cost of a session about to start, or None if it does not fit."""
        with self.lock:
            if self.limit is not None and self.total + dollars > self.limit:
                return None
            self.tickets += 1
            self.held[self.tickets] = (dollars, live)
            return self.tickets

    def release(self, ticket: int) -> None:
        with self.lock:
            self.held.pop(ticket, None)


def pending(path: Path, jobs: list[Job], simulator: str | None = None) -> list[Job]:
    """The jobs not already in the model's session file with the same simulator, longest
    sessions first so that none is left running alone at the end. A session whose people were
    played by another model, or by none, does not stand in for one played by `simulator`."""
    done = {(*stored_key(run), run.simulator) for run in stored_sessions(path)}
    todo = [job for job in jobs if (*job.key, simulator) not in done]
    # Within one length, every task gets its first settings before any gets its later ones,
    # so a run stopped part way has an even sample.
    seen: dict[str, int] = defaultdict(int)
    rounds = {}
    for job in todo:
        rounds[id(job)] = seen[job.task.id]
        seen[job.task.id] += 1
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
    retry_failed: bool = False,
    expected: Callable[[Job], float] | None = None,
) -> list[SessionRun]:
    """Run every job not already in the model's session file, at most `workers` at once.
    With `start` below `workers` the number at once begins there and follows the provider's
    rate limits and the machine's load (`Throttle`); Anthropic requests go through their own
    client, so for those only the load counts. A job that would not finish before the
    deadline is not started. Each finished session is appended at once, so an interrupted
    command resumes where it stopped. A session that failed `FAILURE_LIMIT` times by the
    tested model's own error is not tried again unless `retry_failed`."""
    path = sessions_path(settings, model.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with holding(path):
        return run_held(
            settings,
            model,
            jobs,
            workers,
            simulator,
            budget,
            start,
            deadline,
            path,
            retry_failed,
            expected,
        )


def run_held(
    settings: Settings,
    model: Model,
    jobs: list[Job],
    workers: int,
    simulator: Simulator | None,
    budget: Budget | None,
    start: int | None,
    deadline: Deadline | None,
    path: Path,
    retry_failed: bool = False,
    expected: Callable[[Job], float] | None = None,
) -> list[SessionRun]:
    """`run_jobs` once the model's session file is held."""
    dropped = drop_faulty_sessions(path)
    if dropped:
        print(f"{model.name}: {dropped} sessions hit a machine fault and will be run again")
    played_by = getattr(simulator, "model", None)
    failures_path = failed_path(settings, model.name)
    failed_before = {} if retry_failed else failures_of(failures_path)
    todo = []
    given_up = 0
    for job in pending(path, jobs, played_by):
        if failed_before.get((*job.key, played_by), 0) >= FAILURE_LIMIT:
            given_up += 1
        else:
            todo.append(job)
    if given_up:
        print(
            f"{model.name}: {given_up} sessions not tried again after {FAILURE_LIMIT} failures; "
            f"see {failures_path}"
        )
    budget = budget or Budget(None)

    def expected_usd(job: Job) -> float:
        return expected(job) if expected is not None else 0.0

    deadline = deadline or Deadline(None)
    throttle = Throttle(model.name, start or workers, workers)
    lock = threading.Lock()
    rates = rate_log(settings, model.name)
    client = httpx.Client(
        base_url=model.base_url,
        headers=model.auth_headers(os.environ.get(model.api_key_env, "")),
        timeout=httpx.Timeout(model.timeout_seconds, connect=30.0),
        event_hooks={"response": [throttle.observe, rates.hook("agent")]},
    )
    skipped = 0

    def failed(job: Job, policies: list[Policy], error: Exception) -> FailureKind:
        """Record a failed session, with what its policy spent and whose failure it was,
        and count the spend."""
        usage = getattr(policies[-1], "usage", Usage()) if policies else Usage()
        cost = model.pricing.cost(usage)
        budget.add(cost)
        kind = failure_kind(error)
        record = FailedSession(
            key=list(job.key),
            simulator=played_by,
            error=type(error).__name__,
            message=str(error)[:BODY_EXCERPT],
            body=body_of(error),
            usage=usage,
            cost_usd=cost,
            kind=kind,
        )
        with lock, failures_path.open("a") as out:
            out.write(record.model_dump_json() + "\n")
        print(
            f"failed {job.task.id} {job.setting.id} trial {job.trial} ({kind}): "
            f"{type(error).__name__}: {error} (${cost:.4f} spent)"
        )
        return kind

    def attempt(job: Job, policies: list[Policy]) -> SessionRun | FailureKind:
        policies.clear()
        made = model_policy(client, model, job.task)

        def policy_for(tools: list[Message]) -> Policy:
            policies.append(made(tools))
            return policies[-1]

        guarded = GuardedSimulator(simulator) if simulator is not None else None
        try:
            wait_for_room()
            run = run_session(
                settings,
                job.task,
                job.spec,
                job.setting,
                policy_for,
                model.name,
                job.trial,
                model.pricing,
                guarded,
            )
        except Exception as error:  # noqa: BLE001 - one failed session must not stop the rest
            return failed(job, policies, error)
        if guarded is not None:
            run.simulator_usage = guarded.usage
        # What the provider's replies report is recorded over what its documentation states.
        reported = [getattr(policy, "effort", None) for policy in policies]
        run.reasoning = next((effort for effort in reported if effort), model.reasoning)
        return run

    def work(job: Job) -> SessionRun | None:
        nonlocal skipped
        task, setting = job.task, job.setting
        if not budget.room:
            return None
        throttle.acquire()
        policies: list[Policy] = []

        def live() -> float:
            return model.pricing.cost(getattr(policies[-1], "usage", Usage())) if policies else 0.0

        # Spend can pass the limit while a session waits for a slot.
        ticket = budget.reserve(expected_usd(job), live)
        if ticket is None:
            throttle.release()
            return None
        try:
            if not deadline.fits(setting.items):
                with lock:
                    skipped += 1
                return None
            began = time.monotonic()
            # The cap is exact: a run never takes a session past FAILURE_LIMIT of its own.
            left = FAILURE_LIMIT - failed_before.get((*job.key, played_by), 0)
            if retry_failed:
                left = SESSION_ATTEMPTS
            run: SessionRun | None = None
            for _ in range(SESSION_ATTEMPTS):
                outcome = attempt(job, policies)
                if isinstance(outcome, SessionRun):
                    run = outcome
                    break
                left -= outcome == "model"
                if left <= 0 or not budget.room:
                    break
            if run is None:
                return None
            budget.add(run.cost_usd)
        finally:
            budget.release(ticket)
            throttle.release()
        # A parameter a subclass dropped on its own is remembered for later sessions too.
        remember_drops(model.name, run.dropped)
        seconds = time.monotonic() - began
        deadline.finished(setting.items, seconds)
        run.seconds = round(seconds, 1)
        with lock, path.open("a") as out:
            out.write(run.model_dump_json() + "\n")
        changed = [i for i in run.items if i.changed]
        print(
            f"{model.name} {task.id} {setting.id} t{job.trial}: "
            f"harm on {sum(bool(i.harm) for i in changed)} of {len(changed)} changed items, "
            f"{sum(i.ending == 'reported' for i in run.items)} of {len(run.items)} reported, "
            f"{run.usage.requests} requests, ${run.cost_usd:.4f}, {seconds:.0f} s",
            flush=True,
        )
        return run

    # A rate limit belongs to a provider and a model. Only a simulator that is the tested
    # model shares its limit, so only then do its refusals cut the sessions at once; any
    # other simulator waits its rate limits out in its own client's retries.
    played_client = getattr(simulator, "client", None)
    shared = played_client if isinstance(played_client, httpx.Client) else None
    if played_by != model.name:
        shared = None
    hooks = shared.event_hooks if shared is not None else {}
    if shared is not None:
        shared.event_hooks = {**hooks, "response": [*hooks["response"], throttle.observe]}
    stop = threading.Event()
    throttle.run_adjusting(stop)
    logged = [log_while(rates, lambda: (throttle.active, throttle.limit), stop, model.name)]
    if played_by and played_by != model.name:
        played = rate_log(settings, played_by)
        logged.append(log_while(played, lambda: (0, 0), stop, model.name))
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            finished = [r for r in pool.map(work, todo) if r is not None]
    finally:
        stop.set()
        client.close()
        if shared is not None:
            shared.event_hooks = hooks
        for thread in logged:
            thread.join()
    if not budget.room:
        left = len(todo) - len(finished)
        print(
            f"{model.name}: stopped at the ${budget.limit:.2f} limit (${budget.total:.4f} "
            f"spent) with {left} sessions left"
        )
    if skipped:
        print(f"{model.name}: {skipped} sessions left for the next machine, past the deadline")
    return finished


def current_sessions(settings: Settings, models: list[str]) -> list[SessionRun]:
    """Every stored session made on the present version of its task by the present
    harness. Sessions of the same task, setting and trial all count: each is one more
    repeat."""
    versions = {task.id: spec.version for task, spec in load_sessions(settings)}
    return [
        run
        for model in models
        for run in stored_sessions(sessions_path(settings, model))
        if versions.get(run.task) == run.session_version and run.harness == HARNESS
    ]
