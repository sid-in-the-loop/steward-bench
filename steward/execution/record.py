"""What happened in one run of a scenario: every action, every message and the final report."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel

Sender = Literal["agent", "principal", "third_party"]
Ending = Literal["finished", "turn_limit"]

TERMINAL_ARGUMENTS = ("command", "keystrokes", "cmd")


class Action(BaseModel):
    """One tool call by the agent."""

    tool: str
    arguments: dict[str, Any] = {}

    @property
    def text(self) -> str:
        """One line that checks and triggers match against: the command of a terminal call,
        otherwise the tool name and its arguments."""
        for name in TERMINAL_ARGUMENTS:
            value = self.arguments.get(name)
            if isinstance(value, str):
                return value.rstrip("\n")
        return f"{self.tool} {json.dumps(self.arguments, sort_keys=True, ensure_ascii=False)}"


class ActionEvent(BaseModel):
    """One action the agent attempted.

    `planted` is the position of the planted result that answered it, if one did, and
    `changed` says whether that answer showed the world after its change or before it.
    """

    turn: int
    tool: str
    text: str
    executed: bool
    halted: bool
    output: str = ""
    planted: int | None = None
    changed: bool = False


class MessageEvent(BaseModel):
    turn: int
    sender: Sender
    text: str


class ExecutionRecord(BaseModel):
    scenario_id: str
    actions: list[ActionEvent] = []
    messages: list[MessageEvent] = []
    interventions_fired: list[str] = []
    probes: dict[str, bool] = {}
    final_report: str | None = None
    turns: int = 0
    ending: Ending = "finished"
