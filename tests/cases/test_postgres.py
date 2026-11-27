"""The session-long database of a repository case: how the server is started and stopped,
how commands reach it, and that it is stopped on every way out. The last test runs a real
server and is skipped where the container images are not installed."""

import hashlib
import shutil
import signal
import subprocess
from pathlib import Path

import pytest

from steward.cases import postgres, run, sandbox
from steward.cases.case import RUN_FIELDS, Case, Database, Repo, load_case
from steward.config import Settings, load_settings
from steward.environment import workspace as ws
from steward.environment.repository import ReplayReport
from steward.execution.evaluate import workspace_image
from steward.execution.record import Action
from steward.schema import Step

ROOT = Path(__file__).parents[2] / "cases"
RETENTION = "dbops-1d2c83c28d"


def make_workspace(directory: Path) -> ws.Workspace:
    report = ReplayReport(
        trajectory_id="case",
        repository="northwind/ops",
        commit="",
        takeover_step=0,
        checked_out=True,
    )
    workspace = ws.Workspace(
        directory=directory, mount="/home/sam/ops", home="/home/sam", replay=report
    )
    workspace.tree.mkdir(parents=True)
    workspace.temporary.mkdir()
    return workspace


class FakeServer:
    """Stands in for a server: records what was asked of it."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.sockets = directory / "run"
        self.password_file = directory / "pgpass"
        self.role = "sam"
        self.calls: list[str] = []

    def create(self, database: str) -> None:
        self.calls.append(f"create {database}")

    def start(self) -> None:
        self.calls.append("start")

    def stop(self) -> None:
        self.calls.append("stop")


def fake_server_for(monkeypatch: pytest.MonkeyPatch) -> list[FakeServer]:
    made: list[FakeServer] = []

    def server_for(workspace: ws.Workspace, image: Path, role: str) -> tuple[FakeServer, Path]:
        assert role == "sam"
        made.append(FakeServer(workspace.directory / "pg"))
        return made[-1], Path("/images/postgres_client")

    monkeypatch.setattr(postgres, "server_for", server_for)
    return made


def test_a_case_without_a_database_keeps_the_version_it_had() -> None:
    case = load_case(ROOT / "terminal-6ad4ffd09e")
    assert isinstance(case.environment, Repo) and case.environment.database is None
    as_before = case.model_dump_json(include=RUN_FIELDS, exclude={"environment": {"database"}})
    assert '"database"' not in as_before
    parts = [as_before, (ROOT / "_bases" / case.environment.base).read_text()]
    assert case.version == hashlib.sha1("\n".join(parts).encode()).hexdigest()[:10]


def test_a_database_is_part_of_what_a_run_depends_on() -> None:
    case = load_case(ROOT / RETENTION)
    assert isinstance(case.environment, Repo)
    assert case.environment.database == Database(name="warehouse")
    assert '"database":{"name":"warehouse"' in case.model_dump_json(include=RUN_FIELDS)


def test_commands_reach_the_server_through_its_socket_and_client_programs(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path / "workspace")
    server = postgres.Server(workspace.directory / "pg", Path("/images/postgres.sif"), "sam")
    client = Path("/images/postgres_client")
    reaching = postgres.reaching(workspace, server, client, Database(name="warehouse"))
    assert workspace.binds == [] and workspace.environment == {}
    assert f"{client}/bin/psql:/usr/local/bin/psql:ro" in reaching.binds
    assert reaching.environment == {
        "PGHOST": "/var/run/postgresql",
        "PGUSER": "sam",
        "PGDATABASE": "warehouse",
    }
    assert reaching.harness_environment["PGUSER"] == "postgres"
    command = ws.container_command(reaching, "image.sif", reaching.mount, "psql -c 'select 1'", 60)
    assert f"{client}:/opt/postgres-client:ro" in command
    assert "PGDATABASE=warehouse" in command and "PGUSER=sam" in command
    assert not any("pgpass" in part or "PGUSER=postgres" in part for part in command)
    folder = tmp_path / "run"
    folder.mkdir()
    harness = ws.container_command(
        reaching, "image.sif", reaching.mount, "true", 60, harness=folder
    )
    assert f"{server.password_file}:/opt/steward/pgpass:ro" in harness
    script = (folder / "script").read_text()
    assert "PGUSER=postgres" in harness and "ALTER DATABASE" in script
    assert (
        script.index("SECURITY DEFINER") < script.index("true") < script.index("SECURITY INVOKER")
    )
    assert f"{server.sockets}:/var/run/postgresql:ro" in reaching.binds
    # What a script made just before is the agent's when a program of the agent's runs.
    assert "ALTER DATABASE" in (folder / "before-agent").read_text()
    plain = ws.container_command(workspace, "image.sif", workspace.mount, "true", 60)
    assert not any("postgres" in part.lower() or part.startswith("PG") for part in plain)


def test_the_server_runs_confined_in_its_image_over_its_own_directory(tmp_path: Path) -> None:
    server = postgres.Server(tmp_path / "pg", Path("/images/postgres.sif"), "sam")
    command = server.command("pg_isready")
    assert command[:2] == ["apptainer", "exec"]
    assert {"--containall", "--pid", "--net"} <= set(command)
    assert command[command.index("--network") + 1] == "none"
    binds = [command[i + 1] for i, part in enumerate(command) if part == "--bind"]
    assert binds == [
        f"{tmp_path / 'pg' / 'data'}:/var/lib/postgresql/data",
        f"{tmp_path / 'pg' / 'run'}:/var/run/postgresql",
        f"{tmp_path / 'pg' / 'pg_hba.conf'}:/etc/steward/pg_hba.conf:ro",
    ]
    assert command[-2:] == ["/images/postgres.sif", "pg_isready"]
    assert server.postmaster() is None
    server.data.mkdir(parents=True)
    (server.data / "postmaster.pid").write_text("4242\n/var/lib/postgresql/data\n")
    assert server.postmaster() == 4242
    assert postgres.server_image(Path("/images/workspace.sif")) == Path("/images/postgres.sif")


def test_only_the_harness_holds_the_superusers_password() -> None:
    rules = postgres.ACCESS.format(role="sam").splitlines()
    assert rules == [
        "local all postgres scram-sha-256",
        "local all sam trust",
        "local all all scram-sha-256",
    ]
    assert "NOSUPERUSER" in postgres.AGENT_ROLE


class FakeProcess:
    def __init__(self, stops_on: int | None) -> None:
        self.stops_on = stops_on
        self.signals: list[int] = []
        self.killed = False

    def poll(self) -> int | None:
        return None

    def send_signal(self, sent: int) -> None:
        self.signals.append(sent)

    def wait(self, timeout: float | None = None) -> int:
        if self.killed or (self.signals and self.signals[-1] == self.stops_on):
            return 0
        raise subprocess.TimeoutExpired("postgres", timeout or 0)

    def kill(self) -> None:
        self.killed = True


def test_stopping_asks_the_server_first_and_makes_it_quit_if_it_does_not(tmp_path: Path) -> None:
    server = postgres.Server(tmp_path / "pg", Path("/images/postgres.sif"), "sam")
    server.stop()

    polite = FakeProcess(stops_on=signal.SIGINT)
    server.process = polite  # type: ignore[assignment]
    server.stop()
    assert polite.signals == [signal.SIGINT] and server.process is None

    stubborn = FakeProcess(stops_on=signal.SIGQUIT)
    server.process = stubborn  # type: ignore[assignment]
    server.stop()
    assert stubborn.signals == [signal.SIGINT, signal.SIGQUIT] and not stubborn.killed

    dead = FakeProcess(stops_on=None)
    server.process = dead  # type: ignore[assignment]
    server.stop()
    assert dead.killed and server.process is None


def test_client_programs_are_collected_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[list[str]] = []

    def collect(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        ran.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(postgres.subprocess, "run", collect)
    directory = tmp_path / "postgres_client"
    assert postgres.client_programs(Path("/images/postgres.sif"), directory) == directory
    assert directory.is_dir() and len(ran) == 1
    assert "/images/postgres.sif" in ran[0] and "psql" in ran[0][-1]
    postgres.client_programs(Path("/images/postgres.sif"), directory)
    assert len(ran) == 1


def test_a_failed_collection_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed = subprocess.CompletedProcess([], 1, "", "no such image")
    monkeypatch.setattr(postgres.subprocess, "run", lambda *_, **__: failed)
    with pytest.raises(postgres.ServerError, match="no such image"):
        postgres.client_programs(Path("/images/postgres.sif"), tmp_path / "postgres_client")
    assert list(tmp_path.iterdir()) == []


def database_case(on_start: str = "") -> Case:
    case = load_case(ROOT / RETENTION)
    assert isinstance(case.environment, Repo)
    database = Database(name="warehouse", on_start=on_start)
    environment = case.environment.model_copy(update={"database": database})
    return case.model_copy(update={"environment": environment})


def built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ws.Workspace:
    workspace = make_workspace(tmp_path / "built")
    (workspace.directory / "workspace.json").write_text(workspace.model_dump_json())
    steps = [Step(index=0, kind="system", content="system")]
    monkeypatch.setattr(sandbox, "load", lambda _directory: (workspace, steps))
    return workspace


def test_a_run_starts_its_own_server_and_closing_stops_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    built(tmp_path, monkeypatch)
    servers = fake_server_for(monkeypatch)
    ran: list[str] = []

    def in_container(
        workspace: ws.Workspace, image: Path, cwd: str, command: str, *_: object, **__: object
    ) -> tuple[str, int, str]:
        ran.append(command)
        assert workspace.environment["PGDATABASE"] == "warehouse"
        return "", 0, cwd

    monkeypatch.setattr(sandbox, "in_container", in_container)
    prepared = run.prepare_repository(settings, database_case(on_start="open-session"))
    server = servers[0]
    scratch = server.directory.parents[1]
    assert server.calls == ["start"] and ran == ["open-session"]
    assert server.directory.parent != tmp_path / "built" and scratch.is_dir()
    assert prepared.executor.workspace.environment["PGHOST"] == "/var/run/postgresql"
    prepared.close()
    assert server.calls == ["start", "stop"] and not scratch.exists()


def test_a_server_is_stopped_when_on_start_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    built(tmp_path, monkeypatch)
    servers = fake_server_for(monkeypatch)
    monkeypatch.setattr(sandbox, "in_container", lambda *_, **__: ("no such role", 1, "/"))
    with pytest.raises(RuntimeError, match="no such role"):
        run.prepare_repository(settings, database_case(on_start="open-session"))
    assert servers[0].calls[-1] == "stop"
    assert not servers[0].directory.parents[1].exists()


def test_a_case_without_a_database_starts_no_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    built(tmp_path, monkeypatch)
    servers = fake_server_for(monkeypatch)
    prepared = run.prepare_repository(settings, load_case(ROOT / "terminal-6ad4ffd09e"))
    prepared.close()
    assert servers == []


def test_building_stops_the_server_even_when_a_script_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    servers = fake_server_for(monkeypatch)

    def in_container(workspace: ws.Workspace, *_: object, **__: object) -> tuple[str, int, str]:
        assert workspace.harness_environment["PGUSER"] == "postgres"
        return "relation does not exist", 1, "/"

    monkeypatch.setattr(sandbox, "in_container", in_container)
    with pytest.raises(sandbox.BuildError, match="base script"):
        sandbox.build(database_case(), "psql -c 'select 1'", tmp_path / "case", Path("image.sif"))
    assert servers[0].calls == ["create warehouse", "stop"]


def images_installed() -> bool:
    image = workspace_image(load_settings())
    return (
        shutil.which("apptainer") is not None
        and image.exists()
        and postgres.server_image(image).exists()
    )


@pytest.mark.skipif(not images_installed(), reason="the container images are not installed")
def test_a_real_server_keeps_its_state_across_commands_and_is_gone_after_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = load_settings()
    case = database_case().model_copy(update={"history": [], "setup": "true"})
    base = "psql -q -c 'CREATE TABLE kept (n integer)' -c 'INSERT INTO kept VALUES (1)'"
    base += " -c 'CREATE ROLE colleague' -c 'CREATE TABLE theirs (n integer)'"
    base += " -c 'ALTER TABLE theirs OWNER TO colleague'"
    directory = tmp_path / "built"
    sandbox.build(case, base, directory, workspace_image(real))
    assert postgres.Server(directory / "pg", Path("unused"), "sam").postmaster() is None

    monkeypatch.setattr(run, "built_directory", lambda _settings, _case: directory)
    prepared = run.prepare_repository(real, case)
    # The case has no inherited session, so no tool was offered to call.
    prepared.executor.offered = frozenset({"Bash"})
    try:
        server = postgres.Server(
            prepared.executor.workspace.directory / "pg", Path("unused"), "sam"
        )
        assert server.postmaster() is not None

        async def commands() -> str:
            bash = "Bash"
            await prepared.executor.execute(
                Action(tool=bash, arguments={"command": "psql -c 'INSERT INTO kept VALUES (2)'"})
            )
            return await prepared.executor.execute(
                Action(tool=bash, arguments={"command": "psql -Atc 'SELECT sum(n) FROM kept'"})
            )

        import asyncio

        assert asyncio.run(commands()).strip() == "3"

        async def agent(command: str) -> str:
            return await prepared.executor.execute(
                Action(tool="Bash", arguments={"command": command})
            )

        escapes = [
            "psql -Atc \"COPY kept FROM PROGRAM 'echo 7'\"",
            "psql -Atc \"SELECT pg_read_file('/proc/mounts')\"",
            "psql -Atc \"SELECT lo_import('/etc/passwd')\"",
            "psql -U postgres -Atc 'SELECT 1'",
        ]
        for escape in escapes:
            refused = asyncio.run(agent(escape))
            signs = ("permission denied", "must be superuser", "no password supplied")
            assert any(sign in refused for sign in signs), refused
        assert (
            asyncio.run(agent("psql -Atc 'SELECT current_user, sum(n) FROM kept'")).strip()
            == "sam|3"
        )
        assert "DROP TABLE" in asyncio.run(agent("psql -c 'DROP TABLE theirs'"))
        superuser = asyncio.run(prepared.executor.run("psql -Atc 'SELECT current_user'"))
        assert superuser.strip() == "postgres"
    finally:
        prepared.close()
    assert server.postmaster() is None and not server.directory.exists()


@pytest.mark.skipif(not images_installed(), reason="the container images are not installed")
def test_the_agents_role_cannot_reach_the_superuser_or_the_servers_programs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    real = load_settings()
    case = database_case().model_copy(update={"history": [], "setup": "true"})
    base = "psql -q -c 'CREATE TABLE kept (n integer)' -c 'CREATE ROLE readers NOLOGIN'"
    base += " -c 'CREATE ROLE colleague LOGIN' -c 'GRANT readers TO colleague'"
    directory = tmp_path / "built"
    sandbox.build(case, base, directory, workspace_image(real))
    monkeypatch.setattr(run, "built_directory", lambda _settings, _case: directory)
    prepared = run.prepare_repository(real, case)
    prepared.executor.offered = frozenset({"Bash"})

    def agent(command: str) -> str:
        action = Action(tool="Bash", arguments={"command": command})
        return str(asyncio.run(prepared.executor.execute(action)))

    def harness(command: str) -> str:
        return str(asyncio.run(prepared.executor.run(command)))

    try:
        refused = agent("psql -Atc 'GRANT pg_execute_server_program TO sam'")
        assert "must have admin option" in refused or "permission denied" in refused, refused
        assert "permission denied" in agent("psql -Atc 'CREATE ROLE intruder'")
        assert "Read-only file system" in agent("touch /var/run/postgresql/proxy")
        # What an administrator does to other roles goes through the harness's functions and
        # the right to grant each ordinary role.
        assert "REVOKE ROLE" in agent("psql -c 'REVOKE readers FROM colleague'")
        agent("psql -Atc \"SELECT steward.set_password('colleague', 'new')\"")
        refused = agent("psql -Atc \"SELECT steward.set_password('postgres', 'x')\"")
        assert "permission denied" in refused, refused
        # A trigger of the agent's that the harness's SQL fires runs with the agent's rights.
        trigger = (
            "CREATE FUNCTION grab() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            "BEGIN EXECUTE 'ALTER ROLE sam SUPERUSER'; EXCEPTION WHEN OTHERS THEN NULL; END; "
            "RETURN NEW; END $$; "
            "CREATE TRIGGER grab BEFORE INSERT ON kept FOR EACH ROW EXECUTE FUNCTION grab();"
        )
        agent(f"psql -q <<'END'\n{trigger}\nEND")
        harness("psql -q -c 'INSERT INTO kept VALUES (1)'")
        state = agent(
            "psql -Atc \"SELECT rolsuper FROM pg_roles WHERE rolname = 'sam'\" "
            "-c \"SELECT prosecdef FROM pg_proc WHERE proname = 'grab'\" "
            "-c 'SELECT count(*) FROM kept'"
        )
        assert state.split() == ["f", "f", "1"], state
        agent("psql -Atc \"SELECT steward.drop_role('colleague')\"")
        left = agent("psql -Atc \"SELECT count(*) FROM pg_roles WHERE rolname = 'colleague'\"")
        assert left.strip() == "0", left
    finally:
        prepared.close()
