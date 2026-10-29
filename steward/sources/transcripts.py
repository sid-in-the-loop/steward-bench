"""Parsers from coding-agent transcript formats to Steps.

Each parser takes the decoded JSONL records of one session and returns a ParsedTranscript.
Thinking and reasoning blocks are dropped. Subagent (sidechain) records are skipped.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from steward.filter.tags import is_interruption_text
from steward.schema import Step, StepKind

Record = dict[str, Any]

SYSTEM_PREFIXES = (
    "<system-reminder>",
    "<command-name>",
    "<command-message>",
    "<local-command-stdout>",
    "<local-command-caveat>",
    "<environment_context>",
    "<user_instructions>",
    "<permissions instructions>",
    "# AGENTS.md",
    "Caveat: The messages below",
)


class ParsedTranscript(BaseModel):
    steps: list[Step]
    models: Counter[str] = Field(default_factory=Counter)
    base_commit: str | None = None
    repository_url: str | None = None
    is_continuation: bool = False


def parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(seconds).astimezone()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


class StepBuilder:
    def __init__(self) -> None:
        self.steps: list[Step] = []

    def add(
        self,
        kind: StepKind,
        content: str,
        *,
        tool: str | None = None,
        tool_input: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
        is_interruption: bool = False,
    ) -> None:
        if not content.strip() and kind not in ("action", "result"):
            return
        self.steps.append(
            Step(
                index=len(self.steps),
                kind=kind,
                content=content,
                tool=tool,
                tool_input=tool_input,
                timestamp=timestamp,
                is_interruption=is_interruption,
            )
        )

    def user_text(self, text: str, timestamp: datetime | None) -> None:
        stripped = text.lstrip()
        if is_interruption_text(stripped):
            self.add("user", text, timestamp=timestamp, is_interruption=True)
        elif stripped.startswith(SYSTEM_PREFIXES):
            self.add("system", text, timestamp=timestamp)
        else:
            self.add("user", text, timestamp=timestamp)


def action_content(tool: str, tool_input: dict[str, Any] | None) -> str:
    """One readable line or block for an action: the command for shells, else the input."""
    if not tool_input:
        return tool
    for key in ("command", "cmd"):
        value = tool_input.get(key)
        if isinstance(value, list):
            return " ".join(str(v) for v in value)
        if isinstance(value, str):
            return value
    return json.dumps(tool_input, ensure_ascii=False, sort_keys=True)


def _parse_arguments(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {"input": raw}
        return value if isinstance(value, dict) else {"input": value}
    return None


def _block_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") in ("text", "input_text", "output_text"):
                    parts.append(str(block.get("text", "")))
                elif block.get("type") == "image":
                    parts.append("[image]")
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return "" if content is None else str(content)


def parse_claude_code(records: Iterable[Record]) -> ParsedTranscript:
    builder = StepBuilder()
    models: Counter[str] = Counter()
    continuation = False
    for record in records:
        kind = record.get("type")
        if record.get("isSidechain"):
            continue
        timestamp = parse_timestamp(record.get("timestamp"))
        message = record.get("message")
        if kind == "summary":
            continuation = True
            continue
        if kind == "system":
            builder.add("system", str(record.get("content", "")), timestamp=timestamp)
            continue
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if kind == "user":
            if record.get("isCompactSummary"):
                continuation = True
                builder.add("system", _block_text(content), timestamp=timestamp)
                continue
            if record.get("isMeta"):
                builder.add("system", _block_text(content), timestamp=timestamp)
                continue
            if isinstance(content, str):
                builder.user_text(content, timestamp)
                continue
            for block in content or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    builder.add("result", _block_text(block.get("content")), timestamp=timestamp)
                elif block.get("type") == "text":
                    builder.user_text(str(block.get("text", "")), timestamp)
        elif kind == "assistant":
            model = message.get("model")
            if isinstance(model, str) and model and model != "<synthetic>":
                models[model] += 1
            if isinstance(content, str):
                builder.add("agent_text", content, timestamp=timestamp)
                continue
            for block in content or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    builder.add("agent_text", str(block.get("text", "")), timestamp=timestamp)
                elif block.get("type") == "tool_use":
                    name = str(block.get("name", "tool"))
                    tool_input = _parse_arguments(block.get("input"))
                    builder.add(
                        "action",
                        action_content(name, tool_input),
                        tool=name,
                        tool_input=tool_input,
                        timestamp=timestamp,
                    )
    return ParsedTranscript(steps=builder.steps, models=models, is_continuation=continuation)


def parse_codex(records: Iterable[Record]) -> ParsedTranscript:
    builder = StepBuilder()
    models: Counter[str] = Counter()
    base_commit = None
    repository_url = None
    continuation = False
    for record in records:
        kind = record.get("type")
        payload = record.get("payload")
        timestamp = parse_timestamp(record.get("timestamp"))
        if not isinstance(payload, dict):
            continue
        if kind == "session_meta":
            git = payload.get("git") or {}
            base_commit = git.get("commit_hash") or base_commit
            repository_url = git.get("repository_url") or repository_url
            continue
        if kind == "turn_context":
            model = payload.get("model")
            if isinstance(model, str) and model:
                models[model] += 1
            continue
        if kind == "compacted":
            continuation = True
            continue
        if kind == "event_msg":
            if payload.get("type") == "turn_aborted":
                reason = str(payload.get("reason", ""))
                builder.add(
                    "system",
                    f"[turn aborted: {reason}]",
                    timestamp=timestamp,
                    is_interruption=reason == "interrupted",
                )
            continue
        if kind != "response_item":
            continue
        item = payload.get("type")
        if item == "message":
            role = payload.get("role")
            text = _block_text(payload.get("content"))
            if role == "user":
                builder.user_text(text, timestamp)
            elif role == "assistant":
                builder.add("agent_text", text, timestamp=timestamp)
            else:
                builder.add("system", text, timestamp=timestamp)
        elif item in ("function_call", "custom_tool_call", "local_shell_call"):
            name = str(payload.get("name") or item)
            raw = payload.get("arguments", payload.get("input", payload.get("action")))
            tool_input = _parse_arguments(raw)
            builder.add(
                "action",
                action_content(name, tool_input),
                tool=name,
                tool_input=tool_input,
                timestamp=timestamp,
            )
        elif item in ("function_call_output", "custom_tool_call_output", "local_shell_call_output"):
            output = payload.get("output")
            if isinstance(output, dict):
                output = output.get("output", json.dumps(output))
            builder.add("result", _block_text(output), timestamp=timestamp)
        elif item == "web_search_call":
            action = payload.get("action") if isinstance(payload.get("action"), dict) else None
            builder.add(
                "action",
                action_content("web_search", action),
                tool="web_search",
                tool_input=action,
                timestamp=timestamp,
            )
    return ParsedTranscript(
        steps=builder.steps,
        models=models,
        base_commit=base_commit,
        repository_url=repository_url,
        is_continuation=continuation,
    )


def parse_pi(records: Iterable[Record]) -> ParsedTranscript:
    builder = StepBuilder()
    models: Counter[str] = Counter()
    continuation = False
    for record in records:
        kind = record.get("type")
        timestamp = parse_timestamp(record.get("timestamp"))
        if kind == "compaction":
            continuation = True
            continue
        if kind != "message" or not isinstance(record.get("message"), dict):
            continue
        message = record["message"]
        role = message.get("role")
        content = message.get("content")
        if role == "user":
            builder.user_text(_block_text(content), timestamp)
        elif role == "assistant":
            model = message.get("model")
            if isinstance(model, str) and model:
                models[model] += 1
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    builder.add("agent_text", str(block.get("text", "")), timestamp=timestamp)
                elif block.get("type") == "toolCall":
                    name = str(block.get("name", "tool"))
                    tool_input = _parse_arguments(block.get("arguments"))
                    builder.add(
                        "action",
                        action_content(name, tool_input),
                        tool=name,
                        tool_input=tool_input,
                        timestamp=timestamp,
                    )
            if message.get("stopReason") == "aborted":
                builder.add(
                    "system", "[assistant turn aborted]", timestamp=timestamp, is_interruption=True
                )
        elif role in ("toolResult", "bashExecution"):
            text = _block_text(content) if content is not None else str(message.get("output", ""))
            builder.add("result", text, timestamp=timestamp)
    return ParsedTranscript(steps=builder.steps, models=models, is_continuation=continuation)


def parse_copilot(records: Iterable[Record]) -> ParsedTranscript:
    builder = StepBuilder()
    for record in records:
        kind = record.get("type")
        data = record.get("data")
        timestamp = parse_timestamp(record.get("timestamp"))
        if not isinstance(data, dict):
            continue
        if kind == "user.message":
            builder.user_text(str(data.get("content", "")), timestamp)
        elif kind == "assistant.message":
            builder.add("agent_text", str(data.get("content") or ""), timestamp=timestamp)
            for request in data.get("toolRequests") or []:
                name = str(request.get("name", "tool"))
                tool_input = _parse_arguments(request.get("arguments"))
                builder.add(
                    "action",
                    action_content(name, tool_input),
                    tool=name,
                    tool_input=tool_input,
                    timestamp=timestamp,
                )
        elif kind == "tool.execution_complete":
            result = data.get("result")
            if isinstance(result, dict):
                result = result.get("content", json.dumps(result))
            builder.add("result", _block_text(result), timestamp=timestamp)
        elif kind == "abort":
            builder.add("system", "[aborted]", timestamp=timestamp, is_interruption=True)
    return ParsedTranscript(steps=builder.steps)


Parser = Callable[[Iterable[Record]], ParsedTranscript]


def detect_format(records: list[Record]) -> str | None:
    """Guess the transcript format from the first records."""
    for record in records[:50]:
        kind = record.get("type")
        if kind in ("session_meta", "response_item", "turn_context", "event_msg"):
            return "codex"
        if isinstance(kind, str) and "." in kind and isinstance(record.get("data"), dict):
            return "copilot"
        if kind == "message" and isinstance(record.get("message"), dict):
            role = record["message"].get("role")
            if role in ("toolResult", "assistant", "user") and "parentId" in record:
                return "pi"
        if kind in ("user", "assistant") and isinstance(record.get("message"), dict):
            return "claude_code"
    return None


PARSERS: dict[str, Parser] = {
    "claude_code": parse_claude_code,
    "codex": parse_codex,
    "pi": parse_pi,
    "copilot": parse_copilot,
}


def read_jsonl_records(path: str) -> list[Record]:
    records: list[Record] = []
    with open(path, errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
    return records


def parse_records(records: list[Record]) -> tuple[str | None, ParsedTranscript | None]:
    fmt = detect_format(records)
    if fmt is None:
        return None, None
    return fmt, PARSERS[fmt](records)
