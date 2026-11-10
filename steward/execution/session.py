"""The agent loop for one scenario, independent of where commands run."""

from __future__ import annotations

from typing import Protocol

from steward.execution.policy import Message, Policy
from steward.execution.record import CommandEvent, ExecutionRecord, MessageEvent, Sender
from steward.principal.principal import InterventionSchedule, Responder
from steward.scenario.spec import Intervention, ScenarioSpec

INTERRUPTED = "[Request interrupted by user]"


class Shell(Protocol):
    async def run(self, command: str) -> str: ...

    async def succeeds(self, command: str) -> bool: ...


class Session:
    def __init__(
        self,
        spec: ScenarioSpec,
        shell: Shell,
        policy: Policy,
        responder: Responder,
        history: list[Message],
        max_turns: int = 60,
    ) -> None:
        self.spec = spec
        self.shell = shell
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
            await self.shell.run(intervention.command)
            return
        assert intervention.text is not None
        self._say(intervention.sender, intervention.text, event=intervention.action)

    async def _run_command(self, command: str) -> None:
        halted = self.schedule.halted
        intervention = self.schedule.before_command(command)
        if intervention and intervention.action == "halt":
            self.record.commands.append(
                CommandEvent(turn=self.record.turns, command=command, executed=False, halted=halted)
            )
            self.messages.append({"role": "assistant", "content": "", "command": command})
            self.messages.append({"role": "tool", "content": INTERRUPTED})
            await self._apply(intervention)
            return
        if intervention:
            await self._apply(intervention)
        output = await self.shell.run(command)
        self.record.commands.append(
            CommandEvent(
                turn=self.record.turns, command=command, executed=True, halted=halted, output=output
            )
        )
        self.messages.append({"role": "assistant", "content": "", "command": command})
        self.messages.append({"role": "tool", "content": output})

    async def run(self) -> ExecutionRecord:
        for intervention in self.schedule.at_takeover():
            await self._apply(intervention)
        while self.record.turns < self.max_turns:
            self.record.turns += 1
            turn = self.policy.next_turn(self.messages)
            if turn.command is not None:
                await self._run_command(turn.command)
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
                self.record.probes[check.id] = await self.shell.succeeds(check.command)
        return self.record
