"""What happened in one run of a scenario: every action, every message and the final report."""

from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import BaseModel

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


class Executor(Protocol):
    """Where actions take effect: a container, or an emulator of recorded tool results."""

    async def execute(self, action: Action) -> str: ...

    async def run(self, command: str) -> str: ...

    async def succeeds(self, command: str) -> bool: ...
