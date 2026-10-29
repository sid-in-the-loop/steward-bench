"""Steps from the chat messages of one HAL agent call (OpenAI format).

Tool-calling scaffolds (TAU-bench FewShot) use `tool_calls` and `tool` messages. The HAL
Generalist scaffold is a smolagents CodeAgent: actions are Python blocks in the assistant text
(ending in `<end_code>` and echoed after `Calling tools:`), and tool output comes back as user
messages starting with `Observation:`, `Call id:` or `Error:`. Reasoning fields
(`reasoning_content`, `thinking_blocks`) and `<think>` spans are dropped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from steward.schema import Step
from steward.sources.chat_messages import strip_thinking
from steward.sources.transcripts import StepBuilder, _block_text, _parse_arguments, action_content

CODE_TOOL = "python_interpreter"
CODE_BLOCK = re.compile(r"(?:Code:\s*)?```(?:py|python)?\s*\n(.*?)```", re.S)
CODE_MARKERS = ("<end_code>", "Calling tools:")
CALLING_TOOLS = re.compile(r"\n?Calling tools:.*\Z", re.S)
RESULT_PREFIXES = ("Observation:", "Call id:", "Error:")
HARNESS_PREFIXES = ("Now proceed and carry out this plan", "Now go on and")


def _code_action(builder: StepBuilder, text: str) -> None:
    text = CALLING_TOOLS.sub("", text)
    blocks = list(CODE_BLOCK.finditer(text))
    if not blocks:
        builder.add("agent_text", text)
        return
    last = blocks[-1]
    builder.add("agent_text", (text[: last.start()] + text[last.end() :]).replace("<end_code>", ""))
    code = last.group(1).strip()
    builder.add("action", code, tool=CODE_TOOL, tool_input={"code": code})


def _assistant(builder: StepBuilder, message: dict[str, Any]) -> None:
    text = strip_thinking(_block_text(message.get("content")))
    calls = message.get("tool_calls") or []
    if not calls and any(marker in text for marker in CODE_MARKERS):
        _code_action(builder, text)
        return
    builder.add("agent_text", text)
    for call in calls:
        if not isinstance(call, dict):
            continue
        function = call.get("function") or {}
        name = str(function.get("name") or "tool")
        tool_input = _parse_arguments(function.get("arguments"))
        builder.add("action", action_content(name, tool_input), tool=name, tool_input=tool_input)


def _user(builder: StepBuilder, text: str, code_agent: bool) -> None:
    stripped = text.lstrip()
    if code_agent and stripped.startswith(RESULT_PREFIXES):
        builder.add("result", text)
    elif code_agent and stripped.startswith(HARNESS_PREFIXES):
        builder.add("system", text)
    else:
        builder.user_text(text, None)


def parse_hal_messages(messages: Iterable[dict[str, Any]], code_agent: bool) -> list[Step]:
    builder = StepBuilder()
    for message in messages:
        role = message.get("role")
        if role == "assistant":
            _assistant(builder, message)
            continue
        text = _block_text(message.get("content"))
        if role == "system":
            builder.add("system", text)
        elif role == "user":
            _user(builder, text, code_agent)
        elif role in ("tool", "function"):
            builder.add("result", text)
    return builder.steps
