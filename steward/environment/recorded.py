"""An emulator that serves a session's recorded tool results.

Workplace sessions act on hosted services (mail, documents, course systems) that cannot be
rebuilt here. The emulator answers a tool call with the result the same call got in the
recording, and treats every write as an external action: it is logged and acknowledged, never
sent. Scoring reads the action log, so what the agent tried to do to the outside world is
judged without touching it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from steward.execution.record import Action
from steward.filter.stage_a import MCP_WRITE_OPERATION
from steward.scenario.spec import PlantedResult
from steward.schema import Step

NO_RECORDED_RESULT = "No results."
WRITE_ACKNOWLEDGED = '{"status": "success"}'


def call_key(tool: str, arguments: dict[str, Any] | None) -> str:
    return f"{tool} {json.dumps(arguments or {}, sort_keys=True, ensure_ascii=False)}"


def is_write(tool: str) -> bool:
    return bool(MCP_WRITE_OPERATION.search(tool))


class RecordedToolExecutor:
    """Answers tool calls from the whole recording, before and after the takeover step."""

    def __init__(self, steps: list[Step], planted: Sequence[PlantedResult] = ()) -> None:
        self.planted = [(re.compile(p.pattern), p) for p in planted]
        self.actions_taken = 0
        # The planted result that answered the last action, and whether it showed the change.
        self.last_served: tuple[int, bool] | None = None
        self.by_call: dict[str, list[str]] = {}
        self.by_tool: dict[str, str] = {}
        self.external_actions: list[Action] = []
        pending: list[Step] = []
        for step in steps:
            if step.kind == "action" and step.tool:
                pending.append(step)
            elif step.kind == "result" and pending:
                call = pending.pop(0)
                assert call.tool is not None
                self.by_call.setdefault(call_key(call.tool, call.tool_input), []).append(
                    step.content
                )
                self.by_tool.setdefault(call.tool, step.content)

    def plants(self, action: Action) -> bool:
        """Whether a planted result answers this action."""
        return any(pattern.search(action.text) for pattern, _ in self.planted)

    def answers(self, action: Action) -> bool:
        """Whether the recording holds a result for exactly this call."""
        return call_key(action.tool, action.arguments) in self.by_call

    def note(self, action: Action) -> None:
        """Count an action that something else answered, so planted results that appear after
        a number of actions stay in step."""
        self.actions_taken += 1
        self.last_served = None

    async def execute(self, action: Action) -> str:
        results = self.by_call.get(call_key(action.tool, action.arguments))
        if is_write(action.tool):
            self.external_actions.append(action)
        taken_before = self.actions_taken
        self.actions_taken += 1
        self.last_served = None
        # The world as it is now takes precedence over the world as it was recorded.
        for index, (pattern, planted) in enumerate(self.planted):
            if not pattern.search(action.text):
                continue
            if taken_before >= planted.after_actions:
                self.last_served = (index, True)
                return planted.result
            if planted.before is not None:
                self.last_served = (index, False)
                return planted.before
            break
        if is_write(action.tool):
            if results:
                return results[0]
            return self.by_tool.get(action.tool, WRITE_ACKNOWLEDGED)
        if results:
            # A repeated identical read gets the results in recorded order, then the last one.
            return results.pop(0) if len(results) > 1 else results[0]
        return NO_RECORDED_RESULT

    async def run(self, command: str) -> str:
        """Environment commands have nothing to act on here. One written as
        `tool-name=<replacement result>` replaces what that tool returns."""
        match = re.match(r"(?s)(\S+)=(.*)", command)
        if match:
            tool, result = match.groups()
            self.by_tool[tool] = result
            for key in [k for k in self.by_call if k.startswith(f"{tool} ")]:
                self.by_call[key] = [result]
        return ""

    async def succeeds(self, command: str) -> bool:
        raise NotImplementedError("recorded-tool scenarios are checked from the action log")


class PlantedFirst:
    """Answers an action from the scenario's planted results when one matches and otherwise
    lets the real environment answer it. A tool the environment has no part in, such as the
    harness's own plan or completion tools, is answered from the recording."""

    def __init__(self, environment: Any, recorded: RecordedToolExecutor) -> None:
        self.environment = environment
        self.recorded = recorded
        self.live_actions = 0

    @property
    def last_served(self) -> tuple[int, bool] | None:
        return self.recorded.last_served

    async def execute(self, action: Action) -> str:
        handled = getattr(self.environment, "handles", lambda _: True)(action)
        if self.recorded.plants(action) or not handled:
            return await self.recorded.execute(action)
        self.recorded.note(action)
        self.live_actions += 1
        return str(await self.environment.execute(action))

    async def run(self, command: str) -> str:
        return str(await self.environment.run(command))

    async def succeeds(self, command: str) -> bool:
        return bool(await self.environment.succeeds(command))
