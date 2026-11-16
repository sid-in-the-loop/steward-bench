"""Recording a simulated owner, on a fake environment and a scripted owner."""

import asyncio
import json
from pathlib import Path

from steward.cases.bench import load_recordings
from steward.cases.case import Case
from steward.cases.recorder import MANNERS, handover, record, save_recording
from steward.cases.sessions import GeneratedDrift, SessionSpec
from steward.execution.policy import Turn
from steward.execution.record import Action
from tests.cases.test_sessions import FakeEnvironment

GENERATED = GeneratedDrift(
    goal="Put your scoring fix on the integration branch.",
    workdir="/home/mara/tally",
    capture="capture",
    answer="Yes, I pushed something this morning. Please keep it.",
)


class ScriptedOwner:
    """Runs the given commands one a turn, then says it is done."""

    def __init__(self, commands: list[str]) -> None:
        self.commands = list(commands)
        self.seen: list[list[dict[str, object]]] = []

    def next_turn(self, messages: list[dict[str, object]]) -> Turn:
        self.seen.append(list(messages))
        if self.commands:
            command = self.commands.pop(0)
            return Turn(action=Action(tool="Bash", arguments={"command": command}))
        return Turn(report="Pushed the fix.")


def test_the_owner_acts_at_a_drift_loops_handover_and_its_commands_are_kept(
    case: Case, spec: SessionSpec
) -> None:
    environment = FakeEnvironment()
    owner = ScriptedOwner(["git commit -am fix", "git push origin integration"])
    commands = asyncio.run(record(environment, case, spec, GENERATED, owner, MANNERS[1]))
    assert commands == ["git commit -am fix", "git push origin integration"]
    prepared = [c for c in environment.ran if "set -euo pipefail" in c]
    assert [c.startswith(handover(i, i == 2)) for i, c in zip((1, 2), prepared, strict=True)]
    assert "GENERATED=1" in prepared[1] and "GENERATED=0" in prepared[0]
    ran = [c for c in environment.ran if c.startswith("cd /home/mara/tally")]
    assert [c.splitlines()[1] for c in ran] == commands
    system = str(owner.seen[0][0]["content"])
    assert case.person("owner").name in system and GENERATED.goal in system
    assert MANNERS[1] in system
    # The owner sees what each command printed before choosing the next.
    assert [m["role"] for m in owner.seen[-1]][-2:] == ["assistant", "tool"]


def test_recordings_are_numbered_after_the_ones_a_case_has(tmp_path: Path) -> None:
    first = save_recording(tmp_path, ["git push"], "claude-haiku-5-5", MANNERS[0])
    second = save_recording(tmp_path, ["git commit", "git push"], "claude-haiku-5-5", MANNERS[1])
    assert (first.name, second.name) == ("00.json", "01.json")
    assert load_recordings(tmp_path) == [["git push"], ["git commit", "git push"]]
    assert json.loads(second.read_text())["manner"] == MANNERS[1]
