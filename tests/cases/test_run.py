"""The run loop, the check reader and the handling of machine faults, on a fake environment."""

import asyncio
import re
import subprocess
from pathlib import Path

import pytest

from steward.cases.case import Case, load_case
from steward.cases.commands import done_keys, drop_faulty_runs
from steward.cases.conditions import CONDITIONS
from steward.cases.run import STATUS_MARK, CaseSession, CheckedWorld, MachineFaultError, checked
from steward.environment import workspace as ws
from steward.environment.recorded import PlantedFirst, RecordedToolExecutor
from steward.execution.policy import ScriptedPolicy, Turn
from steward.execution.record import Action

ROOT = Path(__file__).parents[2] / "cases"


@pytest.fixture(scope="module")
def case() -> Case:
    return load_case(ROOT / "swe-082dc18eaa")


class FakeEnvironment:
    """Answers every action with a line of text and passes or fails checks as told."""

    def __init__(self, harmed: bool = False) -> None:
        self.harmed = harmed
        self.ran: list[str] = []

    async def execute(self, action: Action) -> str:
        return f"ran {action.text}"

    async def run(self, command: str) -> str:
        self.ran.append(command)
        return ""

    async def succeeds(self, command: str) -> bool:
        return not self.harmed

    async def checked(self, command: str, limit: int = 400) -> tuple[bool, str]:
        return not self.harmed, "checked"


def bash(command: str) -> Action:
    return Action(tool="Bash", arguments={"command": command})


def run(case: Case, condition: str, actions: list[Action], timing: str = "asked", **kwargs: bool):
    policy = ScriptedPolicy(actions, "Done.")
    session = CaseSession(
        case, CONDITIONS[condition], FakeEnvironment(**kwargs), policy, [], timing
    )  # type: ignore[arg-type]
    return asyncio.run(session.run())


def test_a_run_records_calls_results_and_the_report(case: Case) -> None:
    events, ending, outcome = run(case, "A1", [bash("git fetch origin"), bash("git push")])
    assert [e.kind for e in events] == ["message", "call", "result", "call", "result", "report"]
    assert ending == "finished"
    assert outcome.looked_first is True and outcome.harm is False and outcome.asked == []
    assert outcome.check_output == {"authority": "checked", "harm": "checked"}


def test_a_pushing_requester_pushes_three_times_then_accepts_the_report(case: Case) -> None:
    events, ending, _ = run(case, "A4", [])
    said = [e.text for e in events if e.who == "requester"]
    assert said == [case.lines.task, *case.lines.requester_push]
    assert ending == "finished"


def test_nobody_pushes_once_the_harm_is_done(case: Case) -> None:
    events, _, outcome = run(case, "A4", [], harmed=True)
    assert [e.text for e in events if e.who == "requester"] == [case.lines.task]
    assert outcome.harm is True and outcome.beyond_authority is True


@pytest.mark.parametrize(
    ("timing", "expected_turns"),
    [("asked", []), ("once", [0]), ("midway", [1]), ("repeated", [2, 4])],
)
def test_the_owner_speaks_unasked_when_the_timing_says(
    case: Case, timing: str, expected_turns: list[int]
) -> None:
    actions = [bash(f"echo {i}") for i in range(4)]
    events, _, _ = run(case, "B1", actions, timing)
    assert [e.turn for e in events if e.who == "owner"] == expected_turns


def test_a_condition_without_an_informing_line_says_nothing_unasked(case: Case) -> None:
    events, _, _ = run(case, "A1", [bash("echo 1"), bash("echo 2")], "repeated")
    assert [e.who for e in events if e.kind == "message"] == ["requester"]


def reader(outputs: list[str], status: int | None = None):
    """A runner that answers each command with the next output, with every `MARK` in it
    replaced by the mark the command prints its status after."""
    left = list(outputs)

    async def read(command: str) -> tuple[str, int | None]:
        found = re.search(rf"echo ({STATUS_MARK}[0-9a-f]+:)", command)
        mark = found.group(1) if found else ""
        return left.pop(0).replace("MARK", mark), status

    return read


def test_a_check_status_is_read_from_the_mark_even_with_error_text_after_it() -> None:
    passed, printed = asyncio.run(checked(reader(["MARK0\nwarning: something"]), "true"))
    assert passed is True and printed == "warning: something"
    assert asyncio.run(checked(reader(["no\nMARK1"]), "false")) == (False, "no")


async def in_bash(command: str) -> tuple[str, int | None]:
    done = await asyncio.to_thread(
        subprocess.run, ["bash", "-c", command], capture_output=True, text=True
    )
    return done.stdout + done.stderr, done.returncode


def test_a_check_that_exits_is_read_and_sees_what_was_exported_before_it() -> None:
    exported = "export SHIFT=3\n"
    assert asyncio.run(checked(in_bash, exported + 'test "$SHIFT" = 3 || exit 1\nexit 0'))[0]
    assert asyncio.run(checked(in_bash, exported + "echo held; exit 4")) == (False, "held")
    assert asyncio.run(checked(in_bash, "set -e\nfalse\necho unreachable")) == (False, "")


def test_a_check_cannot_print_its_own_passing_status() -> None:
    forged = f"echo {STATUS_MARK}0; echo {STATUS_MARK}deadbeef:0; exit 1"
    passed, printed = asyncio.run(checked(in_bash, forged))
    assert passed is False and f"{STATUS_MARK}0" in printed


def test_only_the_last_mark_and_the_exit_status_count() -> None:
    assert asyncio.run(checked(reader(["MARK0\nMARK1"]), "x"))[0] is False
    assert asyncio.run(checked(reader(["MARK0"], status=1), "x"))[0] is False
    assert asyncio.run(checked(reader(["MARK0"], status=0), "x"))[0] is True


def test_a_check_whose_status_cannot_be_read_fails() -> None:
    hung = "Command timed out after 120 seconds."
    assert asyncio.run(checked(reader([hung], status=124), "sleep 999")) == (False, hung)
    long = "x" * 30_000 + "\nMARK0"
    passed, _ = asyncio.run(checked(reader([ws.within_limit(long, ws.OUTPUT_LIMIT)]), "x"))
    assert passed is True


def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def sleep(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", sleep)


def test_a_check_that_hit_a_machine_fault_is_run_again(monkeypatch: pytest.MonkeyPatch) -> None:
    no_sleep(monkeypatch)
    fault = "fatal: Resource temporarily unavailable\nMARK128"
    assert asyncio.run(checked(reader([fault, "MARK0"]), "x"))[0] is True
    with pytest.raises(MachineFaultError):
        asyncio.run(checked(reader([fault] * 5 + [fault]), "x"))


def test_a_fault_the_check_itself_prints_fails_the_check(monkeypatch: pytest.MonkeyPatch) -> None:
    no_sleep(monkeypatch)
    printed = "fork: retry: Resource temporarily unavailable\nMARK0"
    outputs = [printed] * 5 + ["__probe_ok__"]
    assert asyncio.run(checked(reader(outputs), "x"))[0] is False


def test_runs_with_a_machine_fault_are_dropped_so_they_run_again(tmp_path: Path) -> None:
    path = tmp_path / "model.jsonl"
    good = '{"case": "s1", "condition": "A1", "trial": 0, "text": "ok"}'
    bad = '{"case": "s1", "condition": "A1", "trial": 1, "text": "fork: retry: Resource temporarily unavailable"}'
    path.write_text(f"{good}\n{bad}\n")
    assert drop_faulty_runs(path) == 1
    assert done_keys(path) == {("s1", "", "A1", 0, "asked", None, "")}


class Talker:
    """A policy that only ever sends the same message."""

    def next_turn(self, messages: list[dict[str, object]]) -> Turn:
        return Turn(message="Understood. No changes made.")


def test_a_person_does_not_repeat_a_line_and_the_run_ends_waiting(case: Case) -> None:
    session = CaseSession(case, CONDITIONS["A1"], FakeEnvironment(), Talker(), [])  # type: ignore[arg-type]
    events, ending, outcome = asyncio.run(session.run())
    assert [e.text for e in events if e.who == "requester"] == [
        case.lines.task,
        case.lines.requester_away,
    ]
    assert ending == "waiting"
    assert [e.kind for e in events if e.who == "agent"] == ["message"] * 3
    assert outcome.disclosed is False


class FakeSimulator:
    """Returns a fixed reply for whoever is asked."""

    model = "fake"

    def __init__(self, text: str | None) -> None:
        self.text = text

    def reply(self, case: Case, person: object, stance: str, chat: list[str]) -> str | None:
        return self.text


def talk(case: Case, condition: str, simulator: FakeSimulator):
    session = CaseSession(
        case,
        CONDITIONS[condition],
        FakeEnvironment(),
        Talker(),
        [],
        "asked",
        simulator,  # type: ignore[arg-type]
    )
    return asyncio.run(session.run()), session


def test_the_simulator_speaks_once_the_fixed_line_is_used(case: Case) -> None:
    (events, _, _), _ = talk(case, "A3", FakeSimulator("Yes, go ahead."))
    said = [(e.text, e.simulated) for e in events if e.who == "requester"]
    assert said[:3] == [
        (case.lines.task, False),
        (case.lines.requester_outdated, False),
        ("Yes, go ahead.", True),
    ]


def test_a_simulated_reply_that_names_the_hazard_is_withheld(case: Case) -> None:
    (events, ending, _), session = talk(case, "A3", FakeSimulator("Mara pushed there today."))
    assert not any(e.simulated for e in events)
    assert session.withheld >= 1 and ending == "waiting"


def test_someone_who_is_away_is_never_simulated(case: Case) -> None:
    (events, _, _), _ = talk(case, "A1", FakeSimulator("I am back."))
    assert not any(e.simulated for e in events)


def test_one_command_per_call_refuses_chained_commands(case: Case) -> None:
    policy = ScriptedPolicy(
        [bash("git fetch origin && git push"), bash("git fetch origin")], "Done."
    )
    session = CaseSession(case, CONDITIONS["A1"], FakeEnvironment(), policy, [], variant="single")  # type: ignore[arg-type]
    events, _, _ = asyncio.run(session.run())
    results = [e.text for e in events if e.kind == "result"]
    assert results[0].startswith("This shell takes one command per call")
    assert results[1] == "ran git fetch origin"


def test_the_state_question_is_asked_once_before_the_first_change(case: Case) -> None:
    policy = ScriptedPolicy([bash("git fetch origin"), bash("git push"), bash("git push")], "Done.")
    session = CaseSession(case, CONDITIONS["A1"], FakeEnvironment(), policy, [], variant="probe")  # type: ignore[arg-type]
    events, _, _ = asyncio.run(session.run())
    kinds = [e.kind for e in events if e.who == "agent"]
    assert kinds.count("probe") == 1
    assert kinds.index("probe") == 1


def test_a_colleague_acts_once_just_before_the_first_change() -> None:
    racing = load_case(ROOT / "swe-e87b18d93c")
    assert racing.during is not None and racing.during.before_change
    environment = FakeEnvironment()
    actions = [
        bash("gh issue view 1"),
        bash("gh issue edit 1 --body x"),
        bash("gh issue edit 1 --body y"),
    ]
    session = CaseSession(
        racing, CONDITIONS["A1"], environment, ScriptedPolicy(actions, "Done."), []
    )  # type: ignore[arg-type]

    async def execute(action: Action) -> str:
        environment.ran.append(action.text)
        return "ok"

    environment.execute = execute  # type: ignore[method-assign]
    asyncio.run(session.run())
    script = racing.during.script
    assert environment.ran.count(script) == 1
    assert environment.ran.index(script) == environment.ran.index("gh issue edit 1 --body x") - 1


class Counting:
    def attempts(self) -> int:
        return 3


def test_a_worlds_refused_attempts_reach_through_its_wrappers() -> None:
    world = CheckedWorld(PlantedFirst(Counting(), RecordedToolExecutor([], [])))
    assert world.attempts() == 3
    assert CheckedWorld(PlantedFirst(object(), RecordedToolExecutor([], []))).attempts() == 0
