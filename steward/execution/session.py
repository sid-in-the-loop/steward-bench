"""The agent loop for one scenario, independent of where commands run."""

from __future__ import annotations

from typing import Protocol

from steward.execution.policy import Message, Policy
from steward.execution.record import (
    Action,
    ActionEvent,
    ExecutionRecord,
    MessageEvent,
    Sender,
)
from steward.principal.principal import InterventionSchedule, Responder
from steward.scenario.spec import Intervention, ScenarioSpec

INTERRUPTED = "[Request interrupted by user]"


class Executor(Protocol):
    """Where actions take effect: a container, or an emulator of recorded tool results."""

    async def execute(self, action: Action) -> str: ...

    async def run(self, command: str) -> str: ...

    async def succeeds(self, command: str) -> bool: ...


class Session:
    def __init__(
        self,
        spec: ScenarioSpec,
        executor: Executor,
        policy: Policy,
        responder: Responder,
        history: list[Message],
        max_turns: int = 60,
    ) -> None:
        self.spec = spec
        self.executor = executor
        self.policy = policy
        self.responder = responder
        self.messages = list(history)
        self.max_turns = max_turns
        self.schedule = InterventionSchedule(spec)
        self.record = ExecutionRecord(scenario_id=spec.scenario_id)

    def _say(self, sender: Sender, text: str, event: str | None = None) -> None:
        self.record.messages.append(MessageEvent(turn=self.record.turns, sender=sender, text=text))
        role = "assistant" if sender == "agent" else "user"
        message: Message = {"role": role, "content": text}
        if event:
            message["steward_event"] = event
        self.messages.append(message)

    async def _apply(self, intervention: Intervention) -> None:
        if intervention.action == "environment_command":
            assert intervention.command is not None
            await self.executor.run(intervention.command)
            return
        assert intervention.text is not None
        self._say(intervention.sender, intervention.text, event=intervention.action)

    async def _act(self, action: Action) -> None:
        halted = self.schedule.halted
        intervention = self.schedule.before_action(action.text)
        event = ActionEvent(
            turn=self.record.turns, tool=action.tool, text=action.text, executed=True, halted=halted
        )
        self.messages.append({"role": "assistant", "content": "", "action": action.model_dump()})
        if intervention and intervention.action == "halt":
            event.executed = False
            self.record.actions.append(event)
            self.messages.append({"role": "tool", "content": INTERRUPTED})
            await self._apply(intervention)
            return
        event.output = await self.executor.execute(action)
        served = getattr(self.executor, "last_served", None)
        if served is not None:
            event.planted, event.changed = served
        self.record.actions.append(event)
        self.messages.append({"role": "tool", "content": event.output})
        if intervention:
            await self._apply(intervention)

    async def run(self) -> ExecutionRecord:
        for intervention in self.schedule.at_takeover():
            await self._apply(intervention)
        while self.record.turns < self.max_turns:
            self.record.turns += 1
            turn = self.policy.next_turn(self.messages)
            if turn.action is not None:
                await self._act(turn.action)
            elif turn.report is not None:
                closing = self.schedule.on_completion()
                self._say("agent", turn.report)
                if closing is None:
                    self.record.final_report = turn.report
                    break
                await self._apply(closing)
                continue
            else:
                self._say("agent", turn.message or "")
                if self.spec.principal.simulator != "none" and not self.schedule.halted:
                    self._say("principal", self.responder.reply(turn.message or ""))
            due = self.schedule.after_turn()
            if due:
                await self._apply(due)
        else:
            self.record.ending = "turn_limit"
        self.record.interventions_fired = list(self.schedule.fired)
        for check in [
            *self.spec.task_checks,
            *self.spec.safety_checks,
            *self.spec.disqualifying_violations,
        ]:
            if check.kind == "environment_command":
                assert check.command is not None
                self.record.probes[check.id] = await self.executor.succeeds(check.command)
        return self.record
