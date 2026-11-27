"""A PostgreSQL server that stays up for a whole run of a repository case.

Each shell command of a case runs in its own container, so nothing a command starts outlives
it. A case that needs a database gets one server, started outside those containers when the
run is prepared and stopped when the run closes. Its data directory and its socket lie in the
run's own directory, so runs in parallel share nothing. The server runs confined: in its own
process and network namespace, with no network, and with nothing of the machine shown to it
but its data directory, its socket directory and its access rules, which it can only read.
Every command reaches it through the socket, with the client programs of the server's own
image shown to the command's container.

The agent connects as a role of its own, named after the case's user: an administrator that
owns the case's databases and what is in them, may create databases and holds every other
ordinary role with the right to grant it, but is neither a superuser nor allowed to create
roles, since PostgreSQL 15 lets a role that may create roles grant itself the roles that run
programs and read files on the server. It changes a password or drops a role through two
functions of the harness's own schema. The harness's own scripts and checks connect as the
superuser with a password drawn for each start of the server, which only their containers are
shown; while one of them runs, every function a role other than a superuser owns runs with
its owner's rights, so code of the agent's (a trigger, a default, a view's function) that the
harness's SQL reaches cannot act as the superuser.
"""

from __future__ import annotations

import atexit
import os
import secrets
import shutil
import signal
import subprocess
import time
from pathlib import Path

from steward.environment.workspace import Workspace, scope_environment, scope_prefix
from steward.task import Database

SUPERUSER = "postgres"
SOCKET_DIRECTORY = "/var/run/postgresql"
DATA_DIRECTORY = "/var/lib/postgresql/data"
ACCESS_MOUNT = "/etc/steward/pg_hba.conf"
PASSWORD_MOUNT = "/opt/steward/pgpass"
CLIENT_MOUNT = "/opt/postgres-client"
CLIENT_PROGRAMS = ("psql", "pg_dump", "pg_restore", "pg_isready")
PROGRAM_DIRECTORY = "/usr/local/bin"
START_TIMEOUT_SEC = 60
# What one query may write to temporary files; the server's tasks and memory are bounded by
# the systemd scope it runs in, where the machine has one (`scope_prefix`).
TEMP_FILE_LIMIT = "4GB"
STOP_TIMEOUT_SEC = 60
# Who may connect through the socket while the server is being set up: the superuser alone.
SETUP_ACCESS = f"local all {SUPERUSER} trust\n"
# And afterwards: the superuser with its password, the agent's role without one, as the
# operating system's account does on a database host, and every other role with its password.
ACCESS = (
    f"local all {SUPERUSER} scram-sha-256\nlocal all {{role}} trust\nlocal all all scram-sha-256\n"
)
# The agent's role: an administrator of the case's databases without superuser rights. It
# reads every session's activity and may end the sessions of other roles that are not
# superusers, as an administrator does.
AGENT_ROLE = """DO $steward$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
        CREATE ROLE "{role}" LOGIN;
    END IF;
END $steward$;
ALTER ROLE "{role}" LOGIN NOSUPERUSER CREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD NULL;
GRANT pg_monitor, pg_signal_backend TO "{role}";
"""
# The harness's schema in every database, owned by the superuser and never handed over. Its
# two functions do for the agent's role what only a role that may create roles could: change
# another ordinary role's password, and drop one. They refuse a superuser, a reserved role and
# the agent's own.
HARNESS_SCHEMA = "steward"
HARNESS_FUNCTIONS = """CREATE SCHEMA IF NOT EXISTS {schema} AUTHORIZATION {superuser};
REVOKE ALL ON SCHEMA {schema} FROM PUBLIC;
GRANT USAGE ON SCHEMA {schema} TO "{role}";
CREATE OR REPLACE FUNCTION {schema}.ordinary(target name) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $steward$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = target) THEN
        RAISE EXCEPTION 'role "%" does not exist', target;
    END IF;
    IF target LIKE 'pg\\_%' OR target = '{role}' OR target = '{superuser}'
       OR (SELECT rolsuper FROM pg_roles WHERE rolname = target) THEN
        RAISE EXCEPTION 'permission denied for role %', target;
    END IF;
END $steward$;
CREATE OR REPLACE FUNCTION {schema}.set_password(target name, password text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $steward$
BEGIN
    PERFORM {schema}.ordinary(target);
    EXECUTE format('ALTER ROLE %I PASSWORD %L', target, password);
END $steward$;
CREATE OR REPLACE FUNCTION {schema}.drop_role(target name) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $steward$
BEGIN
    PERFORM {schema}.ordinary(target);
    EXECUTE format('DROP ROLE %I', target);
END $steward$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {schema} FROM PUBLIC;
GRANT EXECUTE ON FUNCTION {schema}.set_password(name, text), {schema}.drop_role(name) TO "{role}";
"""
# What the superuser owns in a database goes to the agent's role: everything outside the
# system schemas that no extension holds, but for a sequence a table holds, which follows
# its table. Each change is made on its own, so one that fails leaves the rest. Run after
# every script of the harness, so
# what an item script creates is the agent's to change, as it was when the agent connected as
# the superuser.
HAND_OVER = """DO $steward$
DECLARE
    agent text := '{role}';
    found record;
BEGIN
    FOR found IN
        SELECT n.nspname FROM pg_namespace n
        WHERE n.nspowner = '{superuser}'::regrole AND n.nspname <> '{schema}'
          AND n.nspname NOT LIKE 'pg\\_%' AND n.nspname <> 'information_schema'
    LOOP
        BEGIN
            EXECUTE format('ALTER SCHEMA %I OWNER TO %I', found.nspname, agent);
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING '%', SQLERRM;
        END;
    END LOOP;
    FOR found IN
        SELECT c.oid::regclass AS name, c.relkind FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relowner = '{superuser}'::regrole AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
          AND n.nspname NOT LIKE 'pg\\_%' AND n.nspname NOT IN ('information_schema', '{schema}')
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend d
              WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid
                AND (d.deptype = 'e' OR (c.relkind = 'S' AND d.deptype IN ('a', 'i')))
          )
    LOOP
        BEGIN
            EXECUTE format(
                'ALTER %s %s OWNER TO %I',
                CASE found.relkind
                    WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW'
                    WHEN 'f' THEN 'FOREIGN TABLE' WHEN 'S' THEN 'SEQUENCE' ELSE 'TABLE'
                END,
                found.name, agent
            );
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING '%', SQLERRM;
        END;
    END LOOP;
    FOR found IN
        SELECT p.oid::regprocedure AS name, p.prokind FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE p.proowner = '{superuser}'::regrole
          AND n.nspname NOT LIKE 'pg\\_%' AND n.nspname NOT IN ('information_schema', '{schema}')
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend d
              WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e'
          )
    LOOP
        BEGIN
            EXECUTE format(
                'ALTER %s %s OWNER TO %I',
                CASE WHEN found.prokind = 'a' THEN 'AGGREGATE' ELSE 'ROUTINE' END, found.name, agent
            );
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING '%', SQLERRM;
        END;
    END LOOP;
    FOR found IN
        SELECT t.oid::regtype AS name FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typowner = '{superuser}'::regrole AND t.typtype IN ('c', 'd', 'e', 'r')
          AND n.nspname NOT LIKE 'pg\\_%' AND n.nspname NOT IN ('information_schema', '{schema}')
          AND (t.typrelid = 0 OR (SELECT relkind FROM pg_class WHERE oid = t.typrelid) = 'c')
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend d
              WHERE d.classid = 'pg_type'::regclass AND d.objid = t.oid
                AND d.deptype IN ('e', 'i')
          )
    LOOP
        BEGIN
            EXECUTE format('ALTER TYPE %s OWNER TO %I', found.name, agent);
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING '%', SQLERRM;
        END;
    END LOOP;
END $steward$;
"""
# The databases handed over: every one but the templates and the maintenance database; of
# them, the ones the superuser owns become the agent's. The agent's role is also given every
# other ordinary role with the right to grant it, so that, as an administrator could, it may
# drop or change what another person's role owns (`DROP OWNED BY`, a private schema) and grant
# and revoke a group role. Without the right to create roles that is the only way it can, so
# it shows among a group role's members.
DATABASES = (
    "SELECT datname FROM pg_database WHERE NOT datistemplate AND datname <> 'postgres' ORDER BY 1"
)
HAND_OVER_DATABASES = """DO $steward$
DECLARE
    found text;
BEGIN
    FOR found IN
        SELECT datname FROM pg_database
        WHERE NOT datistemplate AND datname <> 'postgres' AND datdba = '{superuser}'::regrole
    LOOP
        BEGIN
            EXECUTE format('ALTER DATABASE %I OWNER TO %I', found, '{role}');
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING '%', SQLERRM;
        END;
    END LOOP;
    FOR found IN
        SELECT r.rolname FROM pg_roles r
        WHERE NOT r.rolsuper AND r.rolname <> '{role}' AND r.rolname NOT LIKE 'pg\\_%'
          AND NOT pg_has_role(r.rolname, '{role}', 'MEMBER')
          AND NOT EXISTS (
              SELECT 1 FROM pg_auth_members m
              WHERE m.roleid = r.oid AND m.member = '{role}'::regrole AND m.admin_option
          )
    LOOP
        BEGIN
            EXECUTE format('GRANT %I TO %I WITH ADMIN OPTION', found, '{role}');
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING '%', SQLERRM;
        END;
    END LOOP;
END $steward$;
"""
# Run in a command's container by the harness after each of its scripts, with the client
# programs and the superuser's password file shown. Its output and status are not read.
HAND_OVER_SCRIPT = """psql -X -q -U {superuser} -d postgres >/dev/null 2>&1 <<'__steward_end'
{databases_over}
__steward_end
for __steward_database in $(psql -X -At -U {superuser} -d postgres -c "{databases}"); do
    psql -X -q -U {superuser} -d "$__steward_database" >/dev/null 2>&1 <<'__steward_end'
{hand_over}
__steward_end
done
unset __steward_database
"""
# The functions that a role other than a superuser owns and that run with their caller's
# rights, outside the system schemas and extensions: while a harness script runs, they run
# with their owner's rights instead, so the superuser's SQL cannot run code of the agent's as
# the superuser, whether a trigger, a rule, a default, a constraint, an index or a view reaches
# it, or a name the agent's schema puts first on the search path. Procedures are left as they
# are, since one that commits cannot run with its owner's rights.
OWNED_ELSEWHERE = (
    "SELECT p.oid FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
    "JOIN pg_roles r ON r.oid = p.proowner WHERE p.prokind IN ('f', 'w') AND NOT p.prosecdef "
    "AND NOT r.rolsuper AND n.nspname NOT LIKE 'pg\\_%' AND n.nspname <> 'information_schema' "
    "AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_proc'::regclass "
    "AND d.objid = p.oid AND d.deptype = 'e')"
)
# The changed functions are listed in /dev/shm of the harness command's own container, which
# `as_agent` covers, so the script after it changes back exactly those.
OWNERS_RIGHTS = r"""for __steward_database in $(psql -X -At -U {superuser} -d postgres -c "{databases}"); do
    __steward_list=/dev/shm/steward-owners-rights-$__steward_database
    psql -X -At -U {superuser} -d "$__steward_database" -c "{find}" > "$__steward_list"
    while read -r __steward_oid; do
        echo "SELECT format('ALTER FUNCTION %s SECURITY DEFINER', $__steward_oid::oid::regprocedure) \gexec"
    done < "$__steward_list" | psql -X -q -U {superuser} -d "$__steward_database"
done
unset __steward_database __steward_list __steward_oid
"""
INVOKERS_RIGHTS = r"""for __steward_list in /dev/shm/steward-owners-rights-*; do
    [ -f "$__steward_list" ] || continue
    __steward_database=${{__steward_list#/dev/shm/steward-owners-rights-}}
    while read -r __steward_oid; do
        echo "SELECT format('ALTER FUNCTION %s SECURITY INVOKER', p.oid::regprocedure) FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner WHERE p.oid = $__steward_oid AND p.prosecdef AND NOT r.rolsuper \gexec"
    done < "$__steward_list" | psql -X -q -U {superuser} -d "$__steward_database"
    rm -f "$__steward_list"
done
unset __steward_database __steward_list __steward_oid
"""
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


def hand_over_script(role: str) -> str:
    """The script that gives the superuser's objects to the agent's role; see HAND_OVER."""
    names = {"role": role, "superuser": SUPERUSER, "schema": HARNESS_SCHEMA}
    return HAND_OVER_SCRIPT.format(
        superuser=SUPERUSER,
        databases=DATABASES,
        databases_over=HAND_OVER_DATABASES.format(**names),
        hand_over=HARNESS_FUNCTIONS.format(**names) + HAND_OVER.format(**names),
    )


def owners_rights_script() -> str:
    """Run before each of the harness's own scripts: see OWNERS_RIGHTS."""
    return OWNERS_RIGHTS.format(superuser=SUPERUSER, databases=DATABASES, find=OWNED_ELSEWHERE)


def invokers_rights_script() -> str:
    """Run after each of the harness's own scripts: the functions `owners_rights_script`
    changed run with their callers' rights again."""
    return INVOKERS_RIGHTS.format(superuser=SUPERUSER, databases=DATABASES)


class Server:
    """One database server over one directory: `data` holds the cluster, `run` the socket.
    `role` is the agent's role."""

    def __init__(self, directory: Path, image: Path, role: str) -> None:
        self.directory = directory
        self.image = image
        self.role = role
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

    @property
    def access(self) -> Path:
        return self.directory / "pg_hba.conf"

    @property
    def password_file(self) -> Path:
        """The superuser's password as a client reads it; shown only to the harness."""
        return self.directory / "pgpass"

    def command(self, *program: str) -> list[str]:
        """A program of the server image, confined, with the cluster, the socket directory and
        the access rules shown."""
        binds = [
            f"{self.data}:{DATA_DIRECTORY}",
            f"{self.sockets}:{SOCKET_DIRECTORY}",
            f"{self.access}:{ACCESS_MOUNT}:ro",
        ]
        arguments = ["apptainer", "exec", "--containall", "--pid", "--net", "--network", "none"]
        for bind in binds:
            arguments += ["--bind", bind]
        return [*arguments, str(self.image), *program]

    def client(self, *program: str) -> list[str]:
        """A client program of the server image run by the harness as the superuser."""
        arguments = ["apptainer", "exec", "--containall", "--pid", "--net", "--network", "none"]
        arguments += ["--bind", f"{self.sockets}:{SOCKET_DIRECTORY}"]
        if self.password_file.exists():
            arguments += ["--bind", f"{self.password_file}:{PASSWORD_MOUNT}:ro"]
            arguments += ["--env", f"PGPASSFILE={PASSWORD_MOUNT}"]
        arguments += ["--env", f"PGHOST={SOCKET_DIRECTORY}", "--env", f"PGUSER={SUPERUSER}"]
        return [*arguments, str(self.image), *program]

    def _run(self, command: list[str], stdin: str | None = None) -> None:
        done = subprocess.run(command, capture_output=True, text=True, input=stdin)
        if done.returncode != 0:
            raise ServerError(f"{command[-1]} failed: {(done.stdout + done.stderr)[-1000:]}")

    def create(self, database: str) -> None:
        """A new cluster holding one empty database, with the server left running."""
        if self.directory.exists():
            shutil.rmtree(self.directory)
        self.data.mkdir(parents=True)
        self.sockets.mkdir()
        self.access.write_text(SETUP_ACCESS)
        # Small write-ahead log segments keep the cluster, which is copied for every run, small.
        initdb = ["initdb", "-D", DATA_DIRECTORY, "-U", SUPERUSER, "-A", "trust", "--wal-segsize=1"]
        self._run(self.command(*initdb))
        self.start()
        try:
            self._run(self.client("createdb", database))
            self.hand_over()
        except ServerError:
            self.stop()
            raise

    def start(self) -> None:
        """Start the server and set it up for this run: the superuser's password is drawn
        afresh, the agent's role is made and given what the superuser owns, and only then are
        the access rules that let the agent in read."""
        # No network address: the only way in is the socket in this run's directory.
        serve = ["postgres", "-D", DATA_DIRECTORY, "-c", "listen_addresses="]
        serve += ["-c", f"unix_socket_directories={SOCKET_DIRECTORY}", "-c", "autovacuum=off"]
        serve += ["-c", f"hba_file={ACCESS_MOUNT}", "-c", f"temp_file_limit={TEMP_FILE_LIMIT}"]
        self.sockets.mkdir(exist_ok=True)
        # The access file is rewritten in place, never replaced, so the server's view of it,
        # which is bound to the file, follows.
        self.access.write_text(SETUP_ACCESS)
        self.password_file.unlink(missing_ok=True)
        with self.log.open("ab") as log:
            self.process = subprocess.Popen(
                [*scope_prefix(), *self.command(*serve)],
                stdout=log,
                stderr=log,
                stdin=subprocess.DEVNULL,
                env=scope_environment(),
            )
        _running.add(self)
        try:
            self._wait_ready()
            self._set_up()
        except BaseException:
            self.stop()
            raise

    def _wait_ready(self) -> None:
        assert self.process is not None
        ready = self.command("pg_isready", "-q", "-h", SOCKET_DIRECTORY)
        deadline = time.monotonic() + START_TIMEOUT_SEC
        while time.monotonic() < deadline:
            if subprocess.run(ready, capture_output=True).returncode == 0:
                return
            if self.process.poll() is not None:
                break
            time.sleep(0.3)
        raise ServerError(f"the database server did not start, see {self.log}")

    def _set_up(self) -> None:
        password = secrets.token_hex(24)
        self.access.write_text(ACCESS.format(role=self.role))
        setup = f"ALTER ROLE {SUPERUSER} PASSWORD '{password}';\n"
        setup += AGENT_ROLE.format(role=self.role)
        self._run(self.client("psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", "postgres"), setup)
        self.password_file.write_text(f"*:*:*:{SUPERUSER}:{password}\n")
        self.password_file.chmod(0o600)
        self.hand_over()
        reload = ["psql", "-X", "-q", "-d", "postgres", "-c", "SELECT pg_reload_conf()"]
        self._run(self.client(*reload))

    def hand_over(self) -> None:
        """Give what the superuser owns to the agent's role; see HAND_OVER."""
        self._run(self.client("sh", "-c", hand_over_script(self.role)))

    def postmaster(self) -> int | None:
        """The server's process in its own namespace, as its pid file names it; None once it
        has stopped."""
        try:
            return int((self.data / "postmaster.pid").read_text().split()[0])
        except (OSError, ValueError, IndexError):
            return None

    def stop(self) -> None:
        """Stop the server and wait until it has gone. The container runtime passes signals
        on to the server: it is asked to shut down fast, and told to quit at once if it has
        not gone in time."""
        _running.discard(self)
        if self.process is None:
            return
        process = self.process
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=STOP_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            process.send_signal(signal.SIGQUIT)
            try:
                process.wait(timeout=STOP_TIMEOUT_SEC)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        self.process = None
        self.password_file.unlink(missing_ok=True)


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
    client programs, and the connection the programs use when given none, which is the
    agent's role for the agent's commands and the superuser for the harness's own."""
    # Read-only: a command can connect through the socket but put nothing of its own there.
    binds = [f"{server.sockets}:{SOCKET_DIRECTORY}:ro", f"{client}:{CLIENT_MOUNT}:ro"]
    binds += [
        f"{client / 'bin' / program}:{PROGRAM_DIRECTORY}/{program}:ro"
        for program in CLIENT_PROGRAMS
    ]
    environment = {"PGHOST": SOCKET_DIRECTORY, "PGUSER": server.role, "PGDATABASE": database.name}
    harness = {"PGUSER": SUPERUSER, "PGPASSFILE": PASSWORD_MOUNT}
    return workspace.model_copy(
        update={
            "binds": binds,
            "environment": environment,
            "harness_binds": [f"{server.password_file}:{PASSWORD_MOUNT}:ro"],
            "harness_environment": harness,
            "harness_prologue": owners_rights_script(),
            "harness_before_agent": hand_over_script(server.role),
            "harness_epilogue": invokers_rights_script() + hand_over_script(server.role),
        }
    )


def server_for(workspace: Workspace, workspace_image: Path, role: str) -> tuple[Server, Path]:
    """The server of a workspace, not yet started, and the client programs for its commands.
    The cluster lies in the workspace's directory, so a copy of a built workspace carries its
    own copy of the database. `role` is the agent's role."""
    image = server_image(workspace_image)
    client = client_programs(image, workspace_image.parent / "postgres_client")
    return Server(workspace.directory / "pg", image, role), client
