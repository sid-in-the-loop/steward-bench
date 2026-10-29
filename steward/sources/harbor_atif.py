"""Parser for Harbor ATIF agent/trajectory.json files (Agent Trajectory Interchange Format).

Two shapes exist: the standard one ({schema_version, agent, steps[]} with step `message`,
tool_calls[{function_name, arguments}] and observation{results[]}) and a wrapper used by
CodeBrain ({..., atif_trajectory{steps[]}} with step `content`, tool_calls[{tool_name,
parameters}] and observation{content, is_error}). reasoning_content is dropped.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from steward.schema import Step
from steward.sources.chat_messages import strip_thinking
from steward.sources.transcripts import (
    StepBuilder,
    _block_text,
    _parse_arguments,
    action_content,
    parse_timestamp,
)


class AtifTrajectory(BaseModel):
    steps: list[Step]
    agent_name: str | None = None
    model_name: str | None = None
    schema_version: str | None = None


def _atif_action_content(tool: str, tool_input: dict[str, Any] | None) -> str:
    """Terminus sends raw keystrokes to tmux; show them as the command."""
    if tool_input and isinstance(tool_input.get("keystrokes"), str):
        return str(tool_input["keystrokes"])
    return action_content(tool, tool_input)


def _observation_texts(observation: Any) -> list[str]:
    if not isinstance(observation, dict):
        return []
    if "results" in observation:
        return [
            _block_text(result.get("content"))
            for result in observation.get("results") or []
            if isinstance(result, dict)
        ]
    if "content" in observation:
        return [_block_text(observation.get("content"))]
    return []


def parse_atif(document: dict[str, Any]) -> AtifTrajectory:
    trajectory = document.get("atif_trajectory")
    if not isinstance(trajectory, dict):
        trajectory = document
    agent: dict[str, Any] = trajectory["agent"] if isinstance(trajectory.get("agent"), dict) else {}
    builder = StepBuilder()
    for step in trajectory.get("steps") or []:
        if not isinstance(step, dict):
            continue
        timestamp = parse_timestamp(step.get("timestamp"))
        text = strip_thinking(_block_text(step.get("message", step.get("content"))))
        source = step.get("source")
        if source == "user":
            builder.user_text(text, timestamp)
        elif source == "system":
            builder.add("system", text, timestamp=timestamp)
        else:
            builder.add("agent_text", text, timestamp=timestamp)
        for call in step.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            name = str(call.get("function_name") or call.get("tool_name") or "tool")
            tool_input = _parse_arguments(call.get("arguments", call.get("parameters")))
            builder.add(
                "action",
                _atif_action_content(name, tool_input),
                tool=name,
                tool_input=tool_input,
                timestamp=timestamp,
            )
        for output in _observation_texts(step.get("observation")):
            builder.add("result", output, timestamp=timestamp)
    model = agent.get("model_name")
    agent_name = agent.get("name")
    version = trajectory.get("schema_version")
    return AtifTrajectory(
        steps=builder.steps,
        agent_name=str(agent_name) if agent_name else None,
        model_name=str(model) if model else None,
        schema_version=str(version) if version else None,
    )
