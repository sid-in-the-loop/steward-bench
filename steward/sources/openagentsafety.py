"""OpenAgentSafety: OpenHands runs of five models on safety-probing workplace tasks."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from steward.config import Settings
from steward.schema import Trajectory
from steward.sources.base import Source, SourceInfo
from steward.sources.git_sparse import sparse_checkout
from steward.sources.json_files import json_files_by_stem, json_stem, read_json
from steward.sources.openhands_events import parse_openhands_events

log = logging.getLogger(__name__)

NAME = "openagentsafety"
REPO_URL = "https://github.com/sani903/OpenAgentSafety"
COMMIT = "857798ff26c4ce6e1d7911ee4d8b3f84a71184e0"
SPARSE_PATTERNS = [
    "/README.md",
    "/LICENSE",
    "/evaluation/*/*.json",
    "/workspaces/tasks/*/task.md",
    "/workspaces/tasks/*/checkpoints.md",
]
# Model directory to the model name the responses report (the README names the same models).
MODEL_BY_DIR = {
    "claude-sonnet": "claude-3-7-sonnet-20250219",
    "deepseek_r1": "deepseek-reasoner",
    "deepseek_v3": "deepseek-chat",
    "gpt-4o": "gpt-4o-2024-08-06",
    "o3-mini": "o3-mini-2025-01-31",
}
SAFETY_RESULTS_SUFFIX = "_safety_eval_results"
LABEL_VALUE = re.compile(r"(-?\d+)\s*$")


def safety_labels(directory: Path) -> dict[str, dict[str, Any]]:
    """Task name to `{llm_label, llm_label_value, rule_based_score}` from the judge file."""
    for path in sorted(directory.iterdir()):
        stem = json_stem(path)
        if stem is None or not stem.endswith(SAFETY_RESULTS_SUFFIX):
            continue
        data = read_json(path)
        if not isinstance(data, dict):
            return {}
        labels: dict[str, dict[str, Any]] = {}
        for task, entry in data.items():
            if not isinstance(entry, dict):
                continue
            raw = entry.get("llm_label")
            match = LABEL_VALUE.search(str(raw)) if raw is not None else None
            labels[task] = {
                "llm_label": raw,
                "llm_label_value": int(match.group(1)) if match else None,
                "rule_based_score": entry.get("rule_based_score"),
            }
        return labels
    return {}


def checker_outcome(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        value = read_json(path)
    except (OSError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in ("final_score", "checkpoints") if key in value}


class OpenAgentSafety(Source):
    info = SourceInfo(
        name=NAME,
        domain="workplace",
        real_user=False,
        location=f"github:sani903/OpenAgentSafety@{COMMIT}",
        license="MIT",
        notes="Screenshots and state files are not used. Colleagues are simulated NPCs (chat).",
    )

    def download(self, settings: Settings) -> None:
        sparse_checkout(REPO_URL, COMMIT, SPARSE_PATTERNS, self.raw_dir(settings) / "repo")

    def model_dirs(self, settings: Settings) -> list[Path]:
        root = self.raw_dir(settings) / "repo" / "evaluation"
        return sorted(path for path in root.iterdir() if path.is_dir()) if root.is_dir() else []

    def load(self, settings: Settings) -> Iterator[Trajectory]:
        skipped = 0
        for directory in self.model_dirs(settings):
            trajectories = json_files_by_stem(directory, "traj_")
            if not trajectories:
                continue
            checks = json_files_by_stem(directory, "eval_")
            labels = safety_labels(directory)
            for task, path in trajectories.items():
                try:
                    data = read_json(path)
                except (OSError, ValueError) as error:
                    log.warning("skipping %s: %s", path, error)
                    skipped += 1
                    continue
                parsed = parse_openhands_events(data if isinstance(data, list) else [])
                if not parsed.steps:
                    skipped += 1
                    continue
                meta: dict[str, Any] = {
                    "model_dir": directory.name,
                    "task": task,
                    **checker_outcome(checks.get(task)),
                }
                if task in labels:
                    meta["safety"] = labels[task]
                if parsed.models:
                    meta["response_models"] = sorted(parsed.models)
                author = MODEL_BY_DIR.get(directory.name)
                if author is None and parsed.models:
                    author = parsed.models.most_common(1)[0][0]
                yield Trajectory(
                    trajectory_id=f"{NAME}:{directory.name}/{task}",
                    source=NAME,
                    domain="workplace",
                    real_user=False,
                    history_author=author,
                    harness="openhands",
                    task_env=f"openagentsafety:{task}",
                    license="MIT",
                    steps=parsed.steps,
                    meta=meta,
                )
        if skipped:
            log.warning("%s: skipped %d unparsable or empty trajectories", NAME, skipped)
