"""The principal: fixed safety-critical messages and replies to the agent's questions.

Halts, permissions, approvals and denials are fixed text in the specification, sent when their
trigger fires. A responder only phrases answers to questions the agent asks; every answer that
bears on safety comes from the specification, so scoring stays deterministic.
"""

from __future__ import annotations

import re
from typing import Protocol

from steward.filter.stage_a import STATE_CHANGING_COMMAND, WRITE_VERBS
from steward.scenario.spec import Intervention, Principal, ScenarioSpec

# An action that may change state: a state-changing shell command, a script being run, a file
# write or edit, or a service tool whose name is a write operation.
STATE_CHANGING = re.compile(
    STATE_CHANGING_COMMAND.pattern
    + r"|^((bash|sh|python3?|node)\s|\./\S)"
    + r"|^(Write|Edit|MultiEdit|NotebookEdit) \{"
    + rf"|^(?i:\S*-(\S*[_-])?({WRITE_VERBS})([_-]\S*)?) \{{"
)


class Responder(Protocol):
    def reply(self, question: str) -> str: ...


class ScriptedResponder:
    """Answers from the specification's fixed replies; needs no model."""

    def __init__(self, principal: Principal) -> None:
        self.principal = principal

    def reply(self, question: str) -> str:
        for scripted in self.principal.replies:
            if re.search(scripted.pattern, question, re.IGNORECASE):
                return scripted.reply
        return self.principal.default_reply


class InterventionSchedule:
    """Which interventions fire when, in specification order, each up to its repeat count."""

    def __init__(self, spec: ScenarioSpec) -> None:
        self.pending: list[Intervention] = list(spec.interventions)
        self.remaining = {i.id: i.repeat for i in spec.interventions}
        self.fired: list[str] = []
        self.halted = False
        self.turns_since_last = 0

    def _fire(self, intervention: Intervention) -> Intervention:
        self.remaining[intervention.id] -= 1
        if self.remaining[intervention.id] <= 0:
            self.pending.remove(intervention)
        self.fired.append(intervention.id)
        self.turns_since_last = 0
        if intervention.action == "halt":
            self.halted = True
        if intervention.action == "permit":
            self.halted = False
        return intervention

    def _next(self, kind: str) -> Intervention | None:
        return next((i for i in self.pending if i.trigger.kind == kind), None)

    def at_takeover(self) -> list[Intervention]:
        return [self._fire(i) for i in list(self.pending) if i.trigger.kind == "at_takeover"]

    def before_action(self, text: str) -> Intervention | None:
        for intervention in self.pending:
            trigger = intervention.trigger
            if trigger.kind != "before_action":
                continue
            if intervention.action == "halt" and self.halted:
                continue
            pattern = re.compile(trigger.pattern) if trigger.pattern else STATE_CHANGING
            if pattern.search(text):
                return self._fire(intervention)
        return None

    def after_turn(self) -> Intervention | None:
        self.turns_since_last += 1
        intervention = self._next("after_turns")
        if intervention is None or (intervention.action == "permit" and not self.halted):
            return None
        if self.turns_since_last >= (intervention.trigger.turns or 1):
            return self._fire(intervention)
        return None

    def on_completion(self) -> Intervention | None:
        intervention = self._next("on_completion")
        return self._fire(intervention) if intervention else None
