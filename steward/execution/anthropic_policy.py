"""A model policy for Claude models on the Anthropic Messages API.

The session keeps its own record of every turn as text and actions. Claude's replies also
carry thinking blocks and tool-use ids that the API expects to see again unchanged, so this
policy keeps each reply's content blocks and sends those back in place of the session's
record of the same turn. The conversation the API sees is therefore only ever appended to,
which also lets the prompt cache serve everything before the newest turn.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol, cast

from steward.execution.history import REPORT_TOOL
from steward.execution.policy import Message, ModelPolicy, Turn, Usage
from steward.execution.record import Action

MAX_TOKENS = 16_000
# What the report tool is answered with, so that the turn after a report is well formed.
REPORT_RECEIVED = "Report received."
# The Messages API takes no conversation that ends with the model's own turn. When the agent
# spoke and nobody answered, this closes the conversation; other providers are sent nothing.
NO_REPLY = "(No one has replied.)"
# The same wait as other providers get on a rate limit, through the SDK's own retries.
MAX_RETRIES = 8


class MessagesClient(Protocol):
    """The part of the Anthropic client this policy uses."""

    @property
    def messages(self) -> Any: ...


def tool_use_id(raw: str) -> str:
    """An id in the form the API accepts, from a recorded call id of any shape."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", raw) or "call"


def claude_tools(tools: list[Message]) -> list[dict[str, Any]]:
    """Tool definitions in the Messages shape, from the chat-completions shape."""
    converted = []
    for tool in tools:
        function = tool["function"]
        schema = function.get("parameters") or {"type": "object", "properties": {}}
        converted.append(
            {
                "name": function["name"],
                "description": function.get("description") or "",
                "input_schema": schema,
            }
        )
    return converted


class AnthropicPolicy:
    """Asks a Claude model for the next turn. A call to the report tool ends the run, any
    other tool call is an action, and plain text is a message to the principal. Thinking is
    left at the model's default and its summary is kept as the turn's thought."""

    thought: str | None = None

    def __init__(self, client: MessagesClient, model: str, tools: list[Message]) -> None:
        self.client = client
        self.model = model
        self.tools = claude_tools(tools)
        self.usage = Usage()
        # The content blocks of each reply, by the position its turn takes in the session.
        self.replies: dict[int, list[Any]] = {}

    def _conversation(self, messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
        """The system text and the conversation in the Messages shape. A turn this policy
        produced is sent back as the blocks it came with; every tool use is followed by its
        result, and a report by a short acknowledgement."""
        system: list[str] = []
        turns: list[dict[str, Any]] = []
        owed: str | None = None

        def add(role: str, blocks: list[Any]) -> None:
            if turns and turns[-1]["role"] == role:
                turns[-1]["content"].extend(blocks)
            else:
                turns.append({"role": role, "content": list(blocks)})

        def settle() -> None:
            nonlocal owed
            if owed is not None:
                result = {"type": "tool_result", "tool_use_id": owed, "content": REPORT_RECEIVED}
                add("user", [result])
                owed = None

        wire = ModelPolicy.wire_messages(messages)
        for index, (message, entry) in enumerate(zip(messages, wire, strict=True)):
            role = message["role"]
            if role == "system":
                system.append(str(message.get("content") or ""))
                continue
            if role == "tool":
                call = owed or tool_use_id(str(entry.get("tool_call_id") or ""))
                owed = None
                content = str(message.get("content") or "") or "(no output)"
                add("user", [{"type": "tool_result", "tool_use_id": call, "content": content}])
                continue
            settle()
            if role == "assistant" and index in self.replies:
                blocks = self.replies[index]
                add("assistant", blocks)
                owed = next((b.id for b in blocks if b.type == "tool_use"), None)
                continue
            if role == "assistant":
                blocks = []
                text = str(entry.get("content") or "")
                if text:
                    blocks.append({"type": "text", "text": text})
                for call_entry in entry.get("tool_calls") or []:
                    function = call_entry["function"]
                    try:
                        arguments = json.loads(function.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    owed = tool_use_id(str(call_entry["id"]))
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": owed,
                            "name": function["name"],
                            "input": arguments if isinstance(arguments, dict) else {},
                        }
                    )
                add("assistant", blocks or [{"type": "text", "text": "(no text)"}])
                continue
            add("user", [{"type": "text", "text": str(message.get("content") or "") or "(empty)"}])
        settle()
        if turns and turns[-1]["role"] == "assistant":
            add("user", [{"type": "text", "text": NO_REPLY}])
        return "\n\n".join(part for part in system if part), turns

    def next_turn(self, messages: list[Message]) -> Turn:
        system, turns = self._conversation(messages)
        response = self.client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=turns,
            tools=self.tools,
            # One tool call a turn, as the session runs one action at a time.
            tool_choice={"type": "auto", "disable_parallel_tool_use": True},
            thinking={"type": "adaptive", "display": "summarized"},
            # The newest block is cached, so each request reads everything before it.
            cache_control={"type": "ephemeral"},
        )
        self._count(response.usage)
        blocks = list(response.content)
        self.replies[len(messages)] = blocks
        thoughts = [b.thinking for b in blocks if b.type == "thinking" and b.thinking]
        self.thought = "\n\n".join(thoughts) or None
        call = next((b for b in blocks if b.type == "tool_use"), None)
        text = "".join(b.text for b in blocks if b.type == "text")
        if call is None:
            if not text.strip():
                # A refusal or a reply with no text has nothing to send back as blocks.
                del self.replies[len(messages)]
            return Turn(message=text)
        arguments = cast(dict[str, Any], call.input) if isinstance(call.input, dict) else {}
        if call.name == REPORT_TOOL:
            return Turn(report=str(arguments.get("report", "")))
        return Turn(action=Action(tool=call.name, arguments=arguments))

    def _count(self, usage: Any) -> None:
        read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        written = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        self.usage.requests += 1
        self.usage.prompt_tokens += int(usage.input_tokens or 0) + read + written
        self.usage.cached_tokens += read
        self.usage.cache_write_tokens += written
        self.usage.completion_tokens += int(usage.output_tokens or 0)
