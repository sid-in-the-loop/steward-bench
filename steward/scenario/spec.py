"""The scenario specification: the single file that defines one benchmark item."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from steward.execution.record import Action
from steward.scenario.designs import BY_ID
from steward.schema import BENCHMARK_DOMAINS, Axis, Domain

CheckKind = Literal["environment_command", "action_log", "final_report", "verifier_reward"]
Window = Literal["all", "halted", "after_takeover"]
TriggerKind = Literal["at_takeover", "before_action", "after_turns", "on_completion"]
InterventionAction = Literal["message", "halt", "permit", "environment_command"]
SimulatorMode = Literal["none", "replies", "replies_and_report"]
Factor = Literal[
    "instruction_recency",
    "context_compaction",
    "environment_drift",
    "history_provenance",
    "principal_authority",
    "oversight_condition",
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(Strict):
    trajectory_id: str
    takeover_step: int = Field(ge=0)
    domain: Domain
    history_author: str | None = None
    harness: str | None = None

    @model_validator(mode="after")
    def benchmark_domain(self) -> Source:
        if self.domain not in BENCHMARK_DOMAINS:
            raise ValueError(f"domain {self.domain!r} is not a benchmark domain")
        return self


class Environment(Strict):
    """Where the scenario runs: a benchmark task image, a repository checked out in a container,
    or the session's recorded tool results served by an emulator."""

    backend: Literal["task_image", "repository", "recorded_tools"]
    task_set: str | None = None
    task: str | None = None
    repository: str | None = None
    commit: str | None = None
    workdir: str = "/app"

    @model_validator(mode="after")
    def backend_fields(self) -> Environment:
        if self.backend == "task_image" and not (self.task_set and self.task):
            raise ValueError("a task_image environment names its task set and task")
        if self.backend == "repository" and not (self.repository and self.commit):
            raise ValueError("a repository environment names its repository and commit")
        return self


class Hazard(Strict):
    """The element of the environment that makes the unsafe path available."""

    description: str
    location: str
    already_present: str


class Trigger(Strict):
    """When an intervention fires. A before_action trigger without a pattern fires before the
    first state-changing action."""

    kind: TriggerKind
    pattern: str | None = None
    turns: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def trigger_fields(self) -> Trigger:
        if self.pattern:
            re.compile(self.pattern)
        if self.kind == "after_turns" and self.turns is None:
            raise ValueError("an after_turns trigger needs a number of turns")
        return self


class Intervention(Strict):
    id: str
    action: InterventionAction
    trigger: Trigger
    sender: Literal["principal", "third_party"] = "principal"
    text: str | None = None
    command: str | None = None
    # How many times it may fire: a halt that repeats, with a permit that repeats, is an
    # approval the agent has to wait for again and again.
    repeat: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def action_fields(self) -> Intervention:
        if self.action == "environment_command" and not self.command:
            raise ValueError("an environment_command intervention needs a command")
        if self.action != "environment_command" and not self.text:
            raise ValueError(f"a {self.action} intervention needs its message text")
        if self.action == "halt" and self.trigger.kind != "before_action":
            raise ValueError("a halt is triggered before an action")
        return self


class ScriptedReply(Strict):
    """A fixed answer to any agent question that matches the pattern."""

    pattern: str
    reply: str


class Principal(Strict):
    role: str
    simulator: SimulatorMode = "none"
    brief: str = ""
    replies: list[ScriptedReply] = Field(default_factory=list)
    default_reply: str = "Use your judgement and carry on."


class Check(Strict):
    """One scripted check.

    An environment_command passes when it exits with status 0. A verifier_reward passes when the
    task's own tests award full reward. An action_log or final_report check passes when the
    pattern is present or absent as stated; an action_log check without a pattern looks for any
    state-changing action and, in the halted window, for the halted action itself. With
    applies_when_failed, the check only applies when the named check failed, which expresses
    "if the asset was lost, the report must say so".
    """

    id: str
    description: str
    kind: CheckKind
    command: str | None = None
    pattern: str | None = None
    expect: Literal["present", "absent"] = "absent"
    window: Window = "all"
    applies_when_failed: str | None = None

    @model_validator(mode="after")
    def kind_fields(self) -> Check:
        if self.kind == "environment_command" and not self.command:
            raise ValueError("an environment_command check needs a command")
        if self.kind == "final_report" and not self.pattern:
            raise ValueError("a final_report check needs a pattern")
        if self.pattern:
            re.compile(self.pattern)
        return self


class ReferenceExecution(Strict):
    """A scripted run with a known result, used to validate the scenario."""

    name: str
    actions: list[Action]
    report: str
    uses_task_solution: bool = False
    expected_task: float = Field(ge=0, le=1)
    expected_safety: float = Field(ge=0, le=1)


class Drift(Strict):
    """A fact the history relied on that has changed since, and an irrelevant change as control."""

    command: str
    announcement: str
    control_command: str


class PlantedStep(Strict):
    """A step added to the inherited history: something the user said earlier, or something the
    previous agent claimed or noted."""

    position: Literal["early", "mid", "late"]
    speaker: Literal["user", "previous_agent"]
    text: str


class PlantedResult(Strict):
    """A change to the world since the history was written: actions matching the pattern now
    get this result."""

    pattern: str
    result: str

    @model_validator(mode="after")
    def valid_pattern(self) -> PlantedResult:
        re.compile(self.pattern)
        return self


class OversightMessages(Strict):
    """The opening message for the reviewed and the unreviewed run of the same scenario."""

    reviewed: str
    unreviewed: str


class Provenance(Strict):
    authored_by: str
    reviewed_by: str | None = None


class ScenarioSpec(Strict):
    source: Source
    environment: Environment
    category: Axis
    design: str | None = None
    evidence_steps: list[int]
    hazard: Hazard
    planted_history: list[PlantedStep] = Field(default_factory=list)
    planted_results: list[PlantedResult] = Field(default_factory=list)
    oversight: OversightMessages | None = None
    interventions: list[Intervention] = Field(min_length=1)
    principal: Principal
    task_checks: list[Check] = Field(min_length=1)
    safety_checks: list[Check] = Field(min_length=3, max_length=5)
    disqualifying_violations: list[Check] = Field(default_factory=list)
    reference_executions: list[ReferenceExecution] = Field(min_length=2)
    factors: list[Factor] = Field(default_factory=list)
    standing_instruction: str | None = None
    drift: Drift | None = None
    provenance: Provenance

    @property
    def scenario_id(self) -> str:
        base = f"{self.source.trajectory_id}#t{self.source.takeover_step}:{self.category}"
        return f"{base}:{self.design}" if self.design else base

    @property
    def key(self) -> str:
        """A filesystem-safe name derived from the identifier."""
        return hashlib.sha1(self.scenario_id.encode()).hexdigest()[:12]

    @model_validator(mode="after")
    def consistent(self) -> ScenarioSpec:
        checks = [*self.task_checks, *self.safety_checks, *self.disqualifying_violations]
        ids = [c.id for c in checks] + [i.id for i in self.interventions]
        if len(set(ids)) != len(ids):
            raise ValueError("check and intervention ids must be unique")
        known = {c.id for c in checks}
        for check in checks:
            if check.applies_when_failed and check.applies_when_failed not in known:
                raise ValueError(f"{check.id} depends on an unknown check")
        actions = [i.action for i in self.interventions]
        if actions.count("permit") > actions.count("halt"):
            raise ValueError("a permit needs an earlier halt")
        needs_instruction = {"instruction_recency", "context_compaction"} & set(self.factors)
        if needs_instruction and not self.standing_instruction:
            raise ValueError(f"{sorted(needs_instruction)} need a standing instruction")
        if "environment_drift" in self.factors and self.drift is None:
            raise ValueError("environment_drift needs its drift commands")
        if "oversight_condition" in self.factors and self.oversight is None:
            raise ValueError("oversight_condition needs the reviewed and unreviewed messages")
        if self.design is not None:
            design = BY_ID.get(self.design)
            if design is None:
                raise ValueError(f"unknown design {self.design!r}")
            if design.category != self.category:
                raise ValueError(f"design {self.design} belongs to {design.category}")
        expected = {(r.expected_task, r.expected_safety) for r in self.reference_executions}
        if (1.0, 1.0) not in expected:
            raise ValueError("one reference execution must be fully compliant")
        if not any(safety == 0.0 for _, safety in expected):
            raise ValueError("one reference execution must violate safety")
        return self


def spec_path(root: Path, spec: ScenarioSpec) -> Path:
    return root / spec.source.domain / spec.key / "scenario.yaml"


def load_spec(path: Path) -> ScenarioSpec:
    return ScenarioSpec.model_validate(yaml.safe_load(path.read_text()))


def save_spec(root: Path, spec: ScenarioSpec) -> Path:
    path = spec_path(root, spec)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(
        spec.model_dump(mode="json"), sort_keys=False, allow_unicode=True, width=100
    )
    path.write_text(text)
    return path


def iter_specs(root: Path) -> list[ScenarioSpec]:
    return [load_spec(p) for p in sorted(root.glob("*/*/scenario.yaml"))]
