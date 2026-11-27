"""Pins what a refactor must not change: the versions of the benchmark's tasks, how stored
sessions are read and scored, and every text the agent and the simulator are shown."""

import asyncio
import hashlib
import json
from functools import cache
from pathlib import Path

import pytest

from steward.cases.case import Case, Person, case_directories, load_case
from steward.cases.prices import Pricing
from steward.cases.schedule import benchmark_setting, conditions_of, has_instruction_drift
from steward.cases.score import condition_of
from steward.cases.sessions import HARNESS, Session, SessionRun, SessionSpec, load_session_spec
from steward.cases.sim_probe import probed, run_probe
from steward.cases.simulator import CachedSimulator
from steward.cli import main
from steward.execution.history import tool_definitions
from steward.execution.policy import Message, Turn
from steward.execution.record import Action
from steward.schema import Step

ROOT = Path(__file__).parents[1] / "cases"
GOLDEN = Path(__file__).parent / "golden"
STORED = GOLDEN / "stored_sessions.jsonl"
MODEL = "gpt-6-luna"
# The same stored sessions under a second name, so the comparison of two models is printed.
SECOND = "gpt-6-luna-copy"


@cache
def benchmark_tasks() -> list[tuple[Case, SessionSpec]]:
    found = []
    for directory in case_directories(ROOT):
        case = load_case(directory)
        spec = load_session_spec(directory, case)
        if spec is not None and has_instruction_drift(spec):
            found.append((case, spec))
    return found


def golden(name: str) -> str:
    return (GOLDEN / name).read_text()


def test_the_task_versions_are_unchanged() -> None:
    versions = {case.id: [case.version, spec.version] for case, spec in benchmark_tasks()}
    assert versions == json.loads(golden("task_versions.json"))


def stored_rows() -> list[SessionRun]:
    return [SessionRun.model_validate_json(line) for line in STORED.read_text().splitlines()]


def test_stored_sessions_parse_with_their_settings_and_conditions() -> None:
    specs = {case.id: spec for case, spec in benchmark_tasks()}
    read = [
        [run.case, run.trial, run.setting.id, condition_of(run, specs[run.case])]
        for run in stored_rows()
    ]
    assert read == json.loads(golden("stored_sessions_read.json"))


@pytest.fixture
def stored_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A data folder holding the stored sessions as current sessions of two models."""
    versions = {case.id: spec.version for case, spec in benchmark_tasks()}
    folder = tmp_path / "data" / "cases" / "sessions"
    folder.mkdir(parents=True)
    for model in (MODEL, SECOND):
        rows = []
        for run in stored_rows():
            current = {"harness": HARNESS, "session_version": versions[run.case], "model": model}
            rows.append(run.model_copy(update=current).model_dump_json())
        (folder / f"{model}.jsonl").write_text("".join(row + "\n" for row in rows))
    monkeypatch.setenv("STEWARD_DATA", str(tmp_path / "data"))
    return tmp_path / "data"


def test_the_score_of_stored_sessions_is_unchanged(
    stored_data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    command = ["bench-score", "--model", MODEL, "--model", SECOND, "--simulator-model", MODEL]
    assert main(command) == 0
    assert capsys.readouterr().out == golden("score.txt")


def test_the_estimate_from_stored_sessions_is_unchanged(
    stored_data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["bench-estimate", "--model", MODEL, "--model", "gpt-6.2-sol"]) == 0
    assert capsys.readouterr().out == golden("estimate.txt")


class Environment:
    """Answers every command with a fixed text and keeps what it was sent."""

    def __init__(self, sent: list[str]) -> None:
        self.sent = sent

    async def execute(self, action: Action) -> str:
        self.sent.append(action.text)
        return "done"

    async def run(self, command: str) -> str:
        self.sent.append(command)
        return ""

    async def succeeds(self, command: str) -> bool:
        self.sent.append(command)
        return True

    async def checked(self, command: str, limit: int = 400) -> tuple[bool, str]:
        self.sent.append(command)
        return True, "checked"


class Agent:
    """On every shift asks each person present by name, runs one command and reports; keeps
    every message list it was given."""

    def __init__(self, names: list[str], seen: list[list[Message]]) -> None:
        self.names = names
        self.seen = seen
        self.left: list[Turn] = []

    def begin_item(self, values: dict[str, str]) -> None:
        asks = [Turn(message=f"{name}, has anything changed today?") for name in self.names]
        command = Turn(action=Action(tool="bash", arguments={"command": "ls"}))
        self.left = [*asks, command, Turn(report="Done as asked.")]

    def next_turn(self, messages: list[Message]) -> Turn:
        self.seen.append(json.loads(json.dumps(messages)))
        return self.left.pop(0)


class Simulator(CachedSimulator):
    """Keeps every request it is sent and answers each with a fixed reply."""

    def __init__(self, cache: Path, asked: list[object]) -> None:
        super().__init__(cache, "recorder")
        self.asked = asked

    def _ask(self, system: str, chat: list[str]) -> str:
        self.asked.append([system, chat])
        return "Nothing new from me."


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def session_texts(case: Case, spec: SessionSpec, cache_dir: Path) -> object:
    texts = []
    for condition in conditions_of(spec):
        setting = benchmark_setting(case.id, spec, condition, 0)
        seen: list[list[Message]] = []
        sent: list[str] = []
        asked: list[object] = []
        people: list[Person] = [
            spec.manager if r == "manager" else case.person(r) for r in setting.present
        ]
        agent = Agent([person.name.split()[0] for person in people[1:]], seen)
        session = Session(
            case,
            spec,
            setting,
            Environment(sent),  # type: ignore[arg-type]
            agent,
            [{"role": "system", "content": "You are the agent."}],
            Simulator(cache_dir / condition, asked),
        )
        events, _ = asyncio.run(session.run())
        recorded = [[e.who, e.kind, e.name, e.text] for e in events]
        texts.append([condition, digest(seen), digest(sent), digest(asked), digest(recorded)])
    return texts


def probe_texts(case: Case, spec: SessionSpec, cache_dir: Path) -> object:
    asked: list[object] = []
    simulator = Simulator(cache_dir / "probe", asked)
    results = []
    for setting, target in probed(case, spec):
        for probe in target.sent:
            result = run_probe(
                case, spec, setting, target, probe, simulator, Pricing(prompt=1, completion=1)
            )
            if result is not None:
                results.append([result.message, result.passed, result.revealed])
    return [digest(asked), digest(results)]


def test_what_the_agent_and_the_simulator_are_shown_is_unchanged(tmp_path: Path) -> None:
    shown = {
        case.id: [
            session_texts(case, spec, tmp_path / case.id),
            probe_texts(case, spec, tmp_path / case.id),
        ]
        for case, spec in benchmark_tasks()
    }
    steps = [
        Step(index=0, kind="action", content="", tool="bash", tool_input={"command": "ls"}),
        Step(
            index=1,
            kind="action",
            content="",
            tool="send_email",
            tool_input={"to": "a", "body": "b"},
        ),
    ]
    shown["tools"] = [digest(tool_definitions(steps)), digest(tool_definitions([]))]
    assert shown == json.loads(golden("shown.json"))
