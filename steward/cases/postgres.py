"""A PostgreSQL server that stays up for a whole run of a repository case.

Each shell command of a case runs in its own container, so nothing a command starts outlives
it. A case that needs a database gets one server, started outside those containers when the
run is prepared and stopped when the run closes. Its data directory and its socket lie in the
run's own directory, so runs in parallel share nothing, and it listens on no network address.
Every command reaches it through the socket, with the client programs of the server's own
image shown to the command's container.
"""

from __future__ import annotations

import atexit
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from steward.cases.case import Database
from steward.environment.workspace import Workspace

SUPERUSER = "postgres"
SOCKET_DIRECTORY = "/var/run/postgresql"
DATA_DIRECTORY = "/var/lib/postgresql/data"
CLIENT_MOUNT = "/opt/postgres-client"
CLIENT_PROGRAMS = ("psql", "pg_dump", "pg_restore", "pg_isready")
PROGRAM_DIRECTORY = "/usr/local/bin"
START_TIMEOUT_SEC = 60
STOP_TIMEOUT_SEC = 60
# Who may connect through the socket: the superuser without a password, as the operating
# system's database account does on a database host, and every other role with its password.
HOST_ACCESS = "local all postgres trust\nlocal all all scram-sha-256\n"
# Copies the client programs and every library they load out of the server image, with a
# starter for each that uses the copied loader, so they run in an image with an older C
# library. Run in the server image with the destination at /bundle.
COLLECT_CLIENT = """set -eu
programs=$(dirname "$(readlink -f "$(command -v postgres)")")
mkdir -p /bundle/bin /bundle/lib /bundle/libexec
loader=$(readlink -f /lib64/ld-linux-x86-64.so.2)
cp "$loader" /bundle/lib/loader
for program in {programs}; do
    cp "$programs/$program" /bundle/libexec/
    ldd "$programs/$program" | awk '$2 == "=>" && $3 ~ /^\\// {{print $3}}' |
        while read -r library; do cp -Ln "$library" /bundle/lib/; done
    printf '#!/bin/sh\\nexec {mount}/lib/loader --library-path {mount}/lib {mount}/libexec/%s "$@"\\n' \\
        "$program" > "/bundle/bin/$program"
    chmod 755 "/bundle/bin/$program"
done
"""


class ServerError(RuntimeError):
    pass


_running: set[Server] = set()


def _stop_all() -> None:
    for server in list(_running):
        server.stop()


atexit.register(_stop_all)


class Server:
    """One database server over one directory: `data` holds the cluster, `run` the socket."""

    def __init__(self, directory: Path, image: Path) -> None:
        self.directory = directory
        self.image = image
        self.process: subprocess.Popen[bytes] | None = None

    @property
    def data(self) -> Path:
        return self.directory / "data"

    @property
    def sockets(self) -> Path:
        return self.directory / "run"

    @property
    def log(self) -> Path:
        return self.directory / "log"

    def command(self, *program: str) -> list[str]:
        """A program of the server image, with the cluster and the socket directory shown."""
        binds = [f"{self.data}:{DATA_DIRECTORY}", f"{self.sockets}:{SOCKET_DIRECTORY}"]
        arguments = ["apptainer", "exec"]
        for bind in binds:
            arguments += ["--bind", bind]
        return [*arguments, str(self.image), *program]

    def _run(self, *program: str) -> None:
        done = subprocess.run(self.command(*program), capture_output=True, text=True)
        if done.returncode != 0:
            raise ServerError(f"{program[0]} failed: {(done.stdout + done.stderr)[-1000:]}")

    def create(self, database: str) -> None:
        """A new cluster holding one empty database, with the server left running."""
        if self.directory.exists():
            shutil.rmtree(self.directory)
        self.data.mkdir(parents=True)
        self.sockets.mkdir()
        # Small write-ahead log segments keep the cluster, which is copied for every run, small.
        self._run("initdb", "-D", DATA_DIRECTORY, "-U", SUPERUSER, "-A", "trust", "--wal-segsize=1")
        (self.data / "pg_hba.conf").write_text(HOST_ACCESS)
        self.start()
        try:
            self._run("createdb", "-h", SOCKET_DIRECTORY, "-U", SUPERUSER, database)
        except ServerError:
            self.stop()
            raise

    def start(self) -> None:
        # No network address: the only way in is the socket in this run's directory.
        serve = ["postgres", "-D", DATA_DIRECTORY, "-c", "listen_addresses="]
        serve += ["-c", f"unix_socket_directories={SOCKET_DIRECTORY}", "-c", "autovacuum=off"]
        self.sockets.mkdir(exist_ok=True)
        with self.log.open("ab") as log:
            self.process = subprocess.Popen(
                self.command(*serve), stdout=log, stderr=log, stdin=subprocess.DEVNULL
            )
        _running.add(self)
        ready = self.command("pg_isready", "-q", "-h", SOCKET_DIRECTORY)
        deadline = time.monotonic() + START_TIMEOUT_SEC
        while time.monotonic() < deadline:
            if subprocess.run(ready, capture_output=True).returncode == 0:
                return
            if self.process.poll() is not None:
                break
            time.sleep(0.3)
        self.stop()
        raise ServerError(f"the database server did not start, see {self.log}")

    def postmaster(self) -> int | None:
        """The server's process, as its own pid file names it; None once it has stopped."""
        try:
            return int((self.data / "postmaster.pid").read_text().split()[0])
        except (OSError, ValueError, IndexError):
            return None

    def stop(self) -> None:
        """Stop the server and wait until it has gone. A server that does not stop when asked
        is told to quit at once."""
        _running.discard(self)
        if self.process is None:
            return
        stop = self.command("pg_ctl", "-D", DATA_DIRECTORY, "stop", "-m", "fast", "-t", "30")
        subprocess.run(stop, capture_output=True)
        try:
            self.process.wait(timeout=STOP_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            pid = self.postmaster()
            if pid is not None:
                os.kill(pid, signal.SIGQUIT)
            self.process.kill()
            self.process.wait()
        self.process = None


def server_image(workspace_image: Path) -> Path:
    """The server image lies beside the image the commands run in."""
    return workspace_image.parent / "postgres.sif"


def client_programs(image: Path, directory: Path) -> Path:
    """The client programs of the server image as a directory that can be shown to another
    image. Collected once; a directory that is there is complete."""
    if directory.exists():
        return directory
    partial = directory.with_name(f"{directory.name}.{os.getpid()}")
    partial.mkdir(parents=True)
    script = COLLECT_CLIENT.format(programs=" ".join(CLIENT_PROGRAMS), mount=CLIENT_MOUNT)
    collect = ["apptainer", "exec", "--bind", f"{partial}:/bundle", str(image), "sh", "-c", script]
    done = subprocess.run(collect, capture_output=True, text=True)
    if done.returncode != 0:
        shutil.rmtree(partial, ignore_errors=True)
        raise ServerError(f"the client programs could not be collected: {done.stderr[-1000:]}")
    try:
        partial.rename(directory)
    except OSError:
        # Another run collected them first.
        shutil.rmtree(partial, ignore_errors=True)
    return directory


def reaching(workspace: Workspace, server: Server, client: Path, database: Database) -> Workspace:
    """The workspace with the server in reach of every command run in it: the socket, the
    client programs, and the connection the programs use when given none."""
    binds = [f"{server.sockets}:{SOCKET_DIRECTORY}", f"{client}:{CLIENT_MOUNT}:ro"]
    binds += [
        f"{client / 'bin' / program}:{PROGRAM_DIRECTORY}/{program}:ro"
        for program in CLIENT_PROGRAMS
    ]
    environment = {"PGHOST": SOCKET_DIRECTORY, "PGUSER": SUPERUSER, "PGDATABASE": database.name}
    return workspace.model_copy(update={"binds": binds, "environment": environment})


def server_for(workspace: Workspace, workspace_image: Path) -> tuple[Server, Path]:
    """The server of a workspace, not yet started, and the client programs for its commands.
    The cluster lies in the workspace's directory, so a copy of a built workspace carries its
    own copy of the database."""
    image = server_image(workspace_image)
    client = client_programs(image, workspace_image.parent / "postgres_client")
    return Server(workspace.directory / "pg", image), client
