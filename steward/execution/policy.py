"""What decides the agent's next turn: a script for reference executions, or a model."""

from __future__ import annotations

import json
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from steward.execution.history import REPORT_TOOL

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


class ModelPolicy:
    """Asks a chat-completions model for the next turn.

    A call to the terminal tool is a command, a call to the report tool ends the run, and plain
    text is a message to the principal.
    """

    def __init__(
        self,
        client: httpx.Client,
        model: str,
        tools: list[Message],
        terminal_argument: str,
        max_tokens: int = 4000,
    ) -> None:
        self.client = client
        self.model = model
        self.tools = tools
        self.terminal_argument = terminal_argument
        self.max_tokens = max_tokens

    def wire_messages(self, messages: list[Message]) -> list[Message]:
        """Session messages in chat-completions form: commands become tool calls."""
        terminal = self.tools[0]["function"]["name"]
        wire: list[Message] = []
        call_id = ""
        for index, message in enumerate(messages):
            if "command" in message:
                call_id = f"live_{index}"
                arguments = json.dumps({self.terminal_argument: message["command"]})
                call = {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": terminal, "arguments": arguments},
                }
                wire.append({"role": "assistant", "content": "", "tool_calls": [call]})
            elif message["role"] == "tool" and "tool_call_id" not in message:
                wire.append(
                    {"role": "tool", "tool_call_id": call_id, "content": message["content"]}
                )
            else:
                wire.append({k: v for k, v in message.items() if k != "steward_event"})
        return wire

    def next_turn(self, messages: list[Message]) -> Turn:
        response = self.client.post(
            "/chat/completions",
            json={
                "model": self.model,
                "messages": self.wire_messages(messages),
                "tools": self.tools,
                "max_tokens": self.max_tokens,
            },
        )
        response.raise_for_status()
        reply = response.json()["choices"][0]["message"]
        calls = reply.get("tool_calls") or []
        if not calls:
            return Turn(message=reply.get("content") or "")
        function = calls[0]["function"]
        arguments = json.loads(function.get("arguments") or "{}")
        if function["name"] == REPORT_TOOL:
            return Turn(report=str(arguments.get("report", "")))
        return Turn(command=str(arguments.get(self.terminal_argument, "")))
