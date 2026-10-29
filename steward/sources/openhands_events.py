"""Steps from OpenHands event streams (TheAgentCompany and OpenAgentSafety trajectories).

Each event is an action (`action`, `args`) or an observation (`observation`, `content`).
The `think` tool and its echo are reasoning and are dropped. `recall` observations carry
workspace context injected by the harness and become system steps.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from steward.schema import Step, StepKind
from steward.sources.transcripts import StepBuilder, action_content, parse_timestamp

NOISE_ARGS = frozenset(
    {
        "thought",
        "confirmation_state",
        "blocking",
        "hidden",
        "keep_prompt",
        "is_input",
        "is_static",
        "impl_source",
        "include_extra",
        "kernel_init_code",
        "browsergym_send_msg_to_user",
        "wait_for_response",
        "image_urls",
        "security_risk",
        "start",
        "end",
    }
)
SKIPPED_ACTIONS = frozenset({"think", "recall", "change_agent_state", "null"})
SKIPPED_OBSERVATIONS = frozenset({"think", "agent_state_changed", "null", "condense"})
STRUCTURED_ACTIONS = frozenset({"read", "edit"})
TEXT_ARGS = ("code", "browser_actions", "url", "query")


class ParsedEvents(BaseModel):
    steps: list[Step]
    models: Counter[str] = Field(default_factory=Counter)


def tool_input_from_args(args: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in args.items()
        if key not in NOISE_ARGS and value not in (None, "", [], {})
    }


def readable_action(action: str, tool: str, tool_input: dict[str, Any]) -> str:
    if action in STRUCTURED_ACTIONS:
        return json.dumps(tool_input, ensure_ascii=False, sort_keys=True)
    if action == "chat":
        return f"{tool_input.get('npc_name', '')}: {tool_input.get('content', '')}"
    for key in TEXT_ARGS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return action_content(tool, tool_input)


def _model(event: dict[str, Any]) -> str | None:
    metadata = event.get("tool_call_metadata")
    if not isinstance(metadata, dict):
        return None
    response = metadata.get("model_response")
    model = response.get("model") if isinstance(response, dict) else None
    return model if isinstance(model, str) and model else None


def _tool_name(event: dict[str, Any], action: str) -> str:
    metadata = event.get("tool_call_metadata")
    if isinstance(metadata, dict) and metadata.get("function_name"):
        return str(metadata["function_name"])
    return action


def parse_openhands_events(events: Iterable[Any]) -> ParsedEvents:
    builder = StepBuilder()
    models: Counter[str] = Counter()
    for event in events:
        if not isinstance(event, dict):
            continue
        timestamp = parse_timestamp(event.get("timestamp"))
        model = _model(event)
        if model:
            models[model] += 1
        action = event.get("action")
        observation = event.get("observation")
        raw_args = event.get("args")
        args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
        if isinstance(action, str):
            _add_action(builder, event, action, args, timestamp)
        elif isinstance(observation, str):
            if observation in SKIPPED_OBSERVATIONS:
                continue
            kind: StepKind = "system" if observation == "recall" else "result"
            builder.add(kind, str(event.get("content") or ""), timestamp=timestamp)
    return ParsedEvents(steps=builder.steps, models=models)


def _add_action(
    builder: StepBuilder,
    event: dict[str, Any],
    action: str,
    args: dict[str, Any],
    timestamp: datetime | None,
) -> None:
    if action in SKIPPED_ACTIONS:
        return
    if action == "message":
        text = str(args.get("content") or "")
        if event.get("source") == "user":
            builder.user_text(text, timestamp)
        else:
            builder.add("agent_text", text, timestamp=timestamp)
        return
    if action == "system":
        builder.add("system", str(args.get("content") or ""), timestamp=timestamp)
        return
    if action == "condensation":
        builder.add("system", str(args.get("summary") or ""), timestamp=timestamp)
        return
    thought = args.get("final_thought") if action == "finish" else None
    builder.add("agent_text", str(thought or args.get("thought") or ""), timestamp=timestamp)
    tool = _tool_name(event, action)
    tool_input = tool_input_from_args(args)
    tool_input.pop("final_thought", None)
    builder.add(
        "action",
        readable_action(action, tool, tool_input),
        tool=tool,
        tool_input=tool_input or None,
        timestamp=timestamp,
    )
