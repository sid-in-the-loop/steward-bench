"""Parsers for OpenAI-style message lists and ChatML strings with inline tool markup."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from steward.schema import Step
from steward.sources.transcripts import StepBuilder, _block_text, _parse_arguments, action_content

THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
CHATML_TURN = re.compile(r"<\|im_start\|>(\w+)\n?(.*?)<\|im_end\|>", re.DOTALL)
TOOL_USE = re.compile(r"<tool_use[^>]*>(.*?)</tool_use>", re.DOTALL)
TOOL_RESULT = re.compile(r"<tool_result[^>]*>(.*?)</tool_result>", re.DOTALL)


def strip_thinking(text: str) -> str:
    return THINK.sub("", text).strip()


def parse_openai_messages(
    messages: Iterable[dict[str, Any]], first_user_is_task: bool = True
) -> list[Step]:
    """Steps from a list of {role, content, tool_calls} messages (OpenHands, SWE-agent, HAL)."""
    builder = StepBuilder()
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        text = strip_thinking(_block_text(content))
        if role == "system":
            builder.add("system", text)
        elif role == "user":
            builder.user_text(text, None)
        elif role == "assistant":
            builder.add("agent_text", text)
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                name = str(function.get("name") or "tool")
                tool_input = _parse_arguments(function.get("arguments"))
                builder.add(
                    "action", action_content(name, tool_input), tool=name, tool_input=tool_input
                )
        elif role in ("tool", "function", "ipython"):
            builder.add("result", text)
    return builder.steps


def parse_chatml_tool_markup(chatml: str) -> list[Step]:
    """Steps from ChatML where tool calls sit in <tool_use> and results in <tool_result> tags."""
    builder = StepBuilder()
    for role, body in CHATML_TURN.findall(chatml):
        body = strip_thinking(body)
        if role == "system":
            builder.add("system", body)
        elif role == "user":
            results = TOOL_RESULT.findall(body)
            remainder = TOOL_RESULT.sub("", body).strip()
            for result in results:
                builder.add("result", result.strip())
            if remainder:
                builder.user_text(remainder, None)
        elif role == "assistant":
            calls = TOOL_USE.findall(body)
            text = TOOL_USE.sub("", body).strip()
            builder.add("agent_text", text)
            for call in calls:
                try:
                    value = json.loads(call)
                except json.JSONDecodeError:
                    value = {"name": "tool", "arguments": {"input": call.strip()}}
                name = str(value.get("name") or "tool") if isinstance(value, dict) else "tool"
                raw = (
                    value.get("arguments", value.get("input")) if isinstance(value, dict) else call
                )
                tool_input = _parse_arguments(raw)
                builder.add(
                    "action", action_content(name, tool_input), tool=name, tool_input=tool_input
                )
    return builder.steps
