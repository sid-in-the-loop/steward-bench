"""A recorded agent session: its steps and where it came from."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

Domain = Literal["swe", "terminal_devops", "workplace", "other"]
StepKind = Literal["user", "agent_text", "action", "result", "system"]


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
