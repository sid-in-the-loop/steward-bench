"""What happened in one run of a scenario: every command, every message and the final report."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Sender = Literal["agent", "principal", "third_party"]
Ending = Literal["finished", "turn_limit"]


class CommandEvent(BaseModel):
    turn: int
    command: str
    executed: bool
    halted: bool
    output: str = ""


class MessageEvent(BaseModel):
    turn: int
    sender: Sender
    text: str


class ExecutionRecord(BaseModel):
    scenario_id: str
    commands: list[CommandEvent] = []
    messages: list[MessageEvent] = []
    interventions_fired: list[str] = []
    probes: dict[str, bool] = {}
    final_report: str | None = None
    turns: int = 0
    ending: Ending = "finished"
