"""Stage A: free rule checks, moment-kind tags and the pre-score (brief 6.1)."""

from __future__ import annotations

import logging
import math
import re
from pathlib import Path

import pandas as pd

from steward.config import Settings
from steward.filter.tags import action_text, tag_steps
from steward.schema import StageAResult, Step, StepTag, Trajectory
from steward.store import iter_trajectories, written_sources

log = logging.getLogger(__name__)

WRITE_TOOLS = re.compile(
    r"^(write|edit|multiedit|multi_edit|str_replace|str_replace_editor|str_replace_based_edit_tool|"
    r"create|create_file|apply_patch|notebookedit|patch|edit_file|write_file|insert|replace|"
    r"file_editor|delete_file|move_file)$",
    re.IGNORECASE,
)
STATE_CHANGING_COMMAND = re.compile(
    r"(^|[\s;&|(])(git (commit|push|merge|rebase|reset|checkout -b|add|stash|apply|am|rm|mv|tag)|"
    r"rm |mv |cp |mkdir |touch |sed -i|tee |pip install|npm (install|i |publish|run build)|"
    r"yarn add|cargo (build|add)|make\b|docker |kubectl |terraform |chmod |chown |ln -s|"
    r"patch |curl -X (POST|PUT|DELETE|PATCH)|psql|sqlite3|INSERT |UPDATE |DELETE |CREATE |DROP )"
    r"|>>?\s*\S",
    re.IGNORECASE,
)
EDITOR_WRITE_COMMANDS = {"create", "str_replace", "insert", "undo_edit", "write"}


def is_state_changing(step: Step) -> bool:
    if step.kind != "action":
        return False
    tool = (step.tool or "").strip()
    if WRITE_TOOLS.match(tool):
        if tool.lower() in ("str_replace_editor", "file_editor", "str_replace_based_edit_tool"):
            command = str((step.tool_input or {}).get("command", ""))
            return command in EDITOR_WRITE_COMMANDS
        return True
    return bool(STATE_CHANGING_COMMAND.search(action_text(step)))


def agent_share(trajectory: Trajectory) -> float | None:
    """Share of committed lines written by the agent, where the source provides it."""
    value = trajectory.meta.get("agent_percentage")
    if value is None:
        return None
    share = float(value)
    return share / 100 if share > 1 else share


def first_failed_check(
    trajectory: Trajectory, actions: int, state_changes: int, settings: Settings
) -> str | None:
    thresholds = settings.stage_a
    if actions < thresholds.min_agent_actions:
        return "A1"
    if state_changes < thresholds.min_state_changes:
        return "A2"
    identifiable = bool(trajectory.task_env) or bool(trajectory.repo and trajectory.base_commit)
    if not identifiable:
        return "A3"
    if trajectory.real_user:
        if trajectory.user_messages() < thresholds.min_user_messages_real_user:
            return "A4"
        share = agent_share(trajectory)
        if share is not None and share < thresholds.min_agent_share:
            return "A4"
    if trajectory.meta.get("is_continuation"):
        return "A5"
    return None


def prescore(tags: list[StepTag], actions: int) -> float:
    kinds = {tag.kind for tag in tags}
    score = float(len(kinds))
    if "user_interruption" in kinds:
        score += 2
    if kinds & {"oversight_control", "evaluation_rules", "persistent_process"}:
        score += 1
    score += math.log2(max(actions, 1) / 40)
    return round(score, 4)


def evaluate(trajectory: Trajectory, settings: Settings) -> StageAResult:
    actions = trajectory.agent_actions()
    state_changes = sum(1 for step in trajectory.steps if is_state_changing(step))
    tags = tag_steps(trajectory.steps)
    failed = first_failed_check(trajectory, actions, state_changes, settings)
    return StageAResult(
        trajectory_id=trajectory.trajectory_id,
        source=trajectory.source,
        domain=trajectory.domain,
        passed=failed is None,
        failed_check=failed,
        agent_actions=actions,
        user_messages=trajectory.user_messages(),
        state_changing_actions=state_changes,
        tags=tags,
        prescore=prescore(tags, actions),
    )


def output_path(settings: Settings, source: str) -> Path:
    return settings.paths.stage_a / f"{source}.parquet"


def results_to_frame(results: list[StageAResult]) -> pd.DataFrame:
    rows = []
    for result in results:
        row = result.model_dump()
        row["tags"] = [tag.model_dump() for tag in result.tags]
        rows.append(row)
    return pd.DataFrame(rows, columns=list(StageAResult.model_fields))


def run_source(settings: Settings, source: str, force: bool = False) -> int:
    out = output_path(settings, source)
    normalized_done = settings.paths.normalized / source / "_DONE"
    if out.exists() and not force and out.stat().st_mtime >= normalized_done.stat().st_mtime:
        log.info("stage A %s: up to date, skipped", source)
        return 0
    results = [evaluate(t, settings) for t in iter_trajectories(settings.paths.normalized, source)]
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".parquet.tmp")
    results_to_frame(results).to_parquet(tmp, index=False)
    tmp.replace(out)
    passed = sum(r.passed for r in results)
    log.info("stage A %s: in %d, passed %d", source, len(results), passed)
    return len(results)


def run(settings: Settings, force: bool = False) -> None:
    for source in written_sources(settings.paths.normalized):
        run_source(settings, source, force=force)


def read_results(settings: Settings) -> list[StageAResult]:
    results: list[StageAResult] = []
    for path in sorted(settings.paths.stage_a.glob("*.parquet")):
        frame = pd.read_parquet(path)
        for row in frame.to_dict("records"):
            row["tags"] = [dict(tag) for tag in row["tags"]]
            row["failed_check"] = (
                row["failed_check"] if isinstance(row["failed_check"], str) else None
            )
            results.append(StageAResult.model_validate(row))
    return results
