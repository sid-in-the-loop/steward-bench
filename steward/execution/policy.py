"""What decides the agent's next turn: a script for reference executions, or a model."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any, Literal, Protocol

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
    # Set when the model gave no usable reply even when asked again: why.
    error: str | None = None


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


Dialect = Literal["openrouter", "openai"]
# How many times the output budget grows for the one retry of an empty or cut-off reply.
RETRY_BUDGET_FACTOR = 2
# Sent when supported; a model that rejects one is run without it.
OPTIONAL_PARAMETERS = ("temperature", "prompt_cache_retention", "prompt_cache_key")


# The parameters each model has rejected in this process, so that later sessions of the
# model do not send them and pay a refused request again.
_known_drops: dict[str, list[str]] = {}
_drops_lock = threading.Lock()


def known_drops(model: str) -> list[str]:
    with _drops_lock:
        return list(_known_drops.get(model, []))


def remember_drops(model: str, names: list[str]) -> None:
    with _drops_lock:
        known = _known_drops.setdefault(model, [])
        known += [name for name in names if name not in known]


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
    # Input tokens written to the cache, where the provider bills those at their own rate.
    cache_write_tokens: int = 0
    completion_tokens: int = 0
    # The prompt of the largest request, which decides a long-context price.
    largest_prompt_tokens: int = 0
    providers: list[str] = []

    def add(self, body: dict[str, Any]) -> None:
        """Add one reply's usage. Chat completions report prompt and completion tokens; the
        Responses API reports the same counts as input and output tokens. Output tokens
        include reasoning tokens, which are billed as output."""
        usage = body.get("usage") or {}
        details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
        prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        self.requests += 1
        self.prompt_tokens += prompt
        self.largest_prompt_tokens = max(self.largest_prompt_tokens, prompt)
        self.cached_tokens += int(details.get("cached_tokens") or 0)
        self.cache_write_tokens += int(details.get("cache_write_tokens") or 0)
        self.completion_tokens += int(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )
        provider = body.get("provider")
        if isinstance(provider, str) and provider not in self.providers:
            self.providers.append(provider)

    def merge(self, other: Usage) -> None:
        self.requests += other.requests
        self.prompt_tokens += other.prompt_tokens
        self.cached_tokens += other.cached_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.completion_tokens += other.completion_tokens
        self.largest_prompt_tokens = max(self.largest_prompt_tokens, other.largest_prompt_tokens)


class RequestFailedError(RuntimeError):
    """A request that still failed after every retry."""


RETRY_ATTEMPTS = 5
RETRY_BASE_SECONDS = 2.0
# A rate limit clears within a minute, and giving up on one discards a whole session, so rate
# limits are waited out far longer than other failures.
RATE_LIMIT_ATTEMPTS = 15
RETRY_MAX_SECONDS = 60.0


def retry_after_seconds(response: httpx.Response) -> float:
    """The wait the server asks for, from `retry-after-ms` or `retry-after`; 0 if none."""
    for header, scale in (("retry-after-ms", 0.001), ("retry-after", 1.0)):
        value = response.headers.get(header)
        if value is not None:
            try:
                return float(value) * scale
            except ValueError:
                continue
    return 0.0


CHAT_COMPLETIONS = "/chat/completions"
RESPONSES = "/responses"
# The field a successful reply carries on each endpoint.
REPLY_FIELD = {CHAT_COMPLETIONS: "choices", RESPONSES: "output"}


def post_with_retry(
    client: httpx.Client,
    body: dict[str, Any],
    sleep: Callable[[float], None] = time.sleep,
    path: str = CHAT_COMPLETIONS,
) -> dict[str, Any]:
    """POST a model request, retrying rate limits, server errors and transport errors with
    exponential backoff. A rate limit waits at least as long as the server asks and is retried
    up to RATE_LIMIT_ATTEMPTS times; other failures up to RETRY_ATTEMPTS times. A 200 reply
    without its result field is retried as a server error."""
    problem = ""
    failures = rate_limits = 0
    asked = 0.0
    while failures < RETRY_ATTEMPTS and rate_limits < RATE_LIMIT_ATTEMPTS:
        tries = failures + rate_limits
        if tries:
            backoff = RETRY_BASE_SECONDS * 2 ** (tries - 1)
            sleep(min(RETRY_MAX_SECONDS, max(backoff, asked)))
        asked = 0.0
        try:
            response = client.post(path, json=body)
        except httpx.TransportError as error:
            problem = f"{type(error).__name__}: {error}"
            failures += 1
            continue
        if response.status_code == 429:
            problem = f"HTTP 429: {response.text[:200]}"
            asked = retry_after_seconds(response)
            rate_limits += 1
            continue
        if response.status_code >= 500:
            problem = f"HTTP {response.status_code}: {response.text[:200]}"
            failures += 1
            continue
        response.raise_for_status()
        try:
            reply: dict[str, Any] = response.json()
        except ValueError:
            problem = f"reply is not JSON: {response.text[:200]}"
            failures += 1
            continue
        if REPLY_FIELD[path] not in reply:
            problem = f"no {REPLY_FIELD[path]} in reply: {str(reply.get('error'))[:200]}"
            failures += 1
            continue
        return reply
    raise RequestFailedError(f"gave up after {failures + rate_limits} attempts: {problem}")


class ModelPolicy:
    """Asks a model for the next turn.

    A call to the report tool ends the run, any other tool call is an action, and plain text is
    a message to the principal. The request starts with the tools and the inherited history,
    which do not change between turns, so a provider's prefix cache can serve them.

    The "openrouter" dialect speaks chat completions. The "openai" dialect speaks OpenAI's
    Responses API: OpenAI's reasoning models accept function tools on chat completions only
    with reasoning switched off, and reasoning stays at the model's default.
    """

    def __init__(
        self,
        client: httpx.Client,
        model: str,
        tools: list[Message],
        settings: RunSettings | None = None,
        provider: str | None = None,
        dialect: Dialect = "openrouter",
        cache_key: str | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.tools = tools
        self.settings = settings or RunSettings()
        self.provider = provider
        self.dialect = dialect
        self.cache_key = cache_key
        self.dropped: list[str] = known_drops(model)
        self.usage = Usage()
        # The output budget of the next request, when it is not the run's own.
        self.output_budget: int | None = None

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

    @classmethod
    def response_items(cls, messages: list[Message]) -> list[Message]:
        """Session messages as Responses input items: text turns keep their role, a tool call
        is a function_call item and its result a function_call_output with the same call_id."""
        items: list[Message] = []
        for message in cls.wire_messages(messages):
            role, content = message["role"], message.get("content") or ""
            if role == "tool":
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message["tool_call_id"],
                        "output": str(content),
                    }
                )
                continue
            if content or not message.get("tool_calls"):
                items.append({"role": role, "content": str(content)})
            for call in message.get("tool_calls") or []:
                function = call["function"]
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call["id"],
                        "name": function["name"],
                        "arguments": function.get("arguments") or "{}",
                    }
                )
        return items

    def response_tools(self) -> list[Message]:
        """The tool definitions in the Responses shape. Schemas are not strict: recorded tools
        are offered with the argument names they were called with, not a full schema."""
        return [{"type": "function", "strict": False, **tool["function"]} for tool in self.tools]

    def request_body(self, messages: list[Message]) -> dict[str, Any]:
        body: dict[str, Any]
        budget = self.output_budget or self.settings.max_tokens_per_turn
        if self.dialect == "openai":
            body = {
                "model": self.model,
                "tools": self.response_tools(),
                "input": self.response_items(messages),
                "max_output_tokens": budget,
                "temperature": self.settings.temperature,
                # Session content is not kept on the provider's side.
                "store": False,
            }
            if self.cache_key:
                # Requests with the same key go to the same cache, so every turn and every
                # condition of a session reuses the inherited history.
                body["prompt_cache_key"] = self.cache_key
                body["prompt_cache_retention"] = "24h"
        else:
            body = {
                "model": self.model,
                "tools": self.tools,
                "messages": self.wire_messages(messages),
                "max_tokens": budget,
                "temperature": self.settings.temperature,
            }
            if self.provider:
                # One provider only, so every run is served at the same precision.
                body["provider"] = {"order": [self.provider], "allow_fallbacks": False}
        return {k: v for k, v in body.items() if k not in self.dropped}

    def post(self, messages: list[Message]) -> dict[str, Any]:
        """Send the request. A model that rejects an optional parameter (some accept no
        temperature, or no cache retention) is asked again without it, and the parameter is
        recorded as dropped."""
        path = RESPONSES if self.dialect == "openai" else CHAT_COMPLETIONS
        while True:
            try:
                return post_with_retry(self.client, self.request_body(messages), path=path)
            except httpx.HTTPStatusError as error:
                text = error.response.text
                rejected = next(
                    (p for p in OPTIONAL_PARAMETERS if p in text and p not in self.dropped), None
                )
                if error.response.status_code != 400 or rejected is None:
                    raise
                self.dropped.append(rejected)
                remember_drops(self.model, [rejected])

    @staticmethod
    def first_call(reply_body: dict[str, Any]) -> tuple[str, str] | None:
        """The name and JSON arguments of the reply's first tool call, on either endpoint."""
        if "output" in reply_body:
            for item in reply_body["output"]:
                if item.get("type") == "function_call":
                    return str(item["name"]), str(item.get("arguments") or "{}")
            return None
        calls = reply_body["choices"][0]["message"].get("tool_calls") or []
        if not calls:
            return None
        function = calls[0]["function"]
        return str(function["name"]), str(function.get("arguments") or "{}")

    @staticmethod
    def reply_text(reply_body: dict[str, Any]) -> str:
        """The reply's plain text, on either endpoint."""
        if "output" not in reply_body:
            return str(reply_body["choices"][0]["message"].get("content") or "")
        parts = [
            part.get("text") or ""
            for item in reply_body["output"]
            if item.get("type") == "message"
            for part in item.get("content") or []
            if part.get("type") == "output_text"
        ]
        return "".join(parts)

    @classmethod
    def unusable(cls, reply_body: dict[str, Any]) -> str | None:
        """Why a reply with no tool call cannot be taken as the agent's turn: it has no text,
        or it was cut off at the output budget. None for a usable reply."""
        if cls.first_call(reply_body) is not None:
            return None
        if reply_body.get("status") == "incomplete":
            details = reply_body.get("incomplete_details") or {}
            return f"incomplete: {details.get('reason') or 'no reason given'}"
        choices = reply_body.get("choices") or [{}]
        if choices[0].get("finish_reason") == "length":
            return "cut off at the output budget"
        if not cls.reply_text(reply_body).strip():
            return "no text and no tool call"
        return None

    def next_turn(self, messages: list[Message]) -> Turn:
        reply_body = self.post(messages)
        self.usage.add(reply_body)
        if self.unusable(reply_body) is not None:
            # Asked once more with room to finish; a reply still unusable ends the shift.
            self.output_budget = self.settings.max_tokens_per_turn * RETRY_BUDGET_FACTOR
            try:
                reply_body = self.post(messages)
            finally:
                self.output_budget = None
            self.usage.add(reply_body)
            problem = self.unusable(reply_body)
            if problem is not None:
                return Turn(error=problem)
        call = self.first_call(reply_body)
        if call is None:
            return Turn(message=self.reply_text(reply_body))
        name, raw_arguments = call
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError:
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        if name == REPORT_TOOL:
            return Turn(report=str(arguments.get("report", "")))
        return Turn(action=Action(tool=name, arguments=arguments))
