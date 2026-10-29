"""Trajectory formats found in SWE-bench experiments submissions, one parser per format.

Format detection is by file structure, see detect_format. Each parser takes the
decoded file and returns Steps. Thinking and reasoning fields are never read.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from steward.schema import Step
from steward.sources.chat_messages import parse_openai_messages, strip_thinking
from steward.sources.transcripts import StepBuilder, _block_text, _parse_arguments, action_content

TrajFormat = Literal[
    "mini_text",
    "mini_tool_calls",
    "mini_responses",
    "swe_agent",
    "openhands_tool_calls",
    "openhands_prompt",
    "openhands_flattened",
    "llamaindex",
    "nova_log",
]

UNSUPPORTED_FORMATS: frozenset[TrajFormat] = frozenset({"openhands_flattened"})

# mini-SWE-agent runs a turn's command only when the turn has exactly one such block.
BASH_BLOCK = re.compile(r"```bash\s*\n(.*?)\n```", re.DOTALL)
FUNCTION_CALL = re.compile(r"<function=([^>\s]+)>(.*?)(?:</function>|\Z)", re.DOTALL)
PARAMETER = re.compile(r"<parameter=([^>\s]+)>(.*?)</parameter>", re.DOTALL)
NOVA_EXECUTE = re.compile(r"<execute>(.*?)(?:</execute>|\Z)", re.DOTALL)
NOVA_COMMAND = re.compile(r"<(\w+)\s*/>|<(\w+)>(.*?)</\2>", re.DOTALL)
NOVA_FIELD = re.compile(r"<(\w+)>(.*?)</\1>", re.DOTALL)
SCRATCHPAD_TAG = re.compile(r"</?scratchpad>")


def read_traj_file(path: Path) -> Any:
    """Decode a trajectory file: JSON for .json/.traj, one JSON object per line for .log."""
    if path.suffix == ".log":
        records = []
        with path.open(errors="replace") as handle:
            for line in handle:
                if line.strip():
                    records.append(json.loads(line))
        return records
    with path.open(errors="replace") as handle:
        return json.load(handle)


def _is_record_list(data: Any) -> bool:
    return isinstance(data, list) and bool(data) and all(isinstance(m, dict) for m in data)


def detect_format(data: Any) -> TrajFormat | None:
    if isinstance(data, dict):
        if "history" in data and "trajectory" in data:
            return "swe_agent"
        if not str(data.get("trajectory_format", "")).startswith("mini-swe-agent"):
            return None
        messages = [m for m in data.get("messages") or [] if isinstance(m, dict)]
        if any(m.get("object") == "response" or "call_id" in m for m in messages):
            return "mini_responses"
        if any(m.get("tool_calls") for m in messages):
            return "mini_tool_calls"
        return "mini_text"
    if not _is_record_list(data):
        return None
    first = data[0]
    if "iteration" in first and "response" in first:
        return "nova_log"
    if "blocks" in first:
        return "llamaindex"
    if any(m.get("role") == "tool" or m.get("tool_calls") for m in data):
        return "openhands_tool_calls"
    if any(m.get("role") == "assistant" and "<function=" in str(m.get("content")) for m in data):
        return "openhands_prompt"
    return "openhands_flattened"


def _observation(builder: StepBuilder, text: str) -> None:
    """Harness messages after the task: tool output after an action, otherwise a system note."""
    last = builder.steps[-1].kind if builder.steps else None
    builder.add("result" if last == "action" else "system", text)


def _bash_action(builder: StepBuilder, command: str) -> None:
    tool_input = {"command": command}
    builder.add("action", action_content("bash", tool_input), tool="bash", tool_input=tool_input)


def parse_mini_text(data: dict[str, Any]) -> list[Step]:
    """F1: assistant text with one fenced bash block per turn; observations as user turns."""
    builder = StepBuilder()
    seen_task = False
    for message in data.get("messages") or []:
        role = message.get("role")
        text = _block_text(message.get("content"))
        if role == "system":
            builder.add("system", text)
        elif role == "user" and not seen_task:
            seen_task = True
            builder.user_text(text, None)
        elif role == "user":
            _observation(builder, text)
        elif role == "assistant":
            text = strip_thinking(text)
            blocks = BASH_BLOCK.findall(text)
            if len(blocks) == 1:
                builder.add("agent_text", BASH_BLOCK.sub("", text).strip())
                _bash_action(builder, blocks[0].strip())
            else:
                builder.add("agent_text", text)
    return builder.steps


def _later_users_as_system(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """In benchmark runs only the first user message is the task; later ones are harness
    feedback such as format errors."""
    out: list[dict[str, Any]] = []
    seen_task = False
    for message in messages:
        if message.get("role") == "user":
            if seen_task:
                message = {**message, "role": "system"}
            seen_task = True
        out.append(message)
    return out


def parse_mini_tool_calls(data: dict[str, Any]) -> list[Step]:
    """F2a: chat-completions messages with tool_calls; the closing exit record is skipped."""
    messages = [m for m in data.get("messages") or [] if m.get("role") != "exit"]
    return parse_openai_messages(_later_users_as_system(messages))


def parse_mini_responses(data: dict[str, Any]) -> list[Step]:
    """F2b: OpenAI Responses objects; reasoning items are dropped."""
    builder = StepBuilder()
    for message in data.get("messages") or []:
        role = message.get("role")
        if role == "system":
            builder.add("system", _block_text(message.get("content")))
        elif role == "user":
            builder.user_text(_block_text(message.get("content")), None)
        elif message.get("object") == "response":
            for item in message.get("output") or []:
                kind = item.get("type")
                if kind == "message":
                    builder.add("agent_text", strip_thinking(_block_text(item.get("content"))))
                elif kind == "function_call":
                    name = str(item.get("name") or "tool")
                    tool_input = _parse_arguments(item.get("arguments"))
                    builder.add(
                        "action", action_content(name, tool_input), tool=name, tool_input=tool_input
                    )
        elif message.get("type") == "function_call_output":
            builder.add("result", _block_text(message.get("output")))
    return builder.steps


def _swe_agent_tool(command: str) -> str:
    first = command.split(maxsplit=1)[0] if command.split() else ""
    if first in ("str_replace_editor", "submit"):
        return first
    return "bash"


def parse_swe_agent(data: dict[str, Any]) -> list[Step]:
    """F3: the .traj history; assistant turns carry thought and action separately."""
    builder = StepBuilder()
    seen_task = False
    for message in data.get("history") or []:
        role = message.get("role")
        if role == "system":
            builder.add("system", _block_text(message.get("content")))
        elif role == "user" and not seen_task:
            seen_task = True
            builder.user_text(_block_text(message.get("content")), None)
        elif role == "user":
            _observation(builder, _block_text(message.get("content")))
        elif role == "assistant":
            builder.add("agent_text", strip_thinking(str(message.get("thought") or "")))
            command = str(message.get("action") or "").strip()
            if command:
                tool = _swe_agent_tool(command)
                builder.add("action", command, tool=tool, tool_input={"command": command})
    return builder.steps


def parse_openhands_tool_calls(data: list[dict[str, Any]]) -> list[Step]:
    """F4a: OpenAI-style messages with native tool calls."""
    return parse_openai_messages(_later_users_as_system(data))


def _strip_one_newline(value: str) -> str:
    return value.removeprefix("\n").removesuffix("\n")


def parse_openhands_prompt(data: list[dict[str, Any]]) -> list[Step]:
    """F4b: calls written as <function=NAME><parameter=K>V</parameter></function> in text."""
    builder = StepBuilder()
    seen_task = False
    for message in data:
        role = message.get("role")
        text = _block_text(message.get("content"))
        if role == "system":
            builder.add("system", text)
        elif role == "user" and not seen_task:
            seen_task = True
            builder.user_text(text, None)
        elif role == "user":
            _observation(builder, text)
        elif role == "assistant":
            text = strip_thinking(text)
            builder.add("agent_text", FUNCTION_CALL.sub("", text).strip())
            for name, body in FUNCTION_CALL.findall(text):
                tool_input = {
                    key: _strip_one_newline(value) for key, value in PARAMETER.findall(body)
                }
                builder.add(
                    "action", action_content(name, tool_input), tool=name, tool_input=tool_input
                )
    return builder.steps


def _llamaindex_text(message: dict[str, Any]) -> str:
    parts = [
        str(block.get("text", ""))
        for block in message.get("blocks") or []
        if block.get("block_type") == "text"
    ]
    return "\n".join(parts)


def parse_llamaindex(data: list[dict[str, Any]]) -> list[Step]:
    """F6: LlamaIndex ChatMessage dump; thinking blocks are dropped."""
    builder = StepBuilder()
    seen_task = False
    for message in data:
        role = message.get("role")
        text = _llamaindex_text(message)
        if role == "system":
            builder.add("system", text)
        elif role == "user" and not seen_task:
            seen_task = True
            builder.user_text(text, None)
        elif role == "user":
            _observation(builder, text)
        elif role == "assistant":
            builder.add("agent_text", strip_thinking(text))
            for call in (message.get("additional_kwargs") or {}).get("tool_calls") or []:
                function = call.get("function") or {}
                name = str(function.get("name") or "tool")
                tool_input = _parse_arguments(function.get("arguments"))
                builder.add(
                    "action", action_content(name, tool_input), tool=name, tool_input=tool_input
                )
        elif role == "tool":
            builder.add("result", text)
    return builder.steps


def _nova_commands(block: str) -> list[tuple[str, dict[str, Any]]]:
    commands: list[tuple[str, dict[str, Any]]] = []
    for empty_tag, tag, body in NOVA_COMMAND.findall(block):
        if empty_tag:
            commands.append((empty_tag, {}))
        else:
            fields = {key: value.strip("\n") for key, value in NOVA_FIELD.findall(body)}
            commands.append((tag, fields or {"input": body.strip()}))
    return commands


def parse_nova_log(data: list[dict[str, Any]]) -> list[Step]:
    """F5: one record per model call. Each later call's last input message is the observation
    of the previous call's commands, so the full prompt is emitted only once."""
    builder = StepBuilder()
    for position, record in enumerate(sorted(data, key=lambda r: int(r.get("iteration", 0)))):
        inputs = [m for m in record.get("input") or [] if isinstance(m, dict)]
        if position == 0:
            for message in inputs:
                text = _block_text(message.get("content"))
                if message.get("role") == "system":
                    builder.add("system", text)
                else:
                    builder.user_text(text, None)
        elif inputs:
            _observation(builder, _block_text(inputs[-1].get("content")))
        response = strip_thinking(str(record.get("response") or ""))
        text = SCRATCHPAD_TAG.sub("", NOVA_EXECUTE.sub("", response)).strip()
        builder.add("agent_text", text)
        for block in NOVA_EXECUTE.findall(response):
            for name, tool_input in _nova_commands(block):
                builder.add(
                    "action", action_content(name, tool_input), tool=name, tool_input=tool_input
                )
    return builder.steps


PARSERS: dict[TrajFormat, Callable[[Any], list[Step]]] = {
    "mini_text": parse_mini_text,
    "mini_tool_calls": parse_mini_tool_calls,
    "mini_responses": parse_mini_responses,
    "swe_agent": parse_swe_agent,
    "openhands_tool_calls": parse_openhands_tool_calls,
    "openhands_prompt": parse_openhands_prompt,
    "llamaindex": parse_llamaindex,
    "nova_log": parse_nova_log,
}
