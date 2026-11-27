"""Turn a recorded history into chat messages the tested model can continue from.

Tool calls keep the recorded tool name and arguments. Thinking was already dropped at ingest,
and the history ends on a user or tool turn, never on a partial assistant turn.
"""

from __future__ import annotations

import json
import math
from typing import Any

from steward.schema import Step

Message = dict[str, Any]

PENDING_OUTPUT = "(output appears with the next command)"
REPORT_TOOL = "submit_report"
DEFAULT_TERMINAL_TOOL = "bash"
CHARS_PER_TOKEN = 2.2


def function_tool(name: str, description: str, parameters: Message) -> Message:
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }


def tool_definitions(steps: list[Step]) -> list[Message]:
    """The tools offered to the tested model: every tool the history used, most used first,
    with the argument names it was called with, and a tool for the final report."""
    arguments: dict[str, list[set[str]]] = {}
    for step in steps:
        if step.kind == "action" and step.tool and step.tool != REPORT_TOOL:
            arguments.setdefault(step.tool, []).append(set(step.tool_input or {}))
    definitions = [
        function_tool(
            name,
            f"The {name} tool, as used earlier in this session.",
            {
                "type": "object",
                "properties": {key: {} for key in sorted(set().union(*calls))},
                "required": sorted(set.intersection(*calls)),
            },
        )
        for name, calls in sorted(arguments.items(), key=lambda item: (-len(item[1]), item[0]))
    ]
    if not definitions:
        definitions.append(
            function_tool(
                DEFAULT_TERMINAL_TOOL,
                "Run a command in the terminal and return its output.",
                {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            )
        )
    report = function_tool(
        REPORT_TOOL,
        "Finish the task and report the outcome to the user.",
        {"type": "object", "properties": {"report": {"type": "string"}}, "required": ["report"]},
    )
    return [*definitions, report]


def to_messages(steps: list[Step]) -> list[Message]:
    """Chat messages for the steps, with every tool call answered by a tool message."""
    messages: list[Message] = []
    open_calls: list[str] = []

    def close_calls(outputs: list[str]) -> None:
        # Terminal harnesses often return one output for several commands, so outputs are
        # matched to calls from the end.
        # A result with no call before it (a planted step can separate the two) is dropped:
        # a tool message without its call is not a valid history.
        if not open_calls:
            return
        padded = [PENDING_OUTPUT] * (len(open_calls) - len(outputs)) + outputs[-len(open_calls) :]
        for call_id, output in zip(open_calls, padded, strict=True):
            messages.append({"role": "tool", "tool_call_id": call_id, "content": output})
        open_calls.clear()

    pending_outputs: list[str] = []
    for step in steps:
        if step.kind == "result":
            pending_outputs.append(step.content)
            continue
        if open_calls and step.kind != "action":
            close_calls(pending_outputs)
            pending_outputs = []
        if step.kind in ("user", "system"):
            messages.append({"role": step.kind, "content": step.content})
        elif step.kind == "agent_text":
            messages.append({"role": "assistant", "content": step.content})
        else:
            if pending_outputs:
                close_calls(pending_outputs)
                pending_outputs = []
            call_id = f"call_{step.index}"
            call = {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": step.tool or DEFAULT_TERMINAL_TOOL,
                    "arguments": json.dumps(step.tool_input or {"command": step.content}),
                },
            }
            last = messages[-1] if messages else None
            if last and last["role"] == "assistant" and (open_calls or "tool_calls" not in last):
                last.setdefault("tool_calls", []).append(call)
            else:
                messages.append({"role": "assistant", "content": "", "tool_calls": [call]})
            open_calls.append(call_id)
    if open_calls:
        close_calls(pending_outputs)
    return messages


def estimate_tokens(text: str) -> int:
    """Conservative token estimate for code-heavy agent transcripts."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def message_tokens(message: Message) -> int:
    return estimate_tokens(json.dumps(message, ensure_ascii=False))


def fit_history(messages: list[Message], budget_tokens: int) -> tuple[list[Message], bool]:
    """Keep the system messages, the first user message and the most recent turns that fit.

    A turn is a user message, or an assistant message with the tool results that answer it, so
    no tool result is left without its call.
    """
    if sum(message_tokens(m) for m in messages) <= budget_tokens:
        return messages, False
    first_user = next((i for i, m in enumerate(messages) if m["role"] == "user"), None)
    head = [m for i, m in enumerate(messages) if m["role"] == "system" or i == first_user]
    turns: list[list[Message]] = []
    for index, message in enumerate(messages):
        if message["role"] == "system" or index == first_user:
            continue
        if message["role"] == "tool" and turns:
            turns[-1].append(message)
        else:
            turns.append([message])
    remaining = budget_tokens - sum(message_tokens(m) for m in head)
    kept: list[list[Message]] = []
    for turn in reversed(turns):
        cost = sum(message_tokens(m) for m in turn)
        if cost > remaining:
            break
        kept.insert(0, turn)
        remaining -= cost
    return [*head, *(m for turn in kept for m in turn)], True
