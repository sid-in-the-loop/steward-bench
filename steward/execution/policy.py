"""What decides the agent's next turn: a script for reference executions, or a model."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
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


class RunSettings(BaseModel):
    """What every model is run with. The takeover is silent: the model gets the history and no
    statement that another agent wrote it. Reasoning is left at each model's default."""

    temperature: float = 0.0
    max_tokens_per_turn: int = 4000
    reasoning: str = "model default"
    takeover_framing: str = "silent"
    attempts_per_scenario: int = 1


class Usage(BaseModel):
    """Token counts as the provider reported them, summed over a run's requests."""

    requests: int = 0
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    providers: list[str] = []

    def add(self, body: dict[str, Any]) -> None:
        usage = body.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        self.requests += 1
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.cached_tokens += int(details.get("cached_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)
        provider = body.get("provider")
        if isinstance(provider, str) and provider not in self.providers:
            self.providers.append(provider)


class RequestFailedError(RuntimeError):
    """A request that still failed after every retry."""


RETRY_ATTEMPTS = 5
RETRY_BASE_SECONDS = 2.0


def post_with_retry(
    client: httpx.Client, body: dict[str, Any], sleep: Callable[[float], None] = time.sleep
) -> dict[str, Any]:
    """POST a chat completion, retrying rate limits, server errors and transport errors with
    exponential backoff. An error object in a 200 reply is retried as a server error."""
    problem = ""
    for attempt in range(RETRY_ATTEMPTS):
        if attempt:
            sleep(RETRY_BASE_SECONDS * 2 ** (attempt - 1))
        try:
            response = client.post("/chat/completions", json=body)
        except httpx.TransportError as error:
            problem = f"{type(error).__name__}: {error}"
            continue
        if response.status_code == 429 or response.status_code >= 500:
            problem = f"HTTP {response.status_code}: {response.text[:200]}"
            continue
        response.raise_for_status()
        reply: dict[str, Any] = response.json()
        if "choices" not in reply:
            problem = f"no choices in reply: {str(reply.get('error'))[:200]}"
            continue
        return reply
    raise RequestFailedError(f"gave up after {RETRY_ATTEMPTS} attempts: {problem}")


class ModelPolicy:
    """Asks a chat-completions model for the next turn.

    A call to the report tool ends the run, any other tool call is an action, and plain text is
    a message to the principal. The request starts with the tools and the inherited history,
    which do not change between turns, so a provider's prefix cache can serve them.
    """

    def __init__(
        self,
        client: httpx.Client,
        model: str,
        tools: list[Message],
        settings: RunSettings | None = None,
        provider: str | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.tools = tools
        self.settings = settings or RunSettings()
        self.provider = provider
        self.usage = Usage()

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

    def request_body(self, messages: list[Message]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "tools": self.tools,
            "messages": self.wire_messages(messages),
            "max_tokens": self.settings.max_tokens_per_turn,
            "temperature": self.settings.temperature,
        }
        if self.provider:
            # One provider only, so every run is served at the same precision.
            body["provider"] = {"order": [self.provider], "allow_fallbacks": False}
        return body

    def next_turn(self, messages: list[Message]) -> Turn:
        reply_body = post_with_retry(self.client, self.request_body(messages))
        self.usage.add(reply_body)
        reply = reply_body["choices"][0]["message"]
        calls = reply.get("tool_calls") or []
        if not calls:
            return Turn(message=reply.get("content") or "")
        function = calls[0]["function"]
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        if function["name"] == REPORT_TOOL:
            return Turn(report=str(arguments.get("report", "")))
        return Turn(action=Action(tool=function["name"], arguments=arguments))
