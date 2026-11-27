"""A task's `task.yaml`: who is involved, what the agent inherits, and how the outcome is
read; and where the tasks are found."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from steward.config import Settings
from steward.execution.record import Action

Role = Literal["requester", "owner", "bystander", "manager"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Database(Strict):
    """A PostgreSQL database that stays up for the whole run. One server is started when the
    run is prepared and stopped when it closes; every command, setup, item and check script
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


class During(Strict):
    """Something another person does while the agent is working: a script run in the
    environment, once. With `after_actions` it runs when the agent has taken that many
    actions; with `before_change` it runs just before the agent's first action that matches
    the task's change pattern, so the timing is the same however fast the agent works. The
    agent is not told."""

    after_actions: int | None = Field(default=None, ge=1)
    before_change: bool = False
    script: str

    @model_validator(mode="after")
    def one_trigger(self) -> During:
        if (self.after_actions is None) == (not self.before_change):
            raise ValueError("give exactly one of after_actions and before_change")
        return self


class Person(Strict):
    name: str
    role: Role
    description: str


class Lines(Strict):
    """The fixed text each person says. Every model reads the same words."""

    task: str
    team_note: str
    requester_correct: str
    requester_away: str
    requester_casual: str
    requester_outdated: str
    requester_push: list[str]
    owner_answer: str
    owner_wait: str
    bystander_approval: str


class Measures(Strict):
    """How the measures are read. `look` and `change` are patterns over the agent's actions.
    `authority` and `harm` are commands run in the environment after the run; each exits 0
    when the agent stayed within its authority, or caused no harm. `disclose` is a pattern
    the final report must match. `done`, when given, exits 0 once what the requester asked
    for has been achieved; a requester who pushes stops pushing then."""

    look: str
    change: str
    authority: str
    harm: str
    disclose: str
    done: str | None = None


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
    # A group of tasks that tell one story in several forms.
    family: str | None = None
    environment: Repo | World = Field(discriminator="kind")
    people: list[Person]
    situation: str
    harmful_action: str
    safe_path: str
    # What is true afterwards when the harm check fails, in one sentence. Empty for a twin.
    harm_statement: str = ""
    history: list[HistoryStep] = []
    setup: str
    during: During | None = None
    lines: Lines
    measures: Measures
    # Set when the task is loaded: a short hash of everything a run depends on.
    version: str = ""
    safe_reference: Reference
    # A harmless twin of a task, where the inherited belief is still true, has none.
    harmful_reference: Reference | None = None

    def person(self, role: Role) -> Person:
        return next(p for p in self.people if p.role == role)


# What a run of a task depends on. A change to any of these makes earlier runs out of date;
# a change to its title or description does not.
RUN_FIELDS = {"environment", "people", "history", "setup", "during", "lines", "measures"}
GH_STAND_IN = Path(__file__).parent / "environment" / "shims" / "gh"


def load_task(directory: Path) -> Task:
    """Read `task.yaml`; `setup` names a script beside it, which is read in."""
    raw = yaml.safe_load((directory / "task.yaml").read_text())
    raw["setup"] = (directory / raw["setup"]).read_text()
    task = Task.model_validate(raw)
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
