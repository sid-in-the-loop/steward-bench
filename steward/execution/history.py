"""Turn a recorded history into chat messages the tested model can continue from.

Tool calls keep the recorded tool name and arguments. Thinking was already dropped at ingest,
and the history ends on a user or tool turn, never on a partial assistant turn.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from steward.environment.shell import REPLAYED_TOOLS
from steward.schema import Step

Message = dict[str, Any]

PENDING_OUTPUT = "(output appears with the next command)"
REPORT_TOOL = "submit_report"
DEFAULT_TERMINAL_TOOL = "bash"


def terminal_tool(steps: list[Step]) -> tuple[str, str]:
    """The terminal tool the history used and the name of its command argument."""
    used = Counter(s.tool for s in steps if s.kind == "action" and s.tool in REPLAYED_TOOLS)
    if not used:
        return DEFAULT_TERMINAL_TOOL, "command"
    name = used.most_common(1)[0][0]
    keystrokes = any(
        "keystrokes" in (s.tool_input or {}) for s in steps if s.kind == "action" and s.tool == name
    )
    return name, "keystrokes" if keystrokes else "command"


def tool_definitions(steps: list[Step]) -> list[Message]:
    """The tools offered to the tested model: the history's terminal tool and a report tool."""
    name, argument = terminal_tool(steps)
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": "Run input in the terminal session and return the new output.",
                "parameters": {
                    "type": "object",
                    "properties": {argument: {"type": "string"}},
                    "required": [argument],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": REPORT_TOOL,
                "description": "Finish the task and report the outcome to the user.",
                "parameters": {
                    "type": "object",
                    "properties": {"report": {"type": "string"}},
                    "required": ["report"],
                },
            },
        },
    ]


def to_messages(steps: list[Step]) -> list[Message]:
    """Chat messages for the steps, with every tool call answered by a tool message."""
    messages: list[Message] = []
    open_calls: list[str] = []

    def close_calls(outputs: list[str]) -> None:
        # Terminal harnesses often return one output for several commands, so outputs are
        # matched to calls from the end.
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
