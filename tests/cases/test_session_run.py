"""`steward session-run --condition`: who plays the people."""

import sys
from pathlib import Path

import pytest

import steward.cases.session_commands as session_commands
from steward.cases.case import Case
from steward.cases.run import Model
from steward.cases.session_commands import Job
from steward.cases.sessions import SessionSpec
from steward.cases.simulator import OpenAISimulator
from steward.cli import build_parser, run_benchmark
from steward.config import Paths, Settings


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
