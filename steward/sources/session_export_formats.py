"""Parsers for exported session files with a top-level `messages` list (DataClaw, SWE-Together).

A message is `{role, content, timestamp?, thinking?, tool_uses?, content_parts?}`. Content is a
string or a list of Anthropic-style blocks (text, thinking, tool_use, tool_result, image).
DataClaw puts tool calls in `tool_uses` with the result inline in `output`; some exports carry
both `tool_use` blocks and a `tool_uses` summary of the same calls, in which case the blocks win.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from steward.schema import Step
from steward.sources.chat_messages import parse_chatml_tool_markup, strip_thinking
from steward.sources.transcripts import (
    StepBuilder,
    _block_text,
    _parse_arguments,
    action_content,
    parse_timestamp,
)

Message = dict[str, Any]

OUTPUT_TEXT_KEYS = ("text", "output", "raw", "contents", "markdown", "error")

HARNESS_BY_SOURCE = {
    "claude": "claude-code",
    "codex": "codex",
    "gemini": "gemini-cli",
    "opencode": "opencode",
    "openclaw": "openclaw",
    "cursor": "cursor",
}

CLAUDE_CODE_TOOLS = frozenset(
    {"Bash", "Read", "Edit", "MultiEdit", "Write", "Grep", "Glob", "Task", "TodoWrite", "LS"}
)

UNKNOWN_MODELS = frozenset({"", "unknown", "none", "null"})


def output_text(output: Any) -> str:
    """Readable tool output: the main text field, plus a nonzero exit code when recorded."""
    if output is None:
        return ""
    if not isinstance(output, dict):
        return _block_text(output)
    text = next(
        (str(output[key]) for key in OUTPUT_TEXT_KEYS if isinstance(output.get(key), str)),
        None,
    )
    if text is None:
        text = json.dumps(output, ensure_ascii=False, sort_keys=True, default=str)
    exit_code = output.get("exit_code", output.get("exitCode"))
    if isinstance(exit_code, int) and exit_code != 0:
        text = f"{text}\n[exit_code: {exit_code}]"
    return text


def _add_action(builder: StepBuilder, name: str, raw_input: Any, timestamp: Any) -> None:
    tool_input = _parse_arguments(raw_input)
    builder.add(
        "action",
        action_content(name, tool_input),
        tool=name,
        tool_input=tool_input,
        timestamp=timestamp,
    )


def _add_user_blocks(builder: StepBuilder, blocks: list[Any], timestamp: Any) -> None:
    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "tool_result":
            builder.add("result", _block_text(block.get("content")), timestamp=timestamp)
        elif kind == "text":
            builder.user_text(str(block.get("text", "")), timestamp)
        elif kind in ("image", "document"):
            builder.add("user", f"[{kind}]", timestamp=timestamp)


def _add_assistant_blocks(builder: StepBuilder, blocks: list[Any], timestamp: Any) -> None:
    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            builder.add(
                "agent_text", strip_thinking(str(block.get("text", ""))), timestamp=timestamp
            )
        elif kind == "tool_use":
            _add_action(builder, str(block.get("name") or "tool"), block.get("input"), timestamp)


def _has_tool_use_blocks(content: Any) -> bool:
    return isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") == "tool_use" for block in content
    )


def parse_session_messages(messages: Iterable[Message]) -> list[Step]:
    """Steps from a DataClaw-style message list. Thinking is dropped."""
    builder = StepBuilder()
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        timestamp = parse_timestamp(message.get("timestamp"))
        if role == "user":
            if isinstance(content, list):
                _add_user_blocks(builder, content, timestamp)
            elif isinstance(content, str):
                builder.user_text(content, timestamp)
            _add_user_blocks(builder, message.get("content_parts") or [], timestamp)
        elif role == "assistant":
            if isinstance(content, list):
                _add_assistant_blocks(builder, content, timestamp)
            elif isinstance(content, str):
                builder.add("agent_text", strip_thinking(content), timestamp=timestamp)
            if _has_tool_use_blocks(content):
                continue
            for use in message.get("tool_uses") or []:
                if not isinstance(use, dict):
                    continue
                _add_action(builder, str(use.get("tool") or "tool"), use.get("input"), timestamp)
                if "output" in use:
                    builder.add("result", output_text(use.get("output")), timestamp=timestamp)
            for part in message.get("content_parts") or []:
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    builder.add("result", _block_text(part.get("content")), timestamp=timestamp)
        elif role == "system" and isinstance(content, str):
            builder.add("system", content, timestamp=timestamp)
    return builder.steps


def parse_markup_messages(messages: Iterable[Message]) -> list[Step]:
    """Steps from plain `{role, content}` messages whose tool calls sit in <tool_use> tags."""
    chatml = "".join(
        f"<|im_start|>{message.get('role')}\n{message.get('content') or ''}<|im_end|>"
        for message in messages
        if isinstance(message, dict)
    )
    return parse_chatml_tool_markup(chatml)


def has_tool_markup(messages: Iterable[Message]) -> bool:
    return any(
        isinstance(message, dict)
        and isinstance(message.get("content"), str)
        and "<tool_use" in message["content"]
        for message in messages
    )


def infer_harness(source: str | None, steps: list[Step]) -> str | None:
    """Harness from the export's `source` field, else from Claude Code tool names."""
    if source:
        return HARNESS_BY_SOURCE.get(source, source)
    if any(step.tool in CLAUDE_CODE_TOOLS for step in steps):
        return "claude-code"
    return None


def clean_model(model: Any) -> str | None:
    """The model name as given, or None when it is missing, unknown or redacted."""
    if not isinstance(model, str) or model.strip().lower() in UNKNOWN_MODELS:
        return None
    if "REDACTED" in model:
        return None
    return model


def steps_hash(steps: list[Step]) -> str:
    """Content hash of a session's steps, used to drop byte-identical copies across datasets."""
    digest = hashlib.sha256()
    for step in steps:
        digest.update(json.dumps([step.kind, step.tool, step.content], ensure_ascii=False).encode())
    return digest.hexdigest()
