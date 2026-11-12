"""A live workplace: Toolathlon-GYM's tool servers over a private PostgreSQL database.

Toolathlon-GYM serves mail, calendar, forms, sheets, a store, a learning system and other
workplace tools from one local database, so the whole state of a task is a database directory
and a folder of files. Each world here has its own database, reached through a socket in its
own directory, and its own copy of the task; nothing is shared between runs and nothing leaves
the machine. The state at any point is saved by copying those two directories.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from steward.execution.record import Action

BRIDGE = Path(__file__).parent / "shims" / "gym_bridge"
DATABASE = "toolathlon_gym"
USER, PASSWORD = "eigent", "camel"
SOCKET_DIRECTORY = "/var/run/postgresql"
DATA_DIRECTORY = "/var/lib/postgresql/data"
REPOSITORY_PARTS = ("main.py", "utils", "configs", "scripts")
START_TIMEOUT_SEC = 60


class GymImages(BaseModel):
    """The container images and the GYM checkout a world is built from."""

    tools: Path
    postgres: Path
    repository: Path


class TaskDescription(BaseModel):
    """What the bridge reports once the task's tools are up."""

    workspace: str
    system: str
    task: str
    tools: list[dict[str, Any]]


class World:
    """One task's database, files and tool servers, in one directory."""

    def __init__(self, directory: Path, task: str, images: GymImages) -> None:
        self.directory = directory
        self.task = task
        self.images = images
        self.database: subprocess.Popen[bytes] | None = None
        self.bridge: subprocess.Popen[str] | None = None
        self.description: TaskDescription | None = None

    @property
    def data(self) -> Path:
        return self.directory / "pg" / "data"

    @property
    def sockets(self) -> Path:
        return self.directory / "pg" / "run"

    @property
    def repository(self) -> Path:
        return self.directory / "repo"

    def postgres(self, *command: str, files: dict[Path, str] | None = None) -> list[str]:
        """A command in the database image; `files` are extra files to show it."""
        binds = [f"{self.data}:{DATA_DIRECTORY}", f"{self.sockets}:{SOCKET_DIRECTORY}"]
        binds += [f"{source}:{target}" for source, target in (files or {}).items()]
        arguments = ["apptainer", "exec", "--env", f"PGPASSWORD={PASSWORD}"]
        for bind in binds:
            arguments += ["--bind", bind]
        return [*arguments, str(self.images.postgres), *command]

    def tools(self, *command: str) -> list[str]:
        environment = {
            "PGHOST": SOCKET_DIRECTORY,
            "PG_HOST": SOCKET_DIRECTORY,
            "PGPORT": "5432",
            "PGUSER": USER,
            "PGPASSWORD": PASSWORD,
            "PGDATABASE": DATABASE,
        }
        arguments = ["apptainer", "exec", "--writable-tmpfs", "--pwd", "/workspace"]
        arguments += ["--bind", f"{self.sockets}:{SOCKET_DIRECTORY}"]
        arguments += ["--bind", f"{self.repository}:/workspace"]
        arguments += ["--bind", f"{BRIDGE}:/opt/gym_bridge:ro"]
        for name, value in environment.items():
            arguments += ["--env", f"{name}={value}"]
        return [*arguments, str(self.images.tools), *command]

    def create(self) -> None:
        """A new world: the task's files and a database loaded with GYM's data."""
        if self.directory.exists():
            shutil.rmtree(self.directory)
        for directory in (self.data, self.sockets, self.repository / "tasks" / "finalpool"):
            directory.mkdir(parents=True)
        for part in REPOSITORY_PARTS:
            source = self.images.repository / part
            copy = shutil.copytree if source.is_dir() else shutil.copy2
            copy(source, self.repository / part)
        task = Path("tasks") / "finalpool" / self.task
        shutil.copytree(self.images.repository / task, self.repository / task)
        password = self.directory / "pg" / "password"
        password.write_text(PASSWORD)
        initialise = ["initdb", "-D", DATA_DIRECTORY, "-U", USER, "-A", "md5"]
        subprocess.run(
            self.postgres(*initialise, "--pwfile=/tmp/password", files={password: "/tmp/password"}),
            check=True,
            capture_output=True,
        )
        self.start_database()
        subprocess.run(self.query_command("createdb", DATABASE), check=True, capture_output=True)
        dump = self.images.repository / "db" / "init.sql.gz"
        load = f"zcat /tmp/init.sql.gz | psql -q -h {SOCKET_DIRECTORY} -U {USER} -d {DATABASE}"
        subprocess.run(
            self.postgres("bash", "-c", load, files={dump: "/tmp/init.sql.gz"}),
            check=True,
            capture_output=True,
        )

    def query_command(self, program: str, *arguments: str) -> list[str]:
        return self.postgres(program, "-h", SOCKET_DIRECTORY, "-U", USER, *arguments)

    def start_database(self) -> None:
        # No network address: the only way in is the socket in this world's directory.
        serve = ["postgres", "-D", DATA_DIRECTORY, "-c", "listen_addresses="]
        with (self.directory / "pg" / "log").open("ab") as log:
            self.database = subprocess.Popen(
                self.postgres(*serve), stdout=log, stderr=log, stdin=subprocess.DEVNULL
            )
        deadline = time.monotonic() + START_TIMEOUT_SEC
        while time.monotonic() < deadline:
            ready = subprocess.run(self.query_command("pg_isready"), capture_output=True)
            if ready.returncode == 0:
                return
            time.sleep(0.5)
        raise RuntimeError(f"the database did not start, see {self.directory / 'pg' / 'log'}")

    def stop_database(self) -> None:
        if self.database is None:
            return
        stop = self.postgres("pg_ctl", "-D", DATA_DIRECTORY, "stop", "-m", "fast")
        subprocess.run(stop, capture_output=True)
        self.database.wait(timeout=START_TIMEOUT_SEC)
        self.database = None

    def start_tools(self, restored: bool = False) -> TaskDescription:
        """Start the task's tool servers. Unless the state was restored, the task's own
        preparation runs first, as it does under GYM's agent."""
        command = ["python", "/opt/gym_bridge", "--task_dir", self.task]
        if restored:
            command.append("--restored")
        log = (self.directory / "bridge.log").open("a")
        self.bridge = subprocess.Popen(
            self.tools(*command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
        )
        self.description = TaskDescription.model_validate(self.read())
        return self.description

    def read(self) -> dict[str, Any]:
        assert self.bridge is not None and self.bridge.stdout is not None
        line = self.bridge.stdout.readline()
        if not line:
            raise RuntimeError(f"the tool servers stopped, see {self.directory / 'bridge.log'}")
        answer: dict[str, Any] = json.loads(line)
        return answer

    def request(self, message: dict[str, Any]) -> dict[str, Any]:
        assert self.bridge is not None and self.bridge.stdin is not None
        self.bridge.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.bridge.stdin.flush()
        return self.read()

    def call(self, tool: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        answer = self.request({"call": tool, "arguments": arguments})
        return str(answer.get("output", "")), bool(answer.get("ok"))

    def evaluate(self, status: str = "success") -> dict[str, Any]:
        """The verdict of the task's own evaluation script on the state as it is now."""
        evaluation: dict[str, Any] = self.request({"evaluate": status})["evaluation"]
        return evaluation

    def stop_tools(self) -> None:
        if self.bridge is None:
            return
        try:
            assert self.bridge.stdin is not None
            self.bridge.stdin.write(json.dumps({"close": True}) + "\n")
            self.bridge.stdin.flush()
            self.bridge.wait(timeout=30)
        except (BrokenPipeError, subprocess.TimeoutExpired):
            self.bridge.kill()
        self.bridge = None

    def stop(self) -> None:
        self.stop_tools()
        self.stop_database()

    def save(self, archive: Path) -> None:
        """Save the whole state. The tool servers and the database are stopped first, so the
        copy is consistent."""
        self.stop()
        archive.parent.mkdir(parents=True, exist_ok=True)
        parts = ["pg/data", "repo"]
        subprocess.run(["tar", "-cf", str(archive), "-C", str(self.directory), *parts], check=True)

    def restore(self, archive: Path) -> None:
        """Put a saved state in place and start its database."""
        if self.directory.exists():
            shutil.rmtree(self.directory)
        self.sockets.mkdir(parents=True)
        subprocess.run(["tar", "-xf", str(archive), "-C", str(self.directory)], check=True)
        self.start_database()

    def succeeds(self, command: str) -> bool:
        """Whether a check on the state passes: a shell command run next to the tools, with
        the database reachable through the usual variables."""
        done = subprocess.run(self.tools("bash", "-c", command), capture_output=True)
        return done.returncode == 0


class GymExecutor:
    """Answers the agent's tool calls from the world's real tool servers."""

    def __init__(self, world: World) -> None:
        self.world = world
        self.live_actions = 0

    async def execute(self, action: Action) -> str:
        output, _ = await asyncio.to_thread(self.world.call, action.tool, action.arguments)
        self.live_actions += 1
        return output

    async def run(self, command: str) -> str:
        done = await asyncio.to_thread(
            subprocess.run, self.world.tools("bash", "-c", command), capture_output=True, text=True
        )
        return done.stdout + done.stderr

    async def succeeds(self, command: str) -> bool:
        return await asyncio.to_thread(self.world.succeeds, command)
