"""`steward run --condition`: who plays the people."""

import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

import steward.runner as runner
from steward.cli import build_parser, run_benchmark
from steward.config import Paths, Settings
from steward.estimate import session_usd, simulator_profile
from steward.execution.policy import Message, Policy, Usage, known_drops
from steward.models import Model
from steward.runner import FailedSession, Job
from steward.session import HARNESS, SessionRun, SessionSpec, Setting
from steward.simulator import OpenAISimulator
from steward.task import Task


def test_simulator_model_none_runs_the_benchmark_without_a_simulator(
    monkeypatch: pytest.MonkeyPatch, task: Task, full: SessionSpec, tmp_path: Path
) -> None:
    ran: list[tuple[str, int, object]] = []

    def run_jobs(
        settings: object,
        model: Model,
        jobs: list[Job],
        workers: int,
        sim: object,
        budget: object = None,
        start: int | None = None,
        **options: object,
    ) -> None:
        ran.append((model.name, len(jobs), sim))

    monkeypatch.setattr(runner, "run_jobs", run_jobs)
    # Nothing may reach for the Anthropic client.
    monkeypatch.setitem(sys.modules, "anthropic", None)
    argv = ["run", "--model", "gpt-6-luna", "--condition", "sd", "--trials", "2"]
    args = build_parser().parse_args([*argv, "--simulator-model", "none", "--confirm"])
    settings = Settings(paths=Paths(root=tmp_path, data=tmp_path))
    assert run_benchmark(settings, args, [(task, full)]) == 0
    assert ran == [("gpt-6-luna", 2, None)]


def test_the_simulator_model_defaults_to_the_session_simulator() -> None:
    args = build_parser().parse_args(["run", "--model", "gpt-6-luna", "--condition", "sd"])
    assert args.simulator_model == "claude-sonnet-5-5"


def test_an_openai_model_can_play_the_people(
    monkeypatch: pytest.MonkeyPatch, task: Task, spec: SessionSpec, tmp_path: Path
) -> None:
    ran: list[tuple[str, object, object, object]] = []

    def run_jobs(
        settings: object,
        model: Model,
        jobs: list[Job],
        workers: int,
        sim: object,
        budget: object = None,
        start: int | None = None,
        **options: object,
    ) -> None:
        ran.append((model.name, sim, getattr(budget, "limit", None), start))

    monkeypatch.setattr(runner, "run_jobs", run_jobs)
    monkeypatch.setitem(sys.modules, "anthropic", None)
    argv = ["run", "--model", "gpt-6-luna", "--condition", "sd", "--confirm"]
    extra = ["--simulator-model", "gpt-6-luna", "--start", "4", "--max-usd", "2"]
    args = build_parser().parse_args([*argv, *extra])
    settings = Settings(paths=Paths(root=tmp_path, data=tmp_path))
    assert run_benchmark(settings, args, [(task, spec)]) == 0
    [(name, sim, limit, start)] = ran
    assert name == "gpt-6-luna" and isinstance(sim, OpenAISimulator)
    assert sim.model == "gpt-6-luna" and limit == 2 and start == 4


def test_the_benchmark_runs_only_tasks_with_both_conditions(
    task: Task, spec: SessionSpec, full: SessionSpec
) -> None:
    other = task.model_copy(update={"id": "other"})
    jobs = runner.benchmark_jobs([(task, full), (other, spec)], ["State Drift"], 1)
    assert [job.task.id for job in jobs] == [task.id]


def test_a_second_run_on_the_same_session_file_stops_at_once(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl"
    with (
        runner.holding(path),
        pytest.raises(runner.SessionsBusyError, match="in use"),
        runner.holding(path),
    ):
        pass
    with runner.holding(path):
        pass


def test_dropping_faulty_sessions_replaces_the_file_whole(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl"
    path.write_text('{"a": 1}\n{"cut short\n')
    assert runner.drop_faulty_sessions(path) == 1
    assert path.read_text() == '{"a": 1}\n'
    assert [p.name for p in tmp_path.iterdir()] == ["m.jsonl"]


def rejecting(policy_for: Callable[[list[Message]], Policy]) -> None:
    """A session whose model spends a request and is then refused with a 400."""
    policy = policy_for([])
    policy.usage.requests, policy.usage.prompt_tokens = 1, 1_000_000  # type: ignore[attr-defined]
    request = httpx.Request("POST", "https://example.test/responses")
    response = httpx.Response(400, text='{"error": "bad parameter"}', request=request)
    raise httpx.HTTPStatusError("400 Bad Request", request=request, response=response)


def test_a_session_that_keeps_failing_is_logged_with_its_spend_and_given_up(
    monkeypatch: pytest.MonkeyPatch, task: Task, full: SessionSpec, tmp_path: Path
) -> None:
    from steward.models import MODELS

    def run_session(
        settings: object,
        task: object,
        spec: object,
        setting: object,
        policy_for: object,
        *a: object,
    ) -> None:
        rejecting(policy_for)  # type: ignore[arg-type]

    monkeypatch.setattr(runner, "run_session", run_session)
    settings = Settings(paths=Paths(root=tmp_path, data=tmp_path))
    model = MODELS["gpt-6-luna"]
    jobs = runner.benchmark_jobs([(task, full)], ["State Drift"], 1)
    budget = runner.Budget(None)
    assert runner.run_jobs(settings, model, jobs, 1, budget=budget) == []
    log = runner.failed_path(settings, model.name)
    records = [runner.FailedSession.model_validate_json(line) for line in log.open()]
    assert len(records) == runner.SESSION_ATTEMPTS
    assert records[0].error == "HTTPStatusError" and "bad parameter" in records[0].body
    assert records[0].usage.prompt_tokens == 1_000_000
    assert budget.spent == pytest.approx(2 * model.pricing.cost(records[0].usage))
    # After FAILURE_LIMIT recorded failures the session is no longer tried.
    runner.run_jobs(settings, model, jobs, 1)
    monkeypatch.setattr(runner, "run_session", lambda *a: pytest.fail("tried again"))
    runner.run_jobs(settings, model, jobs, 1)
    assert len(log.read_text().splitlines()) == runner.FAILURE_LIMIT
    assert all(r.kind == "model" for r in map(FailedSession.model_validate_json, log.open()))


def test_the_failure_cap_is_exact_and_retry_failed_ignores_it(
    monkeypatch: pytest.MonkeyPatch, task: Task, full: SessionSpec, tmp_path: Path
) -> None:
    from steward.models import MODELS

    tried: list[int] = []

    def run_session(*a: object) -> None:
        tried.append(1)
        rejecting(a[4])  # type: ignore[arg-type]

    monkeypatch.setattr(runner, "run_session", run_session)
    settings = Settings(paths=Paths(root=tmp_path, data=tmp_path))
    model = MODELS["gpt-6-luna"]
    jobs = runner.benchmark_jobs([(task, full)], ["State Drift"], 1)
    log = runner.failed_path(settings, model.name)
    log.parent.mkdir(parents=True)
    record = FailedSession(
        key=list(jobs[0].key),
        simulator=None,
        error="HTTPStatusError",
        message="",
        body="",
        usage=Usage(),
        cost_usd=0.0,
    )
    log.write_text((record.model_dump_json() + "\n") * (runner.FAILURE_LIMIT - 1))
    # One failure left before the cap: one attempt, not the run's two.
    runner.run_jobs(settings, model, jobs, 1)
    assert len(tried) == 1
    runner.run_jobs(settings, model, jobs, 1)
    assert len(tried) == 1
    runner.run_jobs(settings, model, jobs, 1, retry_failed=True)
    assert len(tried) == 1 + runner.SESSION_ATTEMPTS


def test_infrastructure_failures_are_logged_and_always_tried_again(
    monkeypatch: pytest.MonkeyPatch, task: Task, full: SessionSpec, tmp_path: Path
) -> None:
    from steward.execution.policy import RequestFailedError
    from steward.models import MODELS
    from steward.prepare import MachineFaultError
    from steward.throttle import NoRoomError

    errors: list[Exception] = [
        NoRoomError("ceiling"),
        MachineFaultError("fork"),
        RequestFailedError("gave up after 15 attempts: HTTP 429"),
        runner.SimulatorFailedError("RequestFailedError: HTTP 429"),
    ]
    raised = iter(errors * 2)

    def run_session(*a: object) -> None:
        raise next(raised)

    monkeypatch.setattr(runner, "run_session", run_session)
    settings = Settings(paths=Paths(root=tmp_path, data=tmp_path))
    model = MODELS["gpt-6-luna"]
    jobs = runner.benchmark_jobs([(task, full)], ["State Drift"], 1)
    for _ in range(4):
        runner.run_jobs(settings, model, jobs, 1)
    log = runner.failed_path(settings, model.name)
    records = [FailedSession.model_validate_json(line) for line in log.open()]
    assert len(records) == 8 and {r.kind for r in records} == {"infrastructure"}
    assert runner.failures_of(log) == {}
    # Records written before the kind are classed by their error's name.
    old = records[0].model_copy(update={"kind": None})
    assert not old.counts and old.model_copy(update={"error": "HTTPStatusError"}).counts
    request = httpx.Request("POST", "https://example.test")
    busy = httpx.HTTPStatusError("", request=request, response=httpx.Response(503))
    assert runner.failure_kind(busy) == "infrastructure"
    bad = httpx.HTTPStatusError("", request=request, response=httpx.Response(400))
    assert runner.failure_kind(bad) == "model"


def test_a_failing_simulator_is_an_infrastructure_failure(task: Task) -> None:
    class Failing:
        model = "gpt-6-luna"

        def reply(self, *a: object, **k: object) -> str:
            raise httpx.ConnectError("down")

    guarded = runner.GuardedSimulator(Failing())  # type: ignore[arg-type]
    with pytest.raises(runner.SimulatorFailedError) as raised:
        guarded.reply(task, task.people[0], "", [])
    assert runner.failure_kind(raised.value) == "infrastructure"
    assert guarded.model == "gpt-6-luna"


def test_the_budget_counts_what_others_spent() -> None:
    spent = [0.0]
    budget = runner.Budget(1.0, others=lambda: spent[0])
    budget.add(0.6)
    assert budget.room and budget.total == 0.6
    spent[0] = 0.5
    assert not budget.room and budget.total == 1.1


def test_a_session_waiting_for_a_slot_does_not_start_past_the_limit(
    monkeypatch: pytest.MonkeyPatch, task: Task, full: SessionSpec, tmp_path: Path
) -> None:
    from steward.models import MODELS

    started: list[int] = []

    def run_session(
        settings: object, task: Task, spec: SessionSpec, setting: object, *a: object
    ) -> SessionRun:
        started.append(1)
        time.sleep(0.3)
        return SessionRun.model_validate(
            {
                "task": task.id,
                "session_version": spec.version,
                "setting": setting,
                "model": "gpt-6-luna",
                "trial": 0,
                "events": [],
                "items": [],
                "usage": Usage(),
                "cost_usd": 5.0,
                "dropped": ["reasoning"],
            }
        )

    monkeypatch.setattr(runner, "run_session", run_session)
    settings = Settings(paths=Paths(root=tmp_path, data=tmp_path))
    jobs = runner.benchmark_jobs([(task, full)], ["State Drift"], 2)
    budget = runner.Budget(1.0)
    # Two workers but one slot: the second job passes the first check and waits for the slot.
    runner.run_jobs(settings, MODELS["gpt-6-luna"], jobs, 2, budget=budget, start=1)
    assert started == [1] and budget.spent == 5.0
    # What the session's policy dropped is kept for the model's later sessions.
    assert known_drops("gpt-6-luna") == ["reasoning"]


def test_the_estimate_matches_the_measured_benchmark_session() -> None:
    from steward.models import MODELS

    setting = Setting(items=12, changed=(3, 5, 8, 11), per_day=6)
    dollars = runner.estimate_usd(MODELS["gpt-6-luna"], setting)
    assert dollars == pytest.approx(0.0163, abs=0.0005)
    # 2 replies at 1,600 prompt tokens and 220 output tokens at gpt-6-luna's prices.
    profile = simulator_profile("gpt-6-luna", {})
    played = session_usd(MODELS["gpt-6-luna"].pricing, profile, 12)
    assert played == pytest.approx(2 * (1_600 * 0.10 + 220 * 0.50) / 1e6)


def test_the_benchmark_estimate_prints_the_simulator_beside_it(
    task: Task, full: SessionSpec, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings(paths=Paths(root=tmp_path, data=tmp_path))
    argv = ["run", "--model", "gpt-6-luna", "--condition", "sd"]
    args = build_parser().parse_args([*argv, "--simulator-model", "gpt-6-luna"])
    assert run_benchmark(settings, args, [(task, full)]) == 0
    assert "for the simulator gpt-6-luna" in capsys.readouterr().out


def test_the_benchmark_estimate_covers_only_the_sessions_still_to_run(
    monkeypatch: pytest.MonkeyPatch,
    task: Task,
    full: SessionSpec,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = Settings(paths=Paths(root=tmp_path, data=tmp_path))
    jobs = runner.benchmark_jobs([(task, full)], ["State Drift"], 2)
    stored = SessionRun.model_validate(
        {
            "task": task.id,
            "session_version": full.version,
            "setting": jobs[0].setting,
            "model": "gpt-6-luna",
            "trial": 0,
            "harness": HARNESS,
            "events": [],
            "items": [],
            "usage": Usage(),
            "cost_usd": 0.0,
        }
    )
    path = runner.sessions_path(settings, "gpt-6-luna")
    path.parent.mkdir(parents=True)
    path.write_text(stored.model_dump_json() + "\n")
    argv = ["run", "--model", "gpt-6-luna", "--condition", "sd", "--trials", "2"]
    args = build_parser().parse_args([*argv, "--simulator-model", "none"])
    assert run_benchmark(settings, args, [(task, full)]) == 0
    printed = capsys.readouterr().out
    assert "1 of 2 sessions to run" in printed and "limit $25.00" in printed


def test_the_benchmark_runs_one_trial_and_refuses_trials_validation_does_not_replay(
    task: Task, full: SessionSpec, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings(paths=Paths(root=tmp_path, data=tmp_path))
    argv = ["run", "--model", "gpt-6-luna", "--condition", "sd"]
    args = build_parser().parse_args([*argv, "--simulator-model", "none"])
    assert run_benchmark(settings, args, [(task, full)]) == 0
    assert "1 of 1 sessions to run" in capsys.readouterr().out
    args = build_parser().parse_args([*argv, "--simulator-model", "none", "--trials", "3"])
    assert run_benchmark(settings, args, [(task, full)]) == 2
    assert "validation replays trials 0, 1 only" in capsys.readouterr().err


def test_only_a_simulator_that_is_the_tested_model_cuts_the_sessions_at_once(
    monkeypatch: pytest.MonkeyPatch, task: Task, full: SessionSpec, tmp_path: Path
) -> None:
    from steward.models import MODELS

    model = MODELS["gpt-6-luna"]
    simulator = OpenAISimulator(httpx.Client(base_url=model.base_url), tmp_path, model.name)
    # Another model of the same provider has a rate limit of its own.
    elsewhere = OpenAISimulator(httpx.Client(base_url=model.base_url), tmp_path, "gpt-6.2-sol")
    seen: list[int] = []

    def run_session(*a: object) -> None:
        seen.append(len(simulator.client.event_hooks["response"]))
        seen.append(len(elsewhere.client.event_hooks["response"]))
        raise RuntimeError("stop here")

    monkeypatch.setattr(runner, "run_session", run_session)
    settings = Settings(paths=Paths(root=tmp_path, data=tmp_path))
    jobs = runner.benchmark_jobs([(task, full)], ["State Drift"], 1)
    runner.run_jobs(settings, model, jobs, 1, simulator)
    runner.run_jobs(settings, model, jobs, 1, elsewhere)
    # The throttle's hook is on the shared client while the run lasts, and only there.
    assert seen[0] == 1 and seen[-1] == 0
    assert simulator.client.event_hooks["response"] == []


def finished(task: Task, spec: SessionSpec, setting: object, cost: float) -> SessionRun:
    return SessionRun.model_validate(
        {
            "task": task.id,
            "session_version": spec.version,
            "setting": setting,
            "model": "gpt-6-luna",
            "trial": 0,
            "events": [],
            "items": [],
            "usage": Usage(),
            "cost_usd": cost,
        }
    )


def test_each_session_stores_what_its_own_simulator_requests_used(
    monkeypatch: pytest.MonkeyPatch, task: Task, full: SessionSpec, tmp_path: Path
) -> None:
    from steward.models import MODELS
    from steward.simulator import CachedSimulator

    class Counting(CachedSimulator):
        def _ask(self, system: str, chat: list[str]) -> str:
            time.sleep(0.01)
            self.last = Usage(requests=1, prompt_tokens=100, completion_tokens=10)
            self._count(self.last)
            return "fine"

    def run_session(
        settings: object, task: Task, spec: SessionSpec, setting: Setting, *a: object
    ) -> SessionRun:
        simulator = a[4]
        for turn in range(len(setting.changed)):
            simulator.reply(task, task.people[0], f"{setting.id} {turn}", [])  # type: ignore[attr-defined]
        return finished(task, spec, setting, 0.0)

    monkeypatch.setattr(runner, "run_session", run_session)
    settings = Settings(paths=Paths(root=tmp_path, data=tmp_path))
    jobs = runner.benchmark_jobs([(task, full)], ["State Drift", "State and Instruction Drift"], 2)
    simulator = Counting(tmp_path / "cache", "gpt-6-luna")
    runs = runner.run_jobs(settings, MODELS["gpt-6-luna"], jobs, 4, simulator)
    assert len(runs) == 4
    for run in runs:
        assert run.simulator_usage is not None
        assert run.simulator_usage.requests == len(run.setting.changed)
    assert (
        sum(r.simulator_usage.requests for r in runs if r.simulator_usage)
        == simulator.usage.requests
    )


def test_parallel_sessions_stop_within_one_session_of_the_limit(
    monkeypatch: pytest.MonkeyPatch, task: Task, full: SessionSpec, tmp_path: Path
) -> None:
    from steward.models import MODELS

    simulator_spent = [0.0]
    lock = threading.Lock()

    def run_session(
        settings: object, task: Task, spec: SessionSpec, setting: object, *a: object
    ) -> SessionRun:
        time.sleep(0.05)
        with lock:
            simulator_spent[0] += 0.5
        time.sleep(0.05)
        return finished(task, spec, setting, 1.0)

    monkeypatch.setattr(runner, "run_session", run_session)
    settings = Settings(paths=Paths(root=tmp_path, data=tmp_path))
    [job] = runner.benchmark_jobs([(task, full)], ["State Drift"], 1)
    jobs = [job.model_copy(update={"trial": trial}) for trial in range(20)]
    limit, each = 5.0, 1.5

    def run(expected: Callable[[Job], float] | None) -> float:
        simulator_spent[0] = 0.0
        for path in (tmp_path / "tasks" / "sessions").glob("*.jsonl"):
            path.unlink()
        budget = runner.Budget(limit, others=lambda: simulator_spent[0])
        model = MODELS["gpt-6-luna"]
        runner.run_jobs(settings, model, jobs, 8, budget=budget, expected=expected)
        return budget.spent + simulator_spent[0]

    # Counting only what finished, eight sessions at once pass the limit by far.
    assert run(None) > limit + each
    assert run(lambda job: each) <= limit + each
