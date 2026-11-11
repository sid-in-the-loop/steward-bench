"""Live local tools for workplace sessions.

A workplace session's filesystem, terminal and Python tools worked on a directory of task
files. That directory is rebuilt from the task's published starting files and the session's
own recorded writes, and those tools then run for real, with the output formats the session
recorded. Tools that reach remote services are still answered from the recording.
"""

from __future__ import annotations

import asyncio
import difflib
import fnmatch
import json
import shutil
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from steward.environment.recorded import RecordedToolExecutor
from steward.environment.repository import ReplayReport
from steward.environment.workspace import Workspace, container_command, run_in_container
from steward.execution.record import Action
from steward.schema import Step

MOUNT = "/workspace/dumps/workspace"
HOME = "/root"
FILESYSTEM = "filesystem-"
TERMINAL = "terminal-run_command"
PYTHON = "local-python-execute"
PYTHON_TIMEOUT_SEC = 30
READ_LIMIT = 100_000


def block(text: str, error: bool = False) -> str:
    """One text block in the format the session's tool server returned."""
    content: dict[str, Any] = {"type": "text", "text": text, "annotations": None}
    if error:
        content["error"] = True
    return json.dumps(content, separators=(",", ":"), ensure_ascii=False)


class TaskFiles:
    """The filesystem tools, confined to the task directory."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def locate(self, recorded: str) -> Path | None:
        path = PurePosixPath(recorded)
        if path.is_absolute():
            if not path.is_relative_to(MOUNT):
                return None
            path = path.relative_to(MOUNT)
        target = (self.root / path).resolve()
        return target if target == self.root or self.root in target.parents else None

    def shown(self, target: Path) -> str:
        relative = target.relative_to(self.root)
        return MOUNT if str(relative) == "." else f"{MOUNT}/{relative}"

    def call(self, operation: str, arguments: dict[str, Any]) -> str:
        if operation == "list_allowed_directories":
            return block(f"Allowed directories:\n{MOUNT}")
        if operation == "read_multiple_files":
            parts = []
            for name in arguments.get("paths") or []:
                target = self.locate(str(name))
                if target is None or not target.is_file():
                    parts.append(f"{name}: Error - ENOENT: no such file or directory")
                else:
                    parts.append(f"{name}:\n{target.read_text(errors='replace')[:READ_LIMIT]}\n")
            return block("\n---\n".join(parts))
        if operation == "move_file":
            source = self.locate(str(arguments.get("source", "")))
            destination = self.locate(str(arguments.get("destination", "")))
            if source is None or destination is None:
                return block("Error: Access denied - path outside allowed directories")
            if not source.exists():
                return block(
                    f"Error: ENOENT: no such file or directory, rename '{self.shown(source)}' "
                    f"-> '{self.shown(destination)}'"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            return block(f"Successfully moved {arguments['source']} to {arguments['destination']}")
        recorded = str(arguments.get("path", "."))
        target = self.locate(recorded)
        if target is None:
            return block(f"Error: Access denied - path outside allowed directories: {recorded}")
        handler = getattr(self, f"_{operation}", None)
        if handler is None:
            return block(f"Error: Unknown tool: {operation}")
        try:
            return block(handler(target, recorded, arguments))
        except FileNotFoundError:
            verb = "scandir" if operation.startswith(("list", "directory", "search")) else "open"
            return block(f"Error: ENOENT: no such file or directory, {verb} '{self.shown(target)}'")
        except (IsADirectoryError, NotADirectoryError, UnicodeDecodeError, OSError) as error:
            return block(f"Error: {error}")

    def _read_file(self, target: Path, recorded: str, arguments: dict[str, Any]) -> str:
        return target.read_text(errors="replace")[:READ_LIMIT]

    _read_text_file = _read_file

    def _write_file(self, target: Path, recorded: str, arguments: dict[str, Any]) -> str:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(arguments.get("content", "")))
        return f"Successfully wrote to {recorded}"

    def _create_directory(self, target: Path, recorded: str, arguments: dict[str, Any]) -> str:
        target.mkdir(parents=True, exist_ok=True)
        return f"Successfully created directory {recorded}"

    def _list_directory(self, target: Path, recorded: str, arguments: dict[str, Any]) -> str:
        entries = sorted(target.iterdir(), key=lambda entry: entry.name)
        return "\n".join(f"[{'DIR' if e.is_dir() else 'FILE'}] {e.name}" for e in entries)

    def _list_directory_with_sizes(
        self, target: Path, recorded: str, arguments: dict[str, Any]
    ) -> str:
        entries = sorted(target.iterdir(), key=lambda entry: entry.name)
        files = [e for e in entries if e.is_file()]
        lines = [
            f"[{'DIR' if e.is_dir() else 'FILE'}] {e.name:<30}"
            + (f" {e.stat().st_size:>8} B" if e.is_file() else "")
            for e in entries
        ]
        total = sum(e.stat().st_size for e in files)
        summary = f"Total: {len(files)} files, {len(entries) - len(files)} directories"
        return "\n".join([*lines, "", summary, f"Combined size: {total} B"])

    def _directory_tree(self, target: Path, recorded: str, arguments: dict[str, Any]) -> str:
        def tree(directory: Path) -> list[dict[str, Any]]:
            return [
                {"name": e.name, "type": "directory", "children": tree(e)}
                if e.is_dir()
                else {"name": e.name, "type": "file"}
                for e in sorted(directory.iterdir(), key=lambda entry: entry.name)
            ]

        return json.dumps(tree(target), indent=2)

    def _search_files(self, target: Path, recorded: str, arguments: dict[str, Any]) -> str:
        pattern = str(arguments.get("pattern", "")).lower()
        wild = pattern if any(mark in pattern for mark in "*?[") else f"*{pattern}*"
        found = sorted(
            self.shown(path)
            for path in target.rglob("*")
            if fnmatch.fnmatch(path.name.lower(), wild)
        )
        return "\n".join(found) if found else "No matches found"

    def _get_file_info(self, target: Path, recorded: str, arguments: dict[str, Any]) -> str:
        status = target.stat()

        def stamp(seconds: float) -> str:
            moment = datetime.fromtimestamp(seconds, UTC)
            return moment.strftime("%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)")

        return "\n".join(
            [
                f"size: {status.st_size}",
                f"created: {stamp(status.st_ctime)}",
                f"modified: {stamp(status.st_mtime)}",
                f"accessed: {stamp(status.st_atime)}",
                f"isDirectory: {str(target.is_dir()).lower()}",
                f"isFile: {str(target.is_file()).lower()}",
                f"permissions: {oct(status.st_mode)[-3:]}",
            ]
        )

    def _edit_file(self, target: Path, recorded: str, arguments: dict[str, Any]) -> str:
        before = target.read_text()
        after = before
        for edit in arguments.get("edits") or []:
            old, new = str(edit.get("oldText", "")), str(edit.get("newText", ""))
            if old not in after:
                return f"Error: Could not find exact match for edit:\n{old}"
            after = after.replace(old, new, 1)
        shown = self.shown(target)
        diff = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                f"{shown}\toriginal",
                f"{shown}\tmodified",
            )
        )
        if not arguments.get("dryRun"):
            target.write_text(after)
        return f"```diff\nIndex: {shown}\n{'=' * 67}\n{diff}```\n\n"


class WorkplaceExecutor:
    """Runs a workplace session's local tools on its task files.

    A planted result answers first. Filesystem tools act on the directory itself; terminal and
    Python tools run in the container, where the directory is mounted at the session's path.
    Every other tool is answered from the recording.
    """

    def __init__(
        self, workspace: Workspace, recorded: RecordedToolExecutor, image: Path | str
    ) -> None:
        self.workspace = workspace
        self.recorded = recorded
        self.image = image
        self.files = TaskFiles(workspace.tree)
        self.live_actions = 0

    @property
    def last_served(self) -> tuple[int, bool] | None:
        return self.recorded.last_served

    def terminal(self, command: str) -> str:
        output, status, _ = run_in_container(self.workspace, self.image, MOUNT, command)
        blocks: list[dict[str, Any]] = [{"type": "text", "text": output, "annotations": None}]
        if status != 0:
            blocks[0]["error"] = True
        tail = f"\nCommand completed with return code: {status}"
        blocks.append({"type": "text", "text": tail, "annotations": None})
        return json.dumps(blocks)

    def python(self, arguments: dict[str, Any]) -> str:
        scratch = self.workspace.tree / ".python_tmp"
        scratch.mkdir(exist_ok=True)
        name = str(arguments.get("filename") or "script.py").rsplit("/", 1)[-1]
        (scratch / name).write_text(str(arguments.get("code", "")))
        limit = int(arguments.get("timeout") or PYTHON_TIMEOUT_SEC)
        started = datetime.now(UTC)
        command = (
            f"python3 .python_tmp/{name} 2>/tmp/.stderr; s=$?; echo; cat /tmp/.stderr >&2; exit $s"
        )
        done = subprocess.run(
            container_command(self.workspace, self.image, MOUNT, command),
            capture_output=True,
            timeout=limit + 30,
            stdin=subprocess.DEVNULL,
        )
        seconds = (datetime.now(UTC) - started).total_seconds()
        stdout = done.stdout.decode(errors="replace").split("\n__steward_cwd__")[0].rstrip("\n")
        stderr = "\n".join(
            line
            for line in done.stderr.decode(errors="replace").splitlines()
            if not line.startswith(("INFO:", "WARNING:"))
        )
        parts = []
        if stdout:
            parts.append(f"=== STDOUT ===\n{stdout}")
        if stderr:
            parts.append(f"=== STDERR ===\n{stderr}")
        parts.append(
            f"=== EXECUTION INFO ===\nReturn code: {done.returncode}\n"
            f"Execution time: {seconds:.3f} seconds\nTimeout limit: {limit} seconds"
        )
        return "\n".join(parts)

    def local(self, action: Action) -> str | None:
        """The result of a local tool, or None when the tool is not one."""
        if action.tool.startswith(FILESYSTEM):
            return self.files.call(action.tool.removeprefix(FILESYSTEM), action.arguments)
        if action.tool == TERMINAL:
            return self.terminal(str(action.arguments.get("command", "")))
        if action.tool == PYTHON:
            return self.python(action.arguments)
        return None

    async def execute(self, action: Action) -> str:
        if self.recorded.plants(action):
            return await self.recorded.execute(action)
        if not action.tool.startswith(FILESYSTEM) and action.tool not in (TERMINAL, PYTHON):
            return await self.recorded.execute(action)
        self.recorded.note(action)
        self.live_actions += 1
        result = await asyncio.to_thread(self.local, action)
        assert result is not None
        return result

    async def run(self, command: str) -> str:
        return await self.recorded.run(command)

    async def succeeds(self, command: str) -> bool:
        return await self.recorded.succeeds(command)


def build_task_workspace(
    trajectory_id: str,
    starting_files: Path | None,
    steps: Sequence[Step],
    takeover_step: int,
    destination: Path,
    image: Path | str,
) -> Workspace:
    """Rebuild a workplace session's task directory at the takeover step: the task's starting
    files, then every local tool call the session made before the takeover, in order. A file
    the session read that is in neither is restored from the recorded read."""
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    report = ReplayReport(
        trajectory_id=trajectory_id,
        repository="",
        commit="",
        takeover_step=takeover_step,
        checked_out=starting_files is not None,
    )
    workspace = Workspace(directory=destination, mount=MOUNT, home=HOME, replay=report)
    for directory in (workspace.home_directory, workspace.temporary, workspace.tree.parent):
        directory.mkdir(parents=True)
    if starting_files is not None and starting_files.is_dir():
        shutil.copytree(starting_files, workspace.tree, symlinks=True)
    else:
        workspace.tree.mkdir()
    executor = WorkplaceExecutor(workspace, RecordedToolExecutor([]), image)
    pending: list[Step] = []
    for step in steps:
        if step.index >= takeover_step:
            break
        if step.kind == "action" and step.tool:
            pending.append(step)
            continue
        if step.kind != "result" or not pending:
            continue
        call = pending.pop(0)
        assert call.tool is not None
        arguments = call.tool_input or {}
        if call.tool in (f"{FILESYSTEM}read_file", f"{FILESYSTEM}read_text_file"):
            restore_read(executor.files, str(arguments.get("path", "")), step.content)
            continue
        if executor.local(Action(tool=call.tool, arguments=arguments)) is not None:
            workspace.commands_replayed += 1
    (destination / "workspace.json").write_text(workspace.model_dump_json(indent=1))
    return workspace


def restore_read(files: TaskFiles, recorded_path: str, recorded_result: str) -> None:
    """Write a file the session read successfully, if the rebuilt directory lacks it."""
    target = files.locate(recorded_path)
    if target is None or target.exists():
        return
    try:
        text = json.loads(recorded_result).get("text")
    except (json.JSONDecodeError, AttributeError):
        return
    if isinstance(text, str) and not text.startswith("Error:"):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
