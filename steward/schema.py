"""Data models for trajectories and every stage output."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Domain = Literal["swe", "terminal_devops", "ai_rnd", "workplace", "tool_use", "data"]
DOMAINS: tuple[Domain, ...] = ("swe", "terminal_devops", "ai_rnd", "workplace", "tool_use", "data")

StepKind = Literal["user", "agent_text", "action", "result", "system"]

MomentKind = Literal[
    "irreversible_action",
    "shared_resource",
    "oversight_control",
    "evaluation_rules",
    "persistent_process",
    "information_boundary",
    "user_report",
    "effort_shortcut",
    "switch_point",
    "user_interruption",
    "other",
]
MOMENT_KINDS: tuple[MomentKind, ...] = (
    "irreversible_action",
    "shared_resource",
    "oversight_control",
    "evaluation_rules",
    "persistent_process",
    "information_boundary",
    "user_report",
    "effort_shortcut",
    "switch_point",
    "user_interruption",
    "other",
)

Axis = Literal[
    "safe_interruptibility",
    "side_effects",
    "absent_supervisor",
    "reward_gaming",
    "self_modification",
]
AXES: tuple[Axis, ...] = (
    "safe_interruptibility",
    "side_effects",
    "absent_supervisor",
    "reward_gaming",
    "self_modification",
)
Criterion = Literal[
    "safe_interruptibility",
    "side_effects",
    "absent_supervisor",
    "reward_gaming",
    "self_modification",
    "distributional_shift",
    "adversaries",
    "safe_exploration",
]
CRITERIA: tuple[Criterion, ...] = (
    *AXES,
    "distributional_shift",
    "adversaries",
    "safe_exploration",
)

MAX_EXCERPT_CHARS = 200


class Step(BaseModel):
    index: int
    kind: StepKind
    content: str
    tool: str | None = None
    tool_input: dict[str, Any] | None = None
    timestamp: datetime | None = None
    is_interruption: bool = False


class Trajectory(BaseModel):
    trajectory_id: str
    source: str
    domain: Domain
    real_user: bool
    history_author: str | None = None
    harness: str | None = None
    repo: str | None = None
    base_commit: str | None = None
    task_env: str | None = None
    license: str | None = None
    steps: list[Step]
    meta: dict[str, Any] = Field(default_factory=dict)
    domain_decided_by_rule: bool = True

    @model_validator(mode="after")
    def check_identity_and_indices(self) -> Trajectory:
        if not self.trajectory_id.startswith(f"{self.source}:"):
            raise ValueError("trajectory_id must be '{source}:{native_id}'")
        for expected, step in enumerate(self.steps):
            if step.index != expected:
                raise ValueError(f"step indices must be contiguous from 0, got {step.index}")
        return self

    def agent_actions(self) -> int:
        return sum(1 for s in self.steps if s.kind == "action")

    def user_messages(self) -> int:
        return sum(1 for s in self.steps if s.kind == "user" and not s.is_interruption)


class TrajectoryMeta(BaseModel):
    """One row of data/normalized/<source>/meta.parquet: a trajectory without its steps."""

    trajectory_id: str
    source: str
    domain: Domain
    real_user: bool
    history_author: str | None
    harness: str | None
    repo: str | None
    base_commit: str | None
    task_env: str | None
    license: str | None
    domain_decided_by_rule: bool
    n_steps: int
    agent_actions: int
    user_messages: int
    meta_json: str


class StepTag(BaseModel):
    step: int
    kind: MomentKind


class StageAResult(BaseModel):
    trajectory_id: str
    source: str
    domain: Domain
    passed: bool
    failed_check: str | None
    agent_actions: int
    user_messages: int
    state_changing_actions: int
    tags: list[StepTag]
    prescore: float


Offline = Literal["likely", "unclear", "unlikely"]
BDomain = Literal["swe", "terminal_devops", "ai_rnd", "workplace", "tool_use", "data", "other"]


class StageBOutput(BaseModel):
    """The JSON object the Stage B prompt asks the model to return."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["YES", "NO"]
    reason: str
    domain: BDomain
    best_steps: list[int] = Field(max_length=5)
    moment_kinds: list[MomentKind]
    offline_rebuild: Offline


class StageBResult(BaseModel):
    trajectory_id: str
    source: str
    domain: Domain
    output: StageBOutput | None
    error: str | None = None


class CriterionScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=0, le=3)
    steps: list[int]
    excerpt: str = ""

    @field_validator("excerpt")
    @classmethod
    def excerpt_short(cls, value: str) -> str:
        if len(value) > MAX_EXCERPT_CHARS:
            raise ValueError(f"excerpt longer than {MAX_EXCERPT_CHARS} characters")
        return value

    @model_validator(mode="after")
    def evidence_for_high_scores(self) -> CriterionScore:
        if self.score >= 2 and (not self.steps or not self.excerpt.strip()):
            raise ValueError("scores of 2 or 3 need at least one step and an excerpt")
        return self


class C1Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: int
    moment_kind: MomentKind
    unsafe_precedent: bool
    conflicting_instruction: bool


class StageC1Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trajectory_id: str
    scores: dict[Criterion, CriterionScore]
    candidates: list[C1Candidate] = Field(max_length=3)
    honest_reporting_moment: bool
    authorization_boundary: bool

    @model_validator(mode="after")
    def all_criteria_scored(self) -> StageC1Output:
        missing = set(CRITERIA) - set(self.scores)
        if missing:
            raise ValueError(f"missing criteria: {sorted(missing)}")
        return self


class StageC1Result(BaseModel):
    trajectory_id: str
    source: str
    domain: Domain
    output: StageC1Output | None
    error: str | None = None


Phase = Literal[
    "investigating", "editing", "testing", "committing", "deploying", "reporting", "other"
]
Checkability = Literal["file_state", "command_log", "external_action", "process", "none"]
Agreement = Literal["high", "medium", "low"]


class C2Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: int
    phase: Phase
    moment_kind: MomentKind
    moment_description: str | None = None
    history_tokens: int = Field(ge=0)
    buttons: list[str]
    checkability: Checkability
    right_behavior_agreement: Agreement
    completable_without_shortcut: bool
    unsafe_precedent: bool
    conflicting_instruction: bool
    rebuild_dependencies: str
    evidence_steps: list[int]

    @model_validator(mode="after")
    def other_needs_description(self) -> C2Candidate:
        if self.moment_kind == "other":
            words = (self.moment_description or "").split()
            if not words or len(words) > 20:
                raise ValueError("moment_kind other needs a description of at most 20 words")
        return self


class StageC2Output(BaseModel):
    """The JSON object a Stage C2 deep read returns, one per trajectory."""

    model_config = ConfigDict(extra="forbid")

    trajectory_id: str
    scores: dict[Criterion, CriterionScore]
    candidates: list[C2Candidate] = Field(max_length=3)
    honest_reporting_moment: bool
    authorization_boundary: bool

    @model_validator(mode="after")
    def all_criteria_scored(self) -> StageC2Output:
        missing = set(CRITERIA) - set(self.scores)
        if missing:
            raise ValueError(f"missing criteria: {sorted(missing)}")
        return self


class StageC2Result(BaseModel):
    trajectory_id: str
    source: str
    domain: Domain
    output: StageC2Output


class C2Input(BaseModel):
    """One line of a C2 handoff shard."""

    trajectory_id: str
    source: str
    domain: Domain
    rendering: str
    c1_output: StageC1Output


class CostRecord(BaseModel):
    batch_id: str
    stage: str
    model: str
    requests: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    submitted_at: datetime
