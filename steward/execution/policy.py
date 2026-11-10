"""What decides the agent's next turn: a script for reference executions, or a model."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel

Message = dict[str, Any]


class Turn(BaseModel):
    """One agent turn: a command to run, a message to the principal, or the final report."""

    command: str | None = None
    message: str | None = None
    report: str | None = None


class Policy(Protocol):
    def next_turn(self, messages: list[Message]) -> Turn: ...


class ScriptedPolicy:
    """Replays a reference execution: its commands in order, then its report.

    A scripted agent that is halted waits: it sends a short acknowledgement each turn until it is
    permitted to continue, unless `ignore_halts` is set. The command a halt interrupted is issued
    again once the agent proceeds.
    """

    def __init__(self, commands: list[str], report: str, ignore_halts: bool = False) -> None:
        self.commands = list(commands)
        self.report = report
        self.ignore_halts = ignore_halts
        self.halted = False
        self.issued: str | None = None

    def next_turn(self, messages: list[Message]) -> Turn:
        last = messages[-1] if messages else {}
        if last.get("steward_event") == "halt":
            self.halted = True
            if self.issued is not None:
                self.commands.insert(0, self.issued)
        if last.get("steward_event") == "permit":
            self.halted = False
        if self.halted and not self.ignore_halts:
            return Turn(message="Understood, I have stopped. Tell me when to continue.")
        if self.commands:
            self.issued = self.commands.pop(0)
            return Turn(command=self.issued)
        return Turn(report=self.report)
