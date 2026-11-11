"""A live working tree for a recorded session, and an executor that runs commands in it.

The tree is the session's repository at its commit with the recorded file edits and the
recorded git and file commands replayed up to the takeover step. It is mounted in a container
at the path the session used on the user's machine, so the paths in the inherited history are
valid. The container has no network; the repository's remote is a bare copy next to the tree,
so fetches and pushes work and stay local.
"""

from __future__ import annotations

import asyncio
import base64
import difflib
import hashlib
import json
import logging
import re
import shlex
import shutil
import subprocess
import time
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel

from steward.environment.recorded import RecordedToolExecutor
from steward.environment.repository import (
    EDIT_TOOLS,
    WRITE_TOOLS,
    PathResolver,
    ReplayReport,
    apply_step,
)
from steward.execution.record import Action
from steward.schema import Step

log = logging.getLogger(__name__)

IMAGE = "docker://nikolaik/python-nodejs:python3.12-nodejs22-bookworm"
SHELL_TOOLS = frozenset(
    {
        "Bash",
        "bash",
        "shell",
        "shell_command",
        "exec_command",
        "exec",
        "run_in_terminal",
        "execute_bash",
        "bash_command",
    }
)
READ_TOOLS = frozenset({"Read", "read_file"})
SEARCH_TOOLS = frozenset({"Grep", "Glob", "list_dir", "LS"})
COMMAND_KEYS = ("command", "cmd", "script", "input")
# Programs that reach outside the machine. They are answered from the recording, because the
# container has no network and no accounts.
OUTSIDE = re.compile(
    r"(?:^|[;&|(]\s*|\bsudo\s+)(?:gh|curl|wget|ssh|scp|docker|kubectl|gcloud|aws|az|npm\s+publish|"
    r"vercel|fly|heroku|terraform)\b"
)
# Recorded commands replayed to rebuild state: git, and plain file operations.
REPLAYED = re.compile(r"^\s*(?:cd\s+\S+\s*&&\s*)?(?:git|mkdir|mv|cp|rm|touch)\s")
ABSOLUTE_PATH = re.compile(r"(?<![\w.])/(?:[\w.@+-]+/)+[\w.@+-]+")
OUTPUT_LIMIT = 20_000
COMMAND_TIMEOUT_SEC = 120
REMOTE_MOUNT = "/srv/git/origin.git"
GH_SHIM = Path(__file__).parent / "shims" / "gh"
GH_STATE = Path(".config") / "gh" / "state.json"
ITEM_NUMBER = re.compile(r"/(?:pull|issues)/(\d+)")
CWD_MARK = "__steward_cwd__"
MATCH_THRESHOLD = 0.9
COMPARE_LIMIT = 2000
# Time spent re-running commands that only read; state-changing ones always run.
REPLAY_BUDGET_SEC = 900
EDIT_SCRIPT = """
import base64, json, pathlib, sys
a = json.loads(base64.b64decode(sys.argv[1]))
p = pathlib.Path(a.get("file_path") or a.get("path"))
old, new = a.get("old_string", a.get("old_str")), a.get("new_string", a.get("new_str"))
text = p.read_text()
if old not in text:
    sys.exit("String to replace not found in file.")
p.write_text(text.replace(old, new, -1 if a.get("replace_all") else 1))
print(f"The file {p} has been updated.")
"""


class Workspace(BaseModel):
    """A rebuilt tree on disk and where it is mounted in the container."""

    directory: Path
    mount: str
    home: str
    replay: ReplayReport
    repository: str = ""
    shell: Literal["bash", "zsh"] = "bash"
    # Where the starting commit came from: the session's own output, or the dataset's metadata.
    commit_source: Literal["recorded", "metadata"] = "metadata"
    commands_replayed: int = 0
    commands_failed: int = 0
    # How well re-running the session's own commands reproduced what the session saw.
    commands_compared: int = 0
    commands_matched: int = 0
    similarity_total: float = 0.0
    commands_outside: int = 0

    @property
    def fidelity(self) -> float | None:
        """Share of re-run commands whose output matched the recording; None without any."""
        return self.commands_matched / self.commands_compared if self.commands_compared else None

    @property
    def under_home(self) -> bool:
        return PurePosixPath(self.mount).is_relative_to(self.home) and self.mount != self.home

    @property
    def tree(self) -> Path:
        """The working directory on disk. When the session worked under the user's home
        directory the whole home is one tree on disk, so directories next to the repository
        (a second worktree, a notes folder) exist too."""
        if self.under_home:
            return self.home_directory / PurePosixPath(self.mount).relative_to(self.home)
        return self.directory / "root" / PurePosixPath(self.mount).name

    @property
    def remote(self) -> Path:
        return self.directory / "origin.git"

    @property
    def home_directory(self) -> Path:
        return self.directory / "home"

    @property
    def temporary(self) -> Path:
        return self.directory / "tmp"


def command_of(arguments: dict[str, Any] | None) -> str | None:
    for key in COMMAND_KEYS:
        value = (arguments or {}).get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, list) and value and all(isinstance(part, str) for part in value):
            # ["bash", "-lc", "<command>"]
            return value[-1] if len(value) > 1 and value[0] in ("bash", "sh", "zsh") else None
    return None


def infer_mount(steps: Sequence[Step], tree: Path, repository: str) -> str:
    """The directory the session worked in on the user's machine, learned from the first
    recorded absolute path whose tail exists in the tree."""
    resolver = PathResolver(tree)
    for step in steps:
        if step.kind != "action":
            continue
        text = json.dumps(step.tool_input or {}) + " " + step.content
        for match in ABSOLUTE_PATH.finditer(text):
            resolver.resolve(match.group(0))
            if resolver.prefix is not None and len(resolver.prefix.parts) > 2:
                return str(resolver.prefix)
            resolver.prefix = None
    return f"/home/user/{repository.rsplit('/', 1)[-1]}"


ON_BRANCH = re.compile(r"^On branch (\S+)", re.MULTILINE)


def starting_branch(steps: Sequence[Step], takeover_step: int, tree: Path) -> str:
    """The branch the session was on: the first one a recorded `git status` names, else the
    remote's default branch."""
    for step in steps:
        if step.index < takeover_step and step.kind == "result":
            found = ON_BRANCH.search(step.content)
            if found:
                return found.group(1)
    head = subprocess.run(
        ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        cwd=tree,
        capture_output=True,
        text=True,
    )
    return head.stdout.strip().removeprefix("origin/") or "main"


HEAD_QUERY = re.compile(
    r"git(?: --no-pager)? (?:log(?: (?:-\S+|\d+))*|rev-parse(?: --short(?:=\d+)?)? HEAD)\s*(?:$|[;&|])"
)
MOVES_HEAD = re.compile(
    r"git(?: -\S+)* (?:commit|checkout|switch|reset|merge|pull|rebase|cherry-pick|revert|am|stash)\b"
)
HASH_LINE = re.compile(r"^(?:commit )?([0-9a-f]{7,40})\b", re.MULTILINE)


def evident_commit(steps: Sequence[Step], takeover_step: int, tree: Path) -> tuple[str, int] | None:
    """The commit the session itself reported as HEAD before it moved HEAD, as a full hash,
    with the length the session's git abbreviated it to. None when the session never showed
    it or the commit is not in the repository."""
    pending: list[Step] = []
    for step in steps:
        if step.index >= takeover_step:
            break
        if step.kind == "action":
            pending.append(step)
            continue
        if step.kind != "result" or not pending:
            continue
        call = pending.pop(0)
        command = command_of(call.tool_input) if call.tool in SHELL_TOOLS else None
        if not command:
            continue
        if MOVES_HEAD.search(command):
            return None
        found = HASH_LINE.search(step.content) if HEAD_QUERY.search(command) else None
        if found is None:
            continue
        resolved = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{found.group(1)}^{{commit}}"],
            cwd=tree,
            capture_output=True,
            text=True,
        )
        if resolved.returncode != 0:
            return None
        return resolved.stdout.strip(), len(found.group(1))
    return None


def home_of(mount: str) -> str:
    parts = PurePosixPath(mount).parts
    return str(PurePosixPath(*parts[:3])) if parts[1:2] in (("Users",), ("home",)) else "/root"


def container_command(workspace: Workspace, image: Path | str, cwd: str, command: str) -> list[str]:
    binds = [f"{workspace.temporary}:/tmp", f"{GH_SHIM}:/usr/local/bin/gh:ro"]
    if workspace.remote.exists():
        binds.append(f"{workspace.remote}:{REMOTE_MOUNT}")
    if not workspace.under_home:
        parent = str(PurePosixPath(workspace.mount).parent)
        if parent == "/":
            binds.append(f"{workspace.tree}:{workspace.mount}")
        else:
            binds.append(f"{workspace.tree.parent}:{parent}")
    wrapped = f"cd {shlex.quote(cwd)} 2>/dev/null || cd {shlex.quote(workspace.mount)}\n{command}\n"
    wrapped += f'__status=$?; printf "\\n{CWD_MARK}%s\\n" "$PWD"; exit $__status'
    arguments = ["apptainer", "exec", "--containall", "--net", "--network", "none"]
    arguments += ["--home", f"{workspace.home_directory}:{workspace.home}"]
    for bind in binds:
        arguments += ["--bind", bind]
    arguments += ["--env", "GIT_TERMINAL_PROMPT=0", "--env", "TERM=dumb"]
    return [*arguments, str(image), workspace.shell, "-c", wrapped]


def run_in_container(
    workspace: Workspace,
    image: Path | str,
    cwd: str,
    command: str,
    timeout_sec: int = COMMAND_TIMEOUT_SEC,
) -> tuple[str, int, str]:
    """Run a command in the workspace. Returns its output, exit status and the directory it
    ended in."""
    try:
        done = subprocess.run(
            container_command(workspace, image, cwd, command),
            capture_output=True,
            timeout=timeout_sec,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout_sec} seconds.", 124, cwd
    output = done.stdout.decode(errors="replace")
    errors = "\n".join(
        line
        for line in done.stderr.decode(errors="replace").splitlines()
        if not line.startswith(("INFO:", "WARNING:"))
    )
    ended = cwd
    if CWD_MARK in output:
        output, _, tail = output.rpartition(f"\n{CWD_MARK}")
        ended = tail.strip() or cwd
    shown = output + errors
    if workspace.repository:
        url = f"https://github.com/{workspace.repository}"
        shown = shown.replace(REMOTE_MOUNT, url).replace(REMOTE_MOUNT.removesuffix(".git"), url)
    return shown[:OUTPUT_LIMIT], done.returncode, ended


def seed_github(
    workspace: Workspace, repository: str, steps: Sequence[Step], takeover_step: int
) -> None:
    """Start the GitHub stand-in from what the session saw before the takeover step: its
    recorded `gh` calls with their outputs, and numbering that continues after the highest
    pull request or issue number that appeared."""
    recorded: dict[str, list[str]] = {}
    highest = 0
    pending: list[Step] = []
    for step in steps:
        if step.index >= takeover_step:
            break
        if step.kind == "action":
            pending.append(step)
        elif step.kind == "result" and pending:
            call = pending.pop(0)
            highest = max([highest, *(int(n) for n in ITEM_NUMBER.findall(step.content))])
            command = command_of(call.tool_input) if call.tool in SHELL_TOOLS else None
            if not command or re.search(r"[|;&<>$`\n]", command):
                continue
            try:
                argv = shlex.split(command)
            except ValueError:
                continue
            if argv[:1] == ["gh"]:
                recorded.setdefault(json.dumps(argv[1:]), []).append(step.content)
    state = {
        "repository": repository,
        "next_number": highest + 1,
        "pulls": {},
        "issues": {},
        "recorded": recorded,
    }
    path = workspace.home_directory / GH_STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1))


# What some harnesses put around a command's output: exit status, timing, a chunk identifier.
WRAPPER = re.compile(
    r"\A(?:Command: [^\n]*\n)?(?:Chunk ID: \w+\n)?(?:Exit code:? \d+\n)?"
    r"(?:Wall time: [\d.]+ seconds\n)?(?:Process exited with code \d+\n)?"
    r"(?:Original token count: \d+\n)?(?:Total output lines: \d+\n)?(?:Output:\n)?"
)


def wrapped(tool: str, command: str, output: str, status: int, seconds: float) -> str:
    """A command's output in the form the session's harness gave its results."""
    if tool == "shell_command":
        return f"Exit code: {status}\nWall time: {seconds:.1f} seconds\nOutput:\n{output}"
    if tool == "exec_command":
        chunk = hashlib.sha1(f"{command}{seconds}".encode()).hexdigest()[:6]
        return (
            f"Chunk ID: {chunk}\nWall time: {seconds:.4f} seconds\n"
            f"Process exited with code {status}\nOriginal token count: {len(output) // 4}\n"
            f"Output:\n{output}"
        )
    if status != 0:
        return f"Exit code {status}\n{output}" if output.strip() else f"Exit code {status}"
    return output if output.strip() else "(no output)"


def settle_directory(tool: str, mount: str, ended: str) -> tuple[str, str]:
    """Where the next command starts, and what the harness says about it. Claude Code's Bash
    tool returns to the project directory after a command that ends outside it."""
    if tool == "Bash" and not PurePosixPath(ended).is_relative_to(mount):
        return mount, f"\nShell cwd was reset to {mount}"
    return ended, ""


def output_lines(text: str) -> Counter[str]:
    return Counter(
        " ".join(line.split()).removeprefix("./") for line in text.splitlines() if line.strip()
    )


def similarity(replayed: str, recorded: str) -> float:
    """How alike two command outputs are, from 0 to 1. Whitespace layout, what the harness
    put around the output, and the order of lines are ignored: a search that lists the same
    files in another order shows the same state."""
    body = WRAPPER.sub("", recorded, count=1)
    left = " ".join(replayed.split())[:COMPARE_LIMIT]
    right = " ".join(body.split())[:COMPARE_LIMIT]
    if left == right:
        return 1.0
    ordered = difflib.SequenceMatcher(None, left, right, autojunk=False).ratio()
    mine, theirs = output_lines(replayed), output_lines(body)
    union = sum((mine | theirs).values())
    shared = sum((mine & theirs).values()) / union if union else 1.0
    return max(ordered, shared)


def write_remote(tree: Path, remote: Path) -> None:
    """A bare repository holding what the real remote held: every branch the clone fetched.
    Its objects are hard links to the tree's, so nothing is transferred and neither depends
    on the other afterwards."""
    subprocess.run(
        ["git", "init", "--quiet", "--bare", str(remote)], check=True, capture_output=True
    )
    shutil.rmtree(remote / "objects")
    subprocess.run(
        ["cp", "-al", str(tree / ".git" / "objects"), str(remote / "objects")], check=True
    )
    listed = subprocess.run(
        ["git", "for-each-ref", "--format=%(objectname) %(refname)", "refs/remotes/origin"],
        cwd=tree,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    lines = ["# pack-refs with: peeled fully-peeled sorted "]
    for line in sorted(listed, key=lambda entry: entry.split(" ", 1)[1]):
        name = line.split(" ", 1)[1].removeprefix("refs/remotes/origin/")
        if name != "HEAD":
            lines.append(f"{line.split(' ', 1)[0]} refs/heads/{name}")
    (remote / "packed-refs").write_text("\n".join(lines) + "\n")
    head = subprocess.run(
        ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        cwd=tree,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if head:
        (remote / "HEAD").write_text(f"ref: refs/heads/{head.removeprefix('origin/')}\n")


def git(tree: Path, *arguments: str, check: bool = True) -> str:
    done = subprocess.run(
        ["git", *arguments], cwd=tree, check=check, capture_output=True, text=True, timeout=900
    )
    return done.stdout.strip()


def session_start(steps: Sequence[Step]) -> datetime | None:
    return next((step.timestamp for step in steps if step.timestamp is not None), None)


def forget_later_state(tree: Path, branch: str, started: datetime | None) -> None:
    """Leave the repository holding only what existed when the session began: the starting
    branch, no other local branches, worktrees or stashes, and each remote branch at its last
    commit from before the session. A remote branch with no such commit did not exist yet."""
    git(tree, "worktree", "prune")
    git(tree, "stash", "clear")
    for name in git(tree, "for-each-ref", "--format=%(refname:short)", "refs/heads").split():
        if name != branch:
            git(tree, "branch", "--quiet", "-D", name, check=False)
    if started is None:
        return
    before = started.isoformat()
    refs = git(tree, "for-each-ref", "--format=%(refname)", "refs/remotes/origin").split()
    for ref in refs:
        if ref.endswith("/HEAD"):
            continue
        tip = git(tree, "rev-list", "-1", f"--before={before}", ref, check=False)
        if tip:
            git(tree, "update-ref", ref, tip)
        else:
            git(tree, "update-ref", "-d", ref)


def missing_objects(checkout: Path) -> bool:
    try:
        listed = subprocess.run(
            ["git", "rev-list", "--objects", "--all", "--missing=print"],
            cwd=checkout,
            capture_output=True,
            text=True,
            timeout=900,
        )
    except subprocess.TimeoutExpired:
        log.warning("%s: could not list objects in time; treated as complete", checkout)
        return False
    return any(line.startswith("?") for line in listed.stdout.splitlines())


def complete_clone(checkout: Path) -> None:
    """Fetch the file contents a blobless clone left out, so the tree works offline. This is
    the one step of a build that needs the network."""
    if not missing_objects(checkout):
        return
    for key in ("remote.origin.partialclonefilter", "remote.origin.promisor"):
        subprocess.run(["git", "config", "--unset", key], cwd=checkout, capture_output=True)
    # Background maintenance after the fetch would move pack files while the tree is in use.
    quiet = ("-c", "gc.auto=0", "-c", "maintenance.auto=false")
    git(checkout, *quiet, "fetch", "--quiet", "--refetch", "origin")
    for promisor in (checkout / ".git" / "objects" / "pack").glob("*.promisor"):
        promisor.unlink()


def build_workspace(
    trajectory_id: str,
    repository: str,
    commit: str,
    steps: Sequence[Step],
    takeover_step: int,
    checkout: Path,
    destination: Path,
    image: Path | str = IMAGE,
) -> Workspace:
    """Rebuild the session's tree at the takeover step under `destination`."""
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    report = ReplayReport(
        trajectory_id=trajectory_id,
        repository=repository,
        commit=commit,
        takeover_step=takeover_step,
        checked_out=True,
    )
    staging = destination / "staging"
    subprocess.run(["cp", "-a", str(checkout), str(staging)], check=True)
    complete_clone(staging)
    evident = evident_commit(steps, takeover_step, staging)
    start = evident[0] if evident else commit
    report.commit = start
    branch = starting_branch(steps, takeover_step, staging)
    git(staging, "checkout", "--quiet", "--force", "-B", branch, start)
    git(staging, "clean", "-fdxq")
    forget_later_state(staging, branch, session_start(steps))
    mount = infer_mount(steps, staging, repository)
    workspace = Workspace(
        directory=destination,
        mount=mount,
        home=home_of(mount),
        replay=report,
        repository=repository,
        shell="zsh" if mount.startswith("/Users/") else "bash",
        commit_source="recorded" if evident else "metadata",
    )
    for directory in (workspace.home_directory, workspace.temporary, workspace.tree.parent):
        directory.mkdir(parents=True, exist_ok=True)
    staging.rename(workspace.tree)
    tree = workspace.tree
    write_remote(tree, workspace.remote)
    git(tree, "branch", "--quiet", "--set-upstream-to", f"origin/{branch}", branch, check=False)
    abbreviation = f"[core]\n\tabbrev = {evident[1]}\n" if evident and evident[1] < 40 else ""
    (workspace.home_directory / ".gitconfig").write_text(
        "[user]\n\tname = Developer\n\temail = developer@example.com\n"
        f'[url "{REMOTE_MOUNT}"]\n'
        f"\tinsteadOf = https://github.com/{repository}.git\n"
        f"\tinsteadOf = https://github.com/{repository}\n"
        f"\tinsteadOf = git@github.com:{repository}.git\n"
        f"[safe]\n\tdirectory = *\n[advice]\n\tdetachedHead = false\n{abbreviation}"
    )
    git(tree, "remote", "set-url", "origin", f"https://github.com/{repository}")
    resolver = PathResolver(tree)
    resolver.prefix = PurePosixPath(mount)
    cwd = mount
    started = time.monotonic()
    # Outputs of re-run commands, waiting for the recorded result of the same call.
    awaiting: list[str | None] = []
    for step in steps:
        if step.index >= takeover_step:
            break
        if step.kind == "result" and awaiting:
            output = awaiting.pop(0)
            if output is not None:
                score = similarity(output, step.content)
                workspace.commands_compared += 1
                workspace.commands_matched += score >= MATCH_THRESHOLD
                workspace.similarity_total += score
            continue
        if step.kind != "action":
            continue
        awaiting.append(None)
        if step.tool in EDIT_TOOLS | WRITE_TOOLS:
            apply_step(step, resolver, report)
            continue
        command = command_of(step.tool_input) if step.tool in SHELL_TOOLS else None
        if not command:
            continue
        if OUTSIDE.search(command):
            workspace.commands_outside += 1
            continue
        changes_state = bool(REPLAYED.match(command))
        if not changes_state and time.monotonic() - started > REPLAY_BUDGET_SEC:
            continue
        limit = 60 if changes_state else 30
        output, status, ended = run_in_container(workspace, image, cwd, command, timeout_sec=limit)
        cwd, note = settle_directory(step.tool or "", mount, ended)
        workspace.commands_replayed += 1
        workspace.commands_failed += status != 0
        awaiting[-1] = output + note
    seed_github(workspace, repository, steps, takeover_step)
    (destination / "workspace.json").write_text(workspace.model_dump_json(indent=1))
    return workspace


def copy_workspace(source: Workspace, destination: Path) -> Workspace:
    """A private copy of a built workspace for one run."""
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cp", "-a", str(source.directory), str(destination)], check=True)
    return source.model_copy(update={"directory": destination})


class LiveExecutor:
    """Runs the agent's shell and file tools in the workspace.

    A planted result still answers first, since it states what the scenario changed. A command
    that reaches outside the machine is answered from the recording when the session ran exactly
    that command; otherwise it runs, with a local stand-in for the GitHub CLI and no network.
    A tool with no local meaning is answered from the recording as before.
    """

    def __init__(
        self, workspace: Workspace, recorded: RecordedToolExecutor, image: Path | str = IMAGE
    ) -> None:
        self.workspace = workspace
        self.recorded = recorded
        self.image = image
        self.cwd = workspace.mount
        self.live_actions = 0

    @property
    def last_served(self) -> tuple[int, bool] | None:
        return self.recorded.last_served

    async def shell(self, command: str, tool: str = "Bash") -> str:
        started = time.monotonic()
        output, status, ended = await asyncio.to_thread(
            run_in_container, self.workspace, self.image, self.cwd, command
        )
        self.cwd, note = settle_directory(tool, self.workspace.mount, ended)
        self.live_actions += 1
        return wrapped(tool, command, output + note, status, time.monotonic() - started)

    async def execute(self, action: Action) -> str:
        arguments = action.arguments
        if self.recorded.plants(action):
            return await self.recorded.execute(action)
        self.recorded.note(action)
        if action.tool in SHELL_TOOLS:
            command = command_of(arguments)
            if command is None or (OUTSIDE.search(command) and self.recorded.answers(action)):
                return await self.recorded.execute(action)
            return await self.shell(command, action.tool)
        path = shlex.quote(str(arguments.get("file_path") or arguments.get("path") or "."))
        if action.tool in READ_TOOLS:
            return await self.shell(f"cat -n -- {path} | head -2000")
        if action.tool in WRITE_TOOLS:
            content = str(arguments.get("content", arguments.get("file_text", "")))
            encoded = base64.b64encode(content.encode()).decode()
            wrote = f'mkdir -p -- "$(dirname -- {path})" && echo {encoded} | base64 -d > {path}'
            return await self.shell(f"{wrote} && echo File written: {path}")
        if action.tool in EDIT_TOOLS:
            encoded = base64.b64encode(json.dumps(arguments).encode()).decode()
            return await self.shell(f"python3 -c {shlex.quote(EDIT_SCRIPT)} {encoded}")
        if action.tool == "Grep":
            pattern = shlex.quote(str(arguments.get("pattern", "")))
            return await self.shell(f"grep -rnE -- {pattern} {path} | head -200")
        if action.tool == "Glob":
            pattern = shlex.quote(str(arguments.get("pattern", "*")))
            found = f"find {path} -path {pattern} -o -name {pattern}"
            return await self.shell(f"{found} | grep -v '/.git/' | head -200")
        if action.tool in SEARCH_TOOLS:
            return await self.shell(f"ls -la -- {path}")
        return await self.recorded.execute(action)

    async def run(self, command: str) -> str:
        return await self.recorded.run(command)

    async def succeeds(self, command: str) -> bool:
        return await self.recorded.succeeds(command)


def workspace_directory(data: Path, trajectory_id: str, takeover_step: int) -> Path:
    key = hashlib.sha1(trajectory_id.encode()).hexdigest()[:12]
    return data / "phase2" / "workspaces" / f"{key}-t{takeover_step}"


def load_workspace(directory: Path) -> Workspace | None:
    saved = directory / "workspace.json"
    if not saved.exists():
        return None
    return Workspace.model_validate_json(saved.read_text()).model_copy(
        update={"directory": directory}
    )
