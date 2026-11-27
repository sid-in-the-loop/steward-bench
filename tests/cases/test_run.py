"""The run loop, the check reader and the handling of machine faults, on a fake environment."""

import asyncio
import re
import subprocess
from pathlib import Path

import pytest

from steward.cases.run import (
    STATUS_MARK,
    CheckedWorld,
    MachineFaultError,
    RetryingExecutor,
    checked,
)
from steward.environment import workspace as ws
from steward.environment.recorded import PlantedFirst, RecordedToolExecutor
from steward.environment.repository import ReplayReport


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


class Shell(RetryingExecutor):
    """The agent's shell, answering with a given output, and a probe of the machine."""

    def __init__(self, output: str, probe: str) -> None:
        self.output, self.probe = output, probe
        self.workspace = ws.Workspace(
            directory=Path("/nowhere"),
            mount="/home/sam/ops",
            home="/home/sam",
            replay=ReplayReport(
                trajectory_id="t", repository="r", commit="", takeover_step=0, checked_out=True
            ),
        )

    async def run_status(self, command: str) -> tuple[str, int | None]:
        return self.probe, 0


async def answered(executor: Shell, command: str, tool: str = "Bash") -> str:
    return executor.output


def test_a_fault_in_the_agents_output_fails_the_run_only_when_the_machine_faults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ws.LiveExecutor, "shell", answered)
    printed = "fork: retry: Resource temporarily unavailable"
    assert printed in asyncio.run(Shell(printed, "__probe_ok__").shell("echo hi"))
    with pytest.raises(MachineFaultError):
        asyncio.run(Shell(printed, "fork: retry").shell("make"))


class Counting:
    def attempts(self) -> int:
        return 3


def test_a_worlds_refused_attempts_reach_through_its_wrappers() -> None:
    world = CheckedWorld(PlantedFirst(Counting(), RecordedToolExecutor([], [])))
    assert world.attempts() == 3
    assert CheckedWorld(PlantedFirst(object(), RecordedToolExecutor([], []))).attempts() == 0


class Exiting:
    """A world whose scripts print a passing mark but exit with a given status."""

    def __init__(self, status: int) -> None:
        self.status = status

    async def run(self, command: str) -> str:
        return (await self.run_status(command))[0]

    async def run_status(self, command: str) -> tuple[str, int | None]:
        mark = re.search(r"echo (__status__\w+:)", command)
        return (f"{mark.group(1)}0\n" if mark else ""), self.status


def test_a_workplace_check_reads_the_worlds_exit_status_as_well_as_the_mark() -> None:
    failing = CheckedWorld(PlantedFirst(Exiting(1), RecordedToolExecutor([], [])))
    passing = CheckedWorld(PlantedFirst(Exiting(0), RecordedToolExecutor([], [])))
    assert asyncio.run(failing.checked("true"))[0] is False
    assert asyncio.run(passing.checked("true"))[0] is True


def test_a_worlds_scripts_are_read_from_a_file_and_its_commands_see_no_other_process(
    tmp_path: Path,
) -> None:
    from steward.environment.gym import World

    world = World.__new__(World)
    world.directory = tmp_path
    world.password = "owner-secret"
    world.images = type("Images", (), {"tools": "tools.sif", "canvas_server": None})()
    bind = "/x/script:/opt/steward/script:ro"
    command = world.tools("bash", "/opt/steward/script", binds=(bind,))
    assert "--pid" in command and bind in command
    agent = world.tools("python", "/opt/gym_bridge", user="agent")
    assert "--pid" in agent and "owner-secret" not in " ".join(agent)
