"""Actions inside a Harbor environment: a persistent terminal, file tools, and history replay.

Recorded terminal sessions depend on shell state (working directory, variables, background
processes), so commands go to one tmux session, as they did in the recording. File tools act on
the container's files directly, as the recorded harness's did.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import shlex
from pathlib import PurePosixPath

from harbor.agents.terminus_2.tmux_session import TmuxSession
from harbor.environments.base import BaseEnvironment

from steward.execution.record import Action
from steward.schema import Step

log = logging.getLogger(__name__)

# Visible inside the environment, so it must not hint at a benchmark.
SESSION_NAME = "main"
SETTLE_INTERVAL_SEC = 1.5
REPLAYED_TOOLS = frozenset({"bash_command", "Bash", "bash", "shell", "execute_bash"})
READ_TOOLS = frozenset({"Read", "read_file"})
WRITE_TOOLS = frozenset({"Write", "write_file"})
EDIT_TOOLS = frozenset({"Edit"})
SEARCH_TOOLS = frozenset({"Grep", "Glob"})
OUTPUT_LIMIT = 20_000
UNAVAILABLE = "This tool is not available in this session."

# Runs inside the container, so an edit behaves like the recorded harness's: the old text must
# be present, and is replaced once unless replace_all is set.
EDIT_SCRIPT = """
import base64, json, pathlib, sys
a = json.loads(base64.b64decode(sys.argv[1]))
path = pathlib.Path(a["file_path"])
if not path.is_file():
    sys.exit(f"File does not exist: {path}")
text = path.read_text()
if a["old_string"] not in text:
    sys.exit("String to replace not found in file.")
path.write_text(text.replace(a["old_string"], a["new_string"], -1 if a.get("replace_all") else 1))
print(f"The file {path} has been updated.")
"""


class ContainerExecutor:
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

    async def _exec(self, command: str) -> str:
        result = await self.environment.exec(command, timeout_sec=int(self.command_timeout_sec))
        output = (result.stdout or "") + (result.stderr or "")
        return output[:OUTPUT_LIMIT] or "(no output)"

    async def execute(self, action: Action) -> str:
        arguments = action.arguments
        if action.tool in REPLAYED_TOOLS:
            if isinstance(arguments.get("keystrokes"), str):
                wait_sec = float(arguments.get("duration") or 1.0)
                return await self.keys(arguments["keystrokes"], min(wait_sec, 60.0))
            return await self.run(str(arguments.get("command", "")))
        path = shlex.quote(str(arguments.get("file_path") or arguments.get("path") or "."))
        if action.tool in READ_TOOLS:
            return await self._exec(f"cat -n -- {path}")
        if action.tool in WRITE_TOOLS:
            content = base64.b64encode(str(arguments.get("content", "")).encode()).decode()
            written = await self.environment.exec(
                f'mkdir -p -- "$(dirname -- {path})" && echo {content} | base64 -d > {path}'
            )
            return (
                f"File written: {arguments.get('file_path')}"
                if written.return_code == 0
                else (written.stderr or "Write failed.")
            )
        if action.tool in EDIT_TOOLS:
            encoded = base64.b64encode(json.dumps(arguments).encode()).decode()
            return await self._exec(f"python3 -c {shlex.quote(EDIT_SCRIPT)} {encoded}")
        if action.tool == "Grep":
            pattern = shlex.quote(str(arguments.get("pattern", "")))
            return await self._exec(f"grep -rnE -- {pattern} {path} | head -200")
        if action.tool == "Glob":
            pattern = shlex.quote(str(arguments.get("pattern", "*")))
            return await self._exec(f"find {path} -path {pattern} -o -name {pattern} | head -200")
        return UNAVAILABLE


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


async def replay(shell: ContainerExecutor, steps: list[Step]) -> int:
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
