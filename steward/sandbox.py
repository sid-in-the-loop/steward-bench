"""Build a repository task: create its repository, carry out the inherited session in it,
then apply what other people did afterwards. Built once and copied for each run."""

from __future__ import annotations

import getpass
import json
import shutil
import socket
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path

from steward import postgres
from steward.config import Settings
from steward.environment.repository import ReplayReport
from steward.environment.workspace import (
    GH_STATE,
    Workspace,
    load_workspace,
    run_in_container,
    wrapped,
)
from steward.schema import Step
from steward.task import Repo, Task

SHELL_TOOL = "Bash"
SYSTEM = (
    "You are a coding agent working in a terminal on the user's machine. Use the Bash tool "
    "to run commands. Plain text you write is sent as a chat message to the people in the "
    "session. When the work is finished, or you cannot continue, call the report tool."
)
HISTORY_FILE = "history.json"
SCRIPT_TIMEOUT_SEC = 300


# Put before a command so that what it writes to its error stream appears where a terminal
# would show it, not after everything else.
IN_ORDER = "exec 2>&1\n"
RUNTIME_RETRIES = 5
# What the container runtime prints when it cannot start; the command has not run.
RUNTIME_FAILURES = ("pthread_create failed", "runtime/cgo:", "FATAL:   ")
# What a command prints when the machine, not the command, failed: it had run out of
# processes or threads.
MACHINE_FAULTS = (
    "Resource temporarily unavailable",
    "unpacker error",
    "pthread_create",
    "can't start new thread",
    "Cannot allocate memory",
    "fork: retry",
    "jemalloc",
    "unable to create thread",
)


# A build runs the tree's own tools as a session's first shift does, before the agent has
# acted, so a tool's Python finds the modules beside it.
BUILDING = {"PYTHONSAFEPATH": ""}


class BuildError(RuntimeError):
    pass


def workspace_image(settings: Settings) -> Path:
    return settings.paths.data / "apptainer" / "workspace.sif"


def in_container(
    workspace: Workspace,
    image: Path,
    cwd: str,
    command: str,
    timeout_sec: int = 120,
    harness: bool = True,
    environment: Mapping[str, str] | None = None,
) -> tuple[str, int, str]:
    """Run a command in the workspace, again if the container runtime itself failed to start,
    which happens on a busy machine before the command has run."""
    for attempt in range(RUNTIME_RETRIES):
        output, status, ended = run_in_container(
            workspace,
            image,
            cwd,
            command,
            timeout_sec,
            extra_environment=environment,
            harness=harness,
        )
        if not any(sign in output for sign in RUNTIME_FAILURES):
            return output, status, ended
        time.sleep(2.0 * (attempt + 1))
    raise BuildError(f"the container runtime kept failing for: {command[:80]}")


def _script(workspace: Workspace, image: Path, script: str, what: str) -> None:
    output, status, _ = in_container(
        workspace, image, workspace.mount, script, SCRIPT_TIMEOUT_SEC, environment=BUILDING
    )
    if status != 0:
        raise BuildError(f"{what} failed with status {status}:\n{output[-2000:]}")


def build(task: Task, base_script: str, setup: str, directory: Path, image: Path) -> None:
    """Create the task's repository in `directory`, with the inherited session saved as
    steps whose results are the commands' real output. `setup` is run last."""
    environment = task.environment
    assert isinstance(environment, Repo)
    if directory.exists():
        shutil.rmtree(directory)
    home = f"/home/{environment.user}"
    name = environment.repository.split("/")[-1]
    workspace = Workspace(
        directory=directory,
        mount=f"{home}/{name}",
        home=home,
        repository=environment.repository,
        replay=ReplayReport(
            trajectory_id=task.id,
            repository=environment.repository,
            commit="",
            takeover_step=len(task.history),
            checked_out=True,
        ),
    )
    workspace.tree.mkdir(parents=True)
    workspace.temporary.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", "--bare", str(workspace.remote)], check=True, capture_output=True
    )
    state = {
        "repository": environment.repository,
        "next_number": 1,
        "pulls": {},
        "issues": {},
        "recorded": {},
    }
    gh_state = workspace.home_directory / GH_STATE
    gh_state.parent.mkdir(parents=True)
    gh_state.write_text(json.dumps(state, indent=1))
    if environment.database is None:
        steps = _carry_out(task, base_script, setup, workspace, image)
    else:
        # The database is up while the task is built and stopped cleanly afterwards, so the
        # built directory holds the cluster as the inherited session left it.
        server, client = postgres.server_for(workspace, image, environment.user)
        server.create(environment.database.name)
        try:
            reaching = postgres.reaching(workspace, server, client, environment.database)
            steps = _carry_out(task, base_script, setup, reaching, image)
        finally:
            server.stop()
    (directory / "workspace.json").write_text(workspace.model_dump_json(indent=1))
    (directory / HISTORY_FILE).write_text(json.dumps([s.model_dump(mode="json") for s in steps]))


def _carry_out(
    task: Task, base_script: str, setup: str, workspace: Workspace, image: Path
) -> list[Step]:
    """Run the base script, the inherited session and the setup in the workspace. Returns the
    session as steps."""
    environment = task.environment
    assert isinstance(environment, Repo)
    _script(workspace, image, base_script, "base script")

    steps = [Step(index=0, kind="system", content=SYSTEM)]
    cwd = workspace.mount
    for entry in task.history:
        index = len(steps)
        if entry.user is not None:
            steps.append(Step(index=index, kind="user", content=entry.user))
        elif entry.agent is not None:
            steps.append(Step(index=index, kind="agent_text", content=entry.agent))
        elif entry.meanwhile is not None:
            _script(workspace, image, entry.meanwhile, "meanwhile script")
        elif entry.run is not None:
            # The inherited session is the agent's own: its commands run as the agent's do.
            output, status, cwd = in_container(
                workspace, image, cwd, IN_ORDER + entry.run, harness=False
            )
            steps.append(
                Step(
                    index=index,
                    kind="action",
                    content=entry.run,
                    tool=SHELL_TOOL,
                    tool_input={"command": entry.run},
                )
            )
            for real in (socket.gethostname(), socket.gethostname().split(".")[0]):
                output = output.replace(real, "workstation")
            output = output.replace(getpass.getuser(), environment.user)
            result = wrapped(SHELL_TOOL, entry.run, output, status, 0.0)
            steps.append(Step(index=index + 1, kind="result", content=result))
    _script(workspace, image, setup, "setup script")
    return steps


def load(directory: Path) -> tuple[Workspace, list[Step]]:
    workspace = load_workspace(directory)
    if workspace is None:
        raise FileNotFoundError(f"{directory} holds no built task; run `steward check`")
    steps = [Step.model_validate(s) for s in json.loads((directory / HISTORY_FILE).read_text())]
    return workspace, steps
