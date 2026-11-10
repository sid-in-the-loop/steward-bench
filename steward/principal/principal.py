"""The principal: fixed safety-critical messages and replies to the agent's questions.

Halts, permissions, approvals and denials are fixed text in the specification, sent when their
trigger fires. A responder only phrases answers to questions the agent asks; every answer that
bears on safety comes from the specification, so scoring stays deterministic.
"""

from __future__ import annotations

import re
from typing import Protocol

from steward.scenario.spec import Intervention, Principal, ScenarioSpec


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
    """Which interventions fire when. Each fires at most once, in specification order."""

    def __init__(self, spec: ScenarioSpec) -> None:
        self.pending: list[Intervention] = list(spec.interventions)
        self.fired: list[str] = []
        self.halted = False
        self.turns_since_last = 0

    def _fire(self, intervention: Intervention) -> Intervention:
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

    def before_command(self, command: str) -> Intervention | None:
        for intervention in self.pending:
            trigger = intervention.trigger
            if trigger.kind == "before_command" and re.search(trigger.pattern or "", command):
                return self._fire(intervention)
        return None

    def after_turn(self) -> Intervention | None:
        self.turns_since_last += 1
        intervention = self._next("after_turns")
        if intervention and self.turns_since_last >= (intervention.trigger.turns or 1):
            return self._fire(intervention)
        return None

    def on_completion(self) -> Intervention | None:
        intervention = self._next("on_completion")
        return self._fire(intervention) if intervention else None
