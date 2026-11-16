"""What the agent does first, sampled many times, with one piece of the inherited session
removed at a time. Needs no environment: only the first turn is asked for."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from steward.cases.case import Case
from steward.execution.history import to_messages, tool_definitions
from steward.execution.policy import Message, Policy
from steward.schema import Step

Shape = str  # "change", "look_then_change", "read", "message", "report"


def shape(case: Case, policy: Policy, messages: list[Message]) -> Shape:
    """The kind of first turn: a command that changes the thing (alone, or after a look in
    the same command), a read-only command, a message, or the report."""
    turn = policy.next_turn(messages)
    if turn.action is None:
        return "report" if turn.report is not None else "message"
    text = turn.action.text
    if re.search(case.measures.change, text):
        return "look_then_change" if re.search(case.measures.look, text) else "change"
    return "read"


def without(steps: list[Step], index: int) -> list[Step]:
    """The session with one step removed; a command goes together with its result."""
    drop = {index}
    if steps[index].kind == "action" and index + 1 < len(steps):
        drop.add(index + 1)
    return [s for i, s in enumerate(steps) if i not in drop]


def variants(steps: list[Step]) -> dict[str, list[Step]]:
    """The whole session, then the session with each user message, agent message and
    command removed in turn."""
    named = {"whole session": steps}
    for index, step in enumerate(steps):
        if step.kind in ("user", "agent_text", "action"):
            label = f"without step {index} ({step.kind}): {step.content[:70]}"
            named[label] = without(steps, index)
    return named


def sample(
    case: Case,
    steps: list[Step],
    policy_for: Callable[[list[Message]], Policy],
    samples: int,
    workers: int,
) -> dict[str, dict[str, int]]:
    """For each variant of the session, how often each kind of first turn was chosen."""
    task: Message = {"role": "user", "content": case.lines.task}
    tools = tool_definitions(steps)
    jobs = [(name, [*to_messages(kept), task]) for name, kept in variants(steps).items()] * samples

    def one(job: tuple[str, list[Message]]) -> tuple[str, Shape]:
        return job[0], shape(case, policy_for(tools), job[1])

    counts: dict[str, Counter[str]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for name, kind in pool.map(one, jobs):
            counts.setdefault(name, Counter())[kind] += 1
    return {name: dict(c) for name, c in counts.items()}


def write(path: Path, result: dict[str, dict[str, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=1))
