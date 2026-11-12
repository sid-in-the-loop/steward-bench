"""Actions inside a Harbor environment: a terminal, file tools, and re-running a recorded prefix.

A harness that typed into one terminal gets one tmux session, so shell state carries over as
it did in the recording. A harness that started a new shell for each command gets a new shell
for each command. File tools act on the container's files.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import shlex
import time
from pathlib import PurePosixPath

from harbor.agents.terminus_2.tmux_session import TmuxSession
from harbor.environments.base import BaseEnvironment

from steward.environment.fidelity import ReplayFidelity, ReplayLog
from steward.environment.tools import FILE_TOOLS, ToolError, apply_file_tool, changes_files
from steward.environment.workspace import SHELL_TOOLS, command_of, wrapped
from steward.execution.record import Action
from steward.schema import Step

log = logging.getLogger(__name__)

# Visible inside the environment, so it must not hint at a benchmark.
SESSION_NAME = "main"
SETTLE_INTERVAL_SEC = 1.5
SEARCH_TOOLS = frozenset({"Grep", "Glob"})
OUTPUT_LIMIT = 20_000
WRITE_CHUNK_BYTES = 45_000
UNAVAILABLE = "This tool is not available in this session."
# The prompt a recorded terminal showed: user, host and the rest.
PROMPT = re.compile(r"^(\w+)@([\w.-]+):\S* ?[#$] ", re.MULTILINE)


class ContainerFiles:
    """Files inside the task container, read and written through its shell."""

    def __init__(self, environment: BaseEnvironment) -> None:
        self.environment = environment

    async def read(self, path: str) -> str | None:
        result = await self.environment.exec(f"base64 -w0 -- {shlex.quote(path)}")
        if result.return_code != 0:
            return None
        return base64.b64decode(result.stdout or "").decode(errors="surrogateescape")

    async def write(self, path: str, content: str) -> None:
        quoted = shlex.quote(path)
        data = content.encode(errors="surrogateescape")
        made = await self.environment.exec(f'mkdir -p -- "$(dirname -- {quoted})" && : > {quoted}')
        if made.return_code != 0:
            raise ToolError((made.stderr or made.stdout or f"Cannot write {path}").strip())
        for start in range(0, len(data), WRITE_CHUNK_BYTES):
            chunk = base64.b64encode(data[start : start + WRITE_CHUNK_BYTES]).decode()
            await self.environment.exec(f"echo {chunk} | base64 -d >> {quoted}")

    async def delete(self, path: str) -> None:
        await self.environment.exec(f"rm -f -- {shlex.quote(path)}")


class ContainerExecutor:
    """Runs actions in the task container the way the recorded harness did: keystrokes go to
    one persistent terminal, a harness that started a new shell for every command gets a new
    shell for every command, and file tools act on the container's files."""

    def __init__(self, environment: BaseEnvironment, command_timeout_sec: float = 180.0) -> None:
        self.environment = environment
        self.command_timeout_sec = command_timeout_sec
        self.files = ContainerFiles(environment)
        self.session = TmuxSession(
            session_name=SESSION_NAME,
            environment=environment,
            logging_path=PurePosixPath("/logs/agent") / f"{SESSION_NAME}.pane",
            local_asciinema_recording_path=None,
            remote_asciinema_recording_path=None,
            user=environment.default_user,
        )

    async def start(self, host: str | None = None) -> None:
        """Start the terminal. With `host`, its prompt names that host, as the recorded
        terminal's did, and not the machine this runs on."""
        await self.session.start()
        if host is None:
            return
        prompt = shlex.quote(rf"\u@{host}:\w\$ ")
        await self.session.send_keys(
            [f" export PS1={prompt}; clear", "Enter"], block=False, min_timeout_sec=0.5
        )
        await self.environment.exec(f"tmux clear-history -t {SESSION_NAME}")
        await self.session.get_incremental_output()

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

    async def command(self, tool: str, command: str, workdir: str | None) -> str:
        """Run a command in a new shell and report it in the harness's own format."""
        started = time.monotonic()
        try:
            result = await self.environment.exec(
                command, cwd=workdir, timeout_sec=int(self.command_timeout_sec)
            )
            output = ((result.stdout or "") + (result.stderr or ""))[:OUTPUT_LIMIT]
            status = result.return_code
        except TimeoutError:
            output, status = f"Command timed out after {self.command_timeout_sec:.0f} seconds.", 124
        return wrapped(tool, command, output, status, time.monotonic() - started)

    def handles(self, action: Action) -> bool:
        return action.tool in SHELL_TOOLS | FILE_TOOLS | SEARCH_TOOLS

    async def execute(self, action: Action) -> str:
        arguments = action.arguments
        if action.tool in SHELL_TOOLS:
            if isinstance(arguments.get("keystrokes"), str):
                wait_sec = float(arguments.get("duration") or 1.0)
                return await self.keys(arguments["keystrokes"], min(wait_sec, 60.0))
            workdir = arguments.get("workdir")
            return await self.command(
                action.tool,
                command_of(arguments, action.tool) or "",
                workdir if isinstance(workdir, str) else None,
            )
        outcome = await apply_file_tool(self.files, action.tool, arguments)
        if outcome is not None:
            return outcome[0]
        path = shlex.quote(str(arguments.get("file_path") or arguments.get("path") or "."))
        if action.tool == "Grep":
            pattern = shlex.quote(str(arguments.get("pattern", "")))
            return await self._exec(f"grep -rnE -- {pattern} {path} | head -200")
        if action.tool == "Glob":
            pattern = shlex.quote(str(arguments.get("pattern", "*")))
            return await self._exec(f"find {path} -path {pattern} -o -name {pattern} | head -200")
        return UNAVAILABLE


def recorded_host(steps: list[Step]) -> str | None:
    """The host name in the prompt of the recorded terminal, if the recording shows one."""
    for step in steps:
        found = PROMPT.search(step.content) if step.kind == "result" else None
        if found:
            return found.group(2)
    return None


async def replay(shell: ContainerExecutor, steps: list[Step]) -> ReplayFidelity:
    """Re-run the recorded prefix in order through the same executor the tested model will
    use, comparing what each command printed with the result the recording holds for it."""
    log_ = ReplayLog()
    for step in steps:
        if step.kind == "result":
            log_.result(step.content)
            continue
        if step.kind != "action":
            continue
        tool, arguments = step.tool or "", step.tool_input or {}
        if tool not in SHELL_TOOLS and not changes_files(tool):
            log_.skipped()
            continue
        action = Action(tool=tool, arguments=arguments)
        output = await shell.execute(action)
        if isinstance(arguments.get("keystrokes"), str):
            # The recorded agent took time to answer between commands, so a command had
            # longer to finish than the wait it asked for. Let the terminal come to rest.
            await shell.settle()
            output += await shell.session.get_incremental_output()
        if tool in SHELL_TOOLS:
            log_.ran(step.index, action.text, None, output)
        else:
            log_.skipped()
    fidelity = log_.flush()
    log.info("replayed %d recorded actions", fidelity.replayed)
    return fidelity
