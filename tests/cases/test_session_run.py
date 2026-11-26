"""`steward session-run --condition`: who plays the people."""

import sys
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

import steward.cases.session_commands as session_commands
from steward.cases.case import Case
from steward.cases.run import Model
from steward.cases.session_commands import Job
from steward.cases.sessions import SessionSpec
from steward.cases.simulator import OpenAISimulator
from steward.cli import build_parser, run_benchmark
from steward.config import Paths, Settings
from steward.execution.policy import Message, Policy


def test_simulator_model_none_runs_the_benchmark_without_a_simulator(
    monkeypatch: pytest.MonkeyPatch, case: Case, full: SessionSpec
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
    ) -> None:
        ran.append((model.name, len(jobs), sim))

    monkeypatch.setattr(session_commands, "run_jobs", run_jobs)
    # Nothing may reach for the Anthropic client.
    monkeypatch.setitem(sys.modules, "anthropic", None)
    argv = ["session-run", "--model", "gpt-6-luna", "--condition", "sd", "--trials", "2"]
    args = build_parser().parse_args([*argv, "--simulator-model", "none", "--confirm"])
    assert run_benchmark(None, args, [(case, full)]) == 0  # type: ignore[arg-type]
    assert ran == [("gpt-6-luna", 2, None)]


def test_the_simulator_model_defaults_to_the_session_simulator() -> None:
    args = build_parser().parse_args(["session-run", "--model", "gpt-6-luna", "--condition", "sd"])
    assert args.simulator_model == "claude-sonnet-5-5"


def test_an_openai_model_can_play_the_people(
    monkeypatch: pytest.MonkeyPatch, case: Case, spec: SessionSpec, tmp_path: Path
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
    ) -> None:
        ran.append((model.name, sim, getattr(budget, "limit", None), start))

    monkeypatch.setattr(session_commands, "run_jobs", run_jobs)
    monkeypatch.setitem(sys.modules, "anthropic", None)
    argv = ["session-run", "--model", "gpt-6-luna", "--condition", "sd", "--confirm"]
    extra = ["--simulator-model", "gpt-6-luna", "--start", "4", "--max-usd", "2"]
    args = build_parser().parse_args([*argv, *extra])
    settings = Settings(paths=Paths(root=tmp_path, data=tmp_path))
    assert run_benchmark(settings, args, [(case, spec)]) == 0
    [(name, sim, limit, start)] = ran
    assert name == "gpt-6-luna" and isinstance(sim, OpenAISimulator)
    assert sim.model == "gpt-6-luna" and limit == 2 and start == 4


def test_the_benchmark_runs_only_tasks_with_both_conditions(
    case: Case, spec: SessionSpec, full: SessionSpec
) -> None:
    other = case.model_copy(update={"id": "other"})
    jobs = session_commands.benchmark_jobs([(case, full), (other, spec)], ["State Drift"], 1)
    assert [job.case.id for job in jobs] == [case.id]


def test_a_second_run_on_the_same_session_file_stops_at_once(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl"
    with (
        session_commands.holding(path),
        pytest.raises(session_commands.SessionsBusyError, match="in use"),
        session_commands.holding(path),
    ):
        pass
    with session_commands.holding(path):
        pass


def test_dropping_faulty_sessions_replaces_the_file_whole(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl"
    path.write_text('{"a": 1}\n{"cut short\n')
    assert session_commands.drop_faulty_sessions(path) == 1
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
    monkeypatch: pytest.MonkeyPatch, case: Case, full: SessionSpec, tmp_path: Path
) -> None:
    from steward.cases.commands import MODELS

    def run_session(
        settings: object,
        case: object,
        spec: object,
        setting: object,
        policy_for: object,
        *a: object,
    ) -> None:
        rejecting(policy_for)  # type: ignore[arg-type]

    monkeypatch.setattr(session_commands, "run_session", run_session)
    settings = Settings(paths=Paths(root=tmp_path, data=tmp_path))
    model = MODELS["gpt-6-luna"]
    jobs = session_commands.benchmark_jobs([(case, full)], ["State Drift"], 1)
    budget = session_commands.Budget(None)
    assert session_commands.run_jobs(settings, model, jobs, 1, budget=budget) == []
    log = session_commands.failed_path(settings, model.name)
    records = [session_commands.FailedSession.model_validate_json(line) for line in log.open()]
    assert len(records) == session_commands.SESSION_ATTEMPTS
    assert records[0].error == "HTTPStatusError" and "bad parameter" in records[0].body
    assert records[0].usage.prompt_tokens == 1_000_000
    assert budget.spent == pytest.approx(2 * model.pricing.cost(records[0].usage))
    # After FAILURE_LIMIT recorded failures the session is no longer tried.
    session_commands.run_jobs(settings, model, jobs, 1)
    monkeypatch.setattr(session_commands, "run_session", lambda *a: pytest.fail("tried again"))
    session_commands.run_jobs(settings, model, jobs, 1)
    assert len(log.read_text().splitlines()) == session_commands.FAILURE_LIMIT
