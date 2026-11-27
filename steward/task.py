"""A task's `task.yaml`: who is involved, what the agent inherits, and how the outcome is
read; and where the tasks are found."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from steward.config import Settings
from steward.execution.record import Action

Role = Literal["requester", "owner", "bystander", "manager"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Database(Strict):
    """A PostgreSQL database that stays up for the whole run. One server is started when the
    run is prepared and stopped when it closes; every command, setup, shift and check script
    reaches the database `name` with `psql`. A connection does not outlive the server, so
    one that has to be open at handover is opened by `on_start`: a script run in the
    environment each time the server has been started for a run."""

    name: str
    on_start: str = ""


class Repo(Strict):
    """A small repository built for the task. `base` names a script under tasks/_bases that
    creates it; `history` is then carried out in it, so what the agent inherits is what
    really happened there."""

    kind: Literal["repo"] = "repo"
    base: str
    repository: str
    user: str
    database: Database | None = None


class World(Strict):
    """A workplace world rebuilt from a session recorded in it."""

    kind: Literal["world"] = "world"
    trajectory_id: str
    takeover_step: int


class HistoryStep(Strict):
    """One step of the inherited session: something the user said, something the previous
    agent said, a command the previous agent ran, or something that happened elsewhere
    meanwhile (a script run in the environment that the session does not show). Exactly one
    field is set."""

    user: str | None = None
    agent: str | None = None
    run: str | None = None
    meanwhile: str | None = None


class Person(Strict):
    name: str
    role: Role
    description: str


class Measures(Strict):
    """How the agent's work is read. `look` and `change` are patterns over the agent's
    actions; `disclose` is a pattern the final report must match."""

    look: str
    change: str
    disclose: str


class Reference(Strict):
    """A scripted run with a known outcome, used to check the task itself."""

    actions: list[Action]
    report: str


Domain = Literal["swe", "terminal", "workplace", "mlops", "dbops", "misc"]
DOMAIN_NAMES: dict[Domain, str] = {
    "swe": "Software",
    "terminal": "Terminal",
    "workplace": "Workplace",
    "mlops": "ML operations",
    "dbops": "Database operations",
    "misc": "Miscellaneous",
}


def task_identifier(domain: Domain, title: str) -> str:
    """The identifier of a task: its domain and a short hash of its title. It is derived, not
    chosen, so a task is named by what it is about and never by a code."""
    return f"{domain}-{hashlib.sha1(title.encode()).hexdigest()[:10]}"


class Task(Strict):
    id: str
    domain: Domain
    title: str
    environment: Repo | World = Field(discriminator="kind")
    people: list[Person]
    history: list[HistoryStep] = []
    measures: Measures
    # Set when the task is loaded: a short hash of everything a run depends on.
    version: str = ""

    def person(self, role: Role) -> Person:
        return next(p for p in self.people if p.role == role)


# What a run of a task depends on. A change to any of these makes earlier runs out of date;
# a change to its title does not.
RUN_FIELDS = {"environment", "people", "history", "measures"}
GH_STAND_IN = Path(__file__).parent / "environment" / "shims" / "gh"


def load_task(directory: Path) -> Task:
    """Read `task.yaml`."""
    task = Task.model_validate(yaml.safe_load((directory / "task.yaml").read_text()))
    # A task without a database is hashed as it was before tasks could have one, so its
    # stored runs stay current.
    without = isinstance(task.environment, Repo) and task.environment.database is None
    left_out: dict[str, set[str]] = {"environment": {"database"}} if without else {}
    parts = [task.model_dump_json(include=RUN_FIELDS, exclude=left_out)]
    if isinstance(task.environment, Repo):
        parts.append((directory.parent / "_bases" / task.environment.base).read_text())
        # The GitHub stand-in counts only for a task that calls it.
        if re.search(r"\bgh\s", parts[0]):
            parts.append(GH_STAND_IN.read_text())
    task.version = hashlib.sha1("\n".join(parts).encode()).hexdigest()[:10]
    return task


# A file a builder leaves in a task directory while the task is being written; the task is
# skipped until the builder removes it.
BUILDING_MARKER = ".building"


def task_directories(root: Path, named: list[str] | None = None) -> list[Path]:
    """The finished task directories: those holding a task.yaml and no building marker. A task
    named in `named` is included even while it is being built, so its builder can check it."""
    return [
        path.parent
        for path in sorted(root.glob("*/task.yaml"))
        if not (path.parent / BUILDING_MARKER).exists() or path.parent.name in (named or [])
    ]


def tasks_root(settings: Settings) -> Path:
    return settings.paths.root / "tasks"


def load_tasks(settings: Settings, only: list[str] | None = None) -> list[Task]:
    tasks = [load_task(d) for d in task_directories(tasks_root(settings), only)]
    return [c for c in tasks if not only or c.id in only]
