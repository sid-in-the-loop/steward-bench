"""What decides the agent's next turn: a script for reference executions, or a model."""

from __future__ import annotations

import json
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from steward.execution.history import REPORT_TOOL
from steward.execution.record import Action

Message = dict[str, Any]


class Turn(BaseModel):
    """One agent turn: a tool call, a message to the principal, or the final report."""

    action: Action | None = None
    message: str | None = None
    report: str | None = None


class Policy(Protocol):
    def next_turn(self, messages: list[Message]) -> Turn: ...


class ScriptedPolicy:
    """Replays a reference execution: its actions in order, then its report.

    A scripted agent that is halted waits: it sends a short acknowledgement each turn until it is
    permitted to continue, unless `ignore_halts` is set. The action a halt interrupted is issued
    again once the agent proceeds.
    """

    def __init__(self, actions: list[Action], report: str, ignore_halts: bool = False) -> None:
        self.actions = list(actions)
        self.report = report
        self.ignore_halts = ignore_halts
        self.halted = False
        self.issued: Action | None = None

    def next_turn(self, messages: list[Message]) -> Turn:
        last = messages[-1] if messages else {}
        if last.get("steward_event") == "halt":
            self.halted = True
            if self.issued is not None:
                self.actions.insert(0, self.issued)
        if last.get("steward_event") == "permit":
            self.halted = False
        if self.halted and not self.ignore_halts:
            return Turn(message="Understood, I have stopped. Tell me when to continue.")
        if self.actions:
            self.issued = self.actions.pop(0)
            return Turn(action=self.issued)
        return Turn(report=self.report)


class ModelPolicy:
    """Asks a chat-completions model for the next turn.

    A call to the report tool ends the run, any other tool call is an action, and plain text is
    a message to the principal.
    """

    def __init__(
        self, client: httpx.Client, model: str, tools: list[Message], max_tokens: int = 4000
    ) -> None:
        self.client = client
        self.model = model
        self.tools = tools
        self.max_tokens = max_tokens

    @staticmethod
    def wire_messages(messages: list[Message]) -> list[Message]:
        """Session messages in chat-completions form: live actions become tool calls."""
        wire: list[Message] = []
        call_id = ""
        for index, message in enumerate(messages):
            if "action" in message:
                action = Action.model_validate(message["action"])
                call_id = f"live_{index}"
                call = {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": action.tool, "arguments": json.dumps(action.arguments)},
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
        return Turn(action=Action(tool=function["name"], arguments=arguments))
