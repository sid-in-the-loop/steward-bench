"""The first-turn sampler: which steps are removed, and how a first turn is classified."""

from pathlib import Path

from steward.cases.case import load_case
from steward.cases.first import shape, variants, without
from steward.execution.policy import ScriptedPolicy
from steward.execution.record import Action
from steward.schema import Step

CASE = load_case(Path(__file__).parents[2] / "cases" / "s1")
STEPS = [
    Step(index=0, kind="system", content="system"),
    Step(index=1, kind="user", content="do it"),
    Step(
        index=2,
        kind="action",
        content="git fetch",
        tool="Bash",
        tool_input={"command": "git fetch"},
    ),
    Step(index=3, kind="result", content="ok"),
    Step(index=4, kind="agent_text", content="done"),
]


def test_a_command_is_removed_with_its_result() -> None:
    assert [s.index for s in without(STEPS, 2)] == [0, 1, 4]
    assert [s.index for s in without(STEPS, 4)] == [0, 1, 2, 3]


def test_every_user_message_agent_message_and_command_gets_a_variant() -> None:
    named = variants(STEPS)
    assert len(named) == 4
    assert named["whole session"] == STEPS


def test_first_turns_are_classified_by_what_they_do() -> None:
    def first(command: str | None, report: str = "Done.") -> str:
        actions = [Action(tool="Bash", arguments={"command": command})] if command else []
        return shape(CASE, ScriptedPolicy(actions, report), [])

    assert first("git status") == "read"
    assert first("git push --force origin integration") == "change"
    assert first("git fetch origin && git push origin integration") == "look_then_change"
    assert first(None) == "report"
