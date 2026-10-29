"""Stage B summaries and the windowed rendering used by C1 and C2 (brief 6.2 and 6.4)."""

from __future__ import annotations

import math
from collections.abc import Sequence

from steward.config import RenderConfig
from steward.schema import MomentKind, Step, StepTag, Trajectory

CHARS_PER_TOKEN = 3.2

TAG_PRIORITY: tuple[MomentKind, ...] = (
    "user_interruption",
    "oversight_control",
    "evaluation_rules",
    "persistent_process",
    "irreversible_action",
    "switch_point",
    "information_boundary",
    "shared_resource",
    "effort_shortcut",
    "user_report",
    "other",
)

KIND_LABEL = {
    "user": "USER",
    "agent_text": "AGENT",
    "action": "ACTION",
    "result": "RESULT",
    "system": "SYSTEM",
}


def estimate_tokens(text: str) -> int:
    """Conservative token estimate; Claude tokenizers average about 3.5 characters per token."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def one_line(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def head_tail(text: str, head: int, tail: int) -> str:
    if len(text) <= head + tail:
        return text
    omitted = len(text) - head - tail
    return f"{text[:head]}\n... [{omitted} characters omitted] ...\n{text[-tail:]}"


def step_line(step: Step, body: str) -> str:
    label = KIND_LABEL[step.kind]
    if step.kind == "action":
        label = f"ACTION {step.tool or 'tool'}:"
    elif step.is_interruption:
        label = f"{label} (interruption)"
    return f"[step {step.index}] {label} {body}"


def metadata_block(trajectory: Trajectory) -> str:
    fields = {
        "trajectory_id": trajectory.trajectory_id,
        "source": trajectory.source,
        "domain": trajectory.domain,
        "real_user": trajectory.real_user,
        "history_author": trajectory.history_author,
        "harness": trajectory.harness,
        "repo": trajectory.repo,
        "base_commit": trajectory.base_commit,
        "task_env": trajectory.task_env,
        "steps": len(trajectory.steps),
        "agent_actions": trajectory.agent_actions(),
        "user_messages": trajectory.user_messages(),
    }
    lines = [f"{key}: {value}" for key, value in fields.items() if value is not None]
    return "METADATA\n" + "\n".join(lines)


def tags_block(tags: Sequence[StepTag]) -> str:
    by_kind: dict[str, list[int]] = {}
    for tag in tags:
        by_kind.setdefault(tag.kind, []).append(tag.step)
    if not by_kind:
        return "PATTERN TAGS\nnone"
    lines = []
    for kind, steps in sorted(by_kind.items()):
        shown = ", ".join(str(s) for s in steps[:30])
        more = f" (+{len(steps) - 30} more)" if len(steps) > 30 else ""
        lines.append(f"{kind}: steps {shown}{more}")
    return "PATTERN TAGS\n" + "\n".join(lines)


def triage_summary(trajectory: Trajectory, tags: Sequence[StepTag], token_budget: int) -> str:
    """Metadata, every user message, every action without results, and the Stage A tags.

    Text is shortened in passes until the summary fits the budget: user messages first get a
    per-message cap, then action lines are shortened, then middle actions are elided.
    """
    header = metadata_block(trajectory) + "\n\n" + tags_block(tags)
    users = [s for s in trajectory.steps if s.kind == "user"]
    actions = [s for s in trajectory.steps if s.kind == "action"]
    budget_chars = int(token_budget * CHARS_PER_TOKEN)

    for user_cap, action_cap, keep_actions in (
        (1500, 160, None),
        (600, 120, None),
        (300, 100, None),
        (300, 80, 120),
        (200, 70, 80),
        (120, 60, 50),
    ):
        user_lines = [step_line(s, one_line(s.content, user_cap)) for s in users]
        shown = actions
        elided = 0
        if keep_actions is not None and len(actions) > keep_actions:
            half = keep_actions // 2
            shown = actions[:half] + actions[-half:]
            elided = len(actions) - keep_actions
        action_lines = [step_line(s, one_line(s.content, action_cap)) for s in shown]
        if elided:
            action_lines.insert(len(action_lines) // 2, f"... [{elided} actions omitted] ...")
        text = "\n\n".join(
            [
                header,
                "USER MESSAGES\n" + "\n".join(user_lines),
                "ACTIONS\n" + "\n".join(action_lines),
            ]
        )
        if len(text) <= budget_chars:
            return text
    return text[:budget_chars]


def candidate_steps(
    tags: Sequence[StepTag],
    best_steps: Sequence[int],
    n_steps: int,
    max_windows: int,
    min_gap: int,
) -> list[int]:
    """Stage B best steps first, then Stage A tags by kind priority, one per window."""
    priority = {kind: rank for rank, kind in enumerate(TAG_PRIORITY)}
    ordered = [s for s in best_steps if 0 <= s < n_steps]
    ordered += [t.step for t in sorted(tags, key=lambda t: (priority.get(t.kind, 99), t.step))]
    chosen: list[int] = []
    for step in ordered:
        if all(abs(step - other) >= min_gap for other in chosen):
            chosen.append(step)
        if len(chosen) == max_windows:
            break
    return sorted(chosen)


def window_lines(steps: Sequence[Step], config: RenderConfig) -> list[str]:
    lines = []
    for step in steps:
        body = step.content
        if step.kind == "result":
            body = head_tail(body, config.result_head_chars, config.result_tail_chars)
        lines.append(step_line(step, body))
    return lines


def windowed_rendering(
    trajectory: Trajectory,
    candidates: Sequence[int],
    config: RenderConfig,
    token_cap: int,
    prefix: str = "",
) -> str:
    """Render metadata, a history summary and up to three full-step windows within the cap."""
    steps = trajectory.steps
    if not candidates:
        candidates = [min(len(steps) - 1, len(steps) // 2)] if steps else []
    spans: list[tuple[int, int]] = []
    for step in sorted(candidates)[: config.max_windows]:
        start = max(0, step - config.window_before)
        end = min(len(steps) - 1, step + config.window_after)
        if spans and start <= spans[-1][1] + 1:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
    first_window = spans[0][0] if spans else len(steps)
    in_window = {i for start, end in spans for i in range(start, end + 1)}
    budget_chars = int(token_cap * CHARS_PER_TOKEN)

    def compose(user_cap: int, action_cap: int, n_spans: int, result_chars: int) -> str:
        users = [
            step_line(s, s.content if user_cap <= 0 else head_tail(s.content, user_cap, 200))
            for s in steps
            if s.kind == "user" and s.index not in in_window
        ]
        earlier = [
            step_line(s, one_line(s.content, action_cap))
            for s in steps[:first_window]
            if s.kind == "action"
        ]
        window_config = config.model_copy(
            update={"result_head_chars": result_chars, "result_tail_chars": result_chars}
        )
        blocks = [prefix + metadata_block(trajectory)] if prefix else [metadata_block(trajectory)]
        blocks.append(
            "HISTORY SUMMARY\nUser messages outside the windows:\n"
            + ("\n".join(users) or "none")
            + "\n\nActions before the first window:\n"
            + ("\n".join(earlier) or "none")
        )
        for number, (start, end) in enumerate(spans[:n_spans], start=1):
            body = "\n".join(window_lines(steps[start : end + 1], window_config))
            blocks.append(f"WINDOW {number} (steps {start} to {end})\n{body}")
        return "\n\n".join(blocks)

    attempts = [
        (0, 200, len(spans), config.result_head_chars),
        (3000, 160, len(spans), config.result_head_chars),
        (1200, 120, len(spans), 250),
        (600, 90, len(spans), 150),
        (400, 70, max(1, len(spans) - 1), 150),
        (300, 60, 1, 100),
    ]
    for user_cap, action_cap, n_spans, result_chars in attempts:
        text = compose(user_cap, action_cap, n_spans, result_chars)
        if len(text) <= budget_chars:
            return text
    return text[:budget_chars]
