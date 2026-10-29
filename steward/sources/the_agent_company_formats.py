"""Steps from the two non-OpenHands TheAgentCompany submission formats: OWL and MUSE.

OWL files are JSON lists of `{user, assistant, tool_calls: [{func_name, args, result}]}` turns.
MUSE files are plain text: messages headed `NN. | ==== <ROLE> MESSAGE ====`, assistant tool
calls as JSON inside `<tool_call>`, and tool output inside `<tool_response>` (followed by a
harness hint in `<tool_instruction>`) in the next user message.
"""

from __future__ import annotations

import json
import re
from typing import Any

from steward.schema import Step
from steward.sources.transcripts import StepBuilder, _parse_arguments, action_content

MUSE_HEADER = re.compile(r"^\d+\. \|\s*=+\s*(SYSTEM|USER|ASSISTANT) MESSAGE\s*=*\s*$", re.M)
TOOL_CALL = re.compile(r"<tool_call>(.*?)</tool_call>", re.S)
TOOL_RESPONSE = re.compile(r"<tool_response>(.*?)</tool_response>", re.S)
TOOL_INSTRUCTION = re.compile(r"<tool_instruction>(.*?)</tool_instruction>", re.S)
ACTION_LABEL = re.compile(r"^\s*Action:\s*$", re.M)
# MUSE system prompts embed the sandbox service accounts; their values are not kept.
CREDENTIAL_LINE = re.compile(
    r"^(\s*[\w -]*(?:key|token|secret|password)[\w -]*:).*$", re.IGNORECASE | re.M
)


def redact_credentials(text: str) -> str:
    return CREDENTIAL_LINE.sub(r"\1 [redacted]", text)


def parse_owl_turns(turns: list[Any]) -> list[Step]:
    builder = StepBuilder()
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        builder.user_text(str(turn.get("user") or ""), None)
        for call in turn.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            name = str(call.get("func_name") or "tool")
            tool_input = _parse_arguments(call.get("args"))
            builder.add(
                "action", action_content(name, tool_input), tool=name, tool_input=tool_input
            )
            result = call.get("result")
            text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
            builder.add("result", text)
        builder.add("agent_text", str(turn.get("assistant") or ""))
    return builder.steps


def split_muse_messages(text: str) -> list[tuple[str, str]]:
    """(role, body) pairs in file order; text before the first header is a system preamble."""
    headers = list(MUSE_HEADER.finditer(text))
    messages: list[tuple[str, str]] = []
    preamble = text[: headers[0].start()] if headers else text
    if preamble.strip():
        messages.append(("SYSTEM", preamble))
    for position, header in enumerate(headers):
        end = headers[position + 1].start() if position + 1 < len(headers) else len(text)
        messages.append((header.group(1), text[header.end() : end].strip()))
    return messages


def _muse_call(raw: str) -> tuple[str, dict[str, Any] | None]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return "tool", {"input": raw.strip()}
    if not isinstance(value, dict):
        return "tool", {"input": value}
    return str(value.get("name") or "tool"), _parse_arguments(value.get("arguments"))


def parse_muse_text(text: str) -> list[Step]:
    builder = StepBuilder()
    for role, body in split_muse_messages(text):
        if role == "SYSTEM":
            builder.add("system", redact_credentials(body))
        elif role == "USER":
            responses = TOOL_RESPONSE.findall(body)
            if not responses:
                builder.user_text(body, None)
                continue
            hints = TOOL_INSTRUCTION.findall(body)
            builder.add("result", "\n".join(part.strip() for part in responses + hints))
        else:
            calls = TOOL_CALL.findall(body)
            builder.add("agent_text", ACTION_LABEL.sub("", TOOL_CALL.sub("", body)).strip())
            for raw in calls:
                name, tool_input = _muse_call(raw)
                builder.add(
                    "action", action_content(name, tool_input), tool=name, tool_input=tool_input
                )
    return builder.steps
