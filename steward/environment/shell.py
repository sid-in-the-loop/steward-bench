"""A persistent terminal inside a Harbor environment, and the replay of a recorded history.

Recorded terminal sessions depend on shell state (working directory, variables, background
processes), so commands go to one tmux session, as they did in the recording.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import PurePosixPath

from harbor.agents.terminus_2.tmux_session import TmuxSession
from harbor.environments.base import BaseEnvironment

from steward.schema import Step

log = logging.getLogger(__name__)

# Visible inside the environment, so it must not hint at a benchmark.
SESSION_NAME = "main"
SETTLE_INTERVAL_SEC = 1.5
REPLAYED_TOOLS = frozenset({"bash_command", "Bash", "bash", "shell", "execute_bash"})


class TmuxShell:
    def __init__(self, environment: BaseEnvironment, command_timeout_sec: float = 180.0) -> None:
        self.environment = environment
        self.command_timeout_sec = command_timeout_sec
        self.session = TmuxSession(
            session_name=SESSION_NAME,
            environment=environment,
            logging_path=PurePosixPath("/logs/agent") / f"{SESSION_NAME}.pane",
            local_asciinema_recording_path=None,
            remote_asciinema_recording_path=None,
            user=environment.default_user,
        )

    async def start(self) -> None:
        await self.session.start()

    async def keys(self, keystrokes: str, wait_sec: float) -> str:
        """Send keystrokes exactly as recorded and return the new terminal output."""
        await self.session.send_keys(keystrokes, block=False, min_timeout_sec=wait_sec)
        return await self.session.get_incremental_output()

    async def settle(self) -> None:
        """Wait until the terminal stops changing. No marker is written to the terminal, so
        nothing distinguishes a harness command from one a person typed."""
        previous = await self.session.capture_pane(capture_entire=True)
        waited = 0.0
        while waited < self.command_timeout_sec:
            await asyncio.sleep(SETTLE_INTERVAL_SEC)
            waited += SETTLE_INTERVAL_SEC
            current = await self.session.capture_pane(capture_entire=True)
            if current == previous:
                return
            previous = current

    async def run(self, command: str) -> str:
        await self.session.send_keys([command, "Enter"], block=False, min_timeout_sec=0.2)
        await self.settle()
        return await self.session.get_incremental_output()

    async def succeeds(self, command: str) -> bool:
        result = await self.environment.exec(command, timeout_sec=int(self.command_timeout_sec))
        return result.return_code == 0


def recorded_keystrokes(step: Step) -> tuple[str, float] | None:
    """The keystrokes and wait of a recorded terminal action, or None for other actions."""
    if step.kind != "action" or step.tool not in REPLAYED_TOOLS:
        return None
    arguments = step.tool_input or {}
    keystrokes = arguments.get("keystrokes") or arguments.get("command")
    if not isinstance(keystrokes, str) or not keystrokes:
        return None
    if "keystrokes" not in arguments and not keystrokes.endswith("\n"):
        keystrokes += "\n"
    duration = arguments.get("duration")
    return keystrokes, float(duration) if isinstance(duration, int | float) else 1.0


async def replay(shell: TmuxShell, steps: list[Step]) -> int:
    """Re-run the recorded terminal actions in order; returns how many were replayed."""
    replayed = 0
    for step in steps:
        recorded = recorded_keystrokes(step)
        if recorded is None:
            continue
        keystrokes, wait_sec = recorded
        await shell.keys(keystrokes, min(wait_sec, shell.command_timeout_sec))
        replayed += 1
    log.info("replayed %d recorded actions", replayed)
    return replayed
