"""TheAgentCompany experiments: agent runs on 175 simulated-company tasks per submission."""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from steward.config import Settings
from steward.schema import Step, Trajectory
from steward.sources.base import Source, SourceInfo
from steward.sources.git_sparse import sparse_checkout
from steward.sources.json_files import json_files_by_stem, read_json
from steward.sources.openhands_events import parse_openhands_events
from steward.sources.the_agent_company_formats import parse_muse_text, parse_owl_turns

log = logging.getLogger(__name__)

NAME = "the_agent_company"
REPO_URL = "https://github.com/TheAgentCompany/experiments"
COMMIT = "f9b1e9411fcb554e76509a0e1bc8fe7f8b0e69d6"
SPARSE_PATTERNS = [
    "/README.md",
    "/evaluation/*/*/README.md",
    "/evaluation/*/*/trajectories/",
    "/evaluation/*/*/results/",
]
SUBMISSION_NAME = re.compile(
    r"^\d{8}_(?P<harness>OpenHands-Versa|OpenHands-[\d.]+|OWL-RolePlay|MUSE|TTE-MatrixAgent)"
    r"-(?P<model>.+)$"
)
HARNESS_NAMES = {
    "OpenHands-Versa": "openhands-versa",
    "OWL-RolePlay": "owl-roleplay",
    "MUSE": "muse",
    "TTE-MatrixAgent": "tte-matrixagent",
}
# The 20250729 submissions name the agent and environment (NPC) models together; their README
# says the agent is claude-3-7-sonnet-20250219 and the suffix is the environment model.
AGENT_MODEL_OVERRIDES = {
    "claude37-deepseekv3": ("claude-3-7-sonnet-20250219", "deepseekv3"),
    "claude37-gpt4o": ("claude-3-7-sonnet-20250219", "gpt4o"),
}


class Submission(BaseModel):
    name: str
    harness: str | None
    harness_version: str | None
    history_author: str | None
    environment_model: str | None


def parse_submission(name: str) -> Submission:
    match = SUBMISSION_NAME.match(name)
    if match is None:
        return Submission(
            name=name,
            harness=None,
            harness_version=None,
            history_author=None,
            environment_model=None,
        )
    harness_label, model = match.group("harness"), match.group("model")
    version = None
    if harness_label in HARNESS_NAMES:
        harness = HARNESS_NAMES[harness_label]
    else:
        harness, version = "openhands", harness_label.removeprefix("OpenHands-")
    author, environment = AGENT_MODEL_OVERRIDES.get(model, (model, None))
    return Submission(
        name=name,
        harness=harness,
        harness_version=version,
        history_author=author,
        environment_model=environment,
    )


def trajectory_files(directory: Path) -> dict[str, Path]:
    """Task name to trajectory file: JSON (gzipped or not) first, else the MUSE `.txt`."""
    files = json_files_by_stem(directory, "traj_")
    if directory.is_dir():
        for path in sorted(directory.glob("traj_*.txt")):
            files.setdefault(path.name.removeprefix("traj_").removesuffix(".txt"), path)
    return dict(sorted(files.items()))


def parse_trajectory_file(path: Path) -> tuple[list[Step], Counter[str]]:
    if path.suffix == ".txt":
        return parse_muse_text(path.read_text(errors="replace")), Counter()
    data = read_json(path)
    if not isinstance(data, list):
        raise ValueError("trajectory is not a list")
    if data and isinstance(data[0], dict) and "assistant" in data[0] and "id" not in data[0]:
        return parse_owl_turns(data), Counter()
    parsed = parse_openhands_events(data)
    return parsed.steps, parsed.models


def outcome(results: dict[str, Path], task: str) -> dict[str, Any]:
    path = results.get(task)
    if path is None:
        return {}
    try:
        value = read_json(path)
    except (OSError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in ("final_score", "checkpoints") if key in value}


class TheAgentCompany(Source):
    info = SourceInfo(
        name=NAME,
        domain="workplace",
        real_user=False,
        location=f"github:TheAgentCompany/experiments@{COMMIT}",
        license=None,
        notes=(
            "The experiments repo declares no license; the benchmark repo is MIT. Research use "
            "only. Screenshots and states/ are not downloaded."
        ),
    )

    def download(self, settings: Settings) -> None:
        sparse_checkout(REPO_URL, COMMIT, SPARSE_PATTERNS, self.raw_dir(settings) / "repo")

    def submissions(self, settings: Settings) -> list[Path]:
        root = self.raw_dir(settings) / "repo" / "evaluation"
        return sorted(path for path in root.glob("*/*") if path.is_dir())

    def load(self, settings: Settings) -> Iterator[Trajectory]:
        skipped = 0
        for directory in self.submissions(settings):
            submission = parse_submission(directory.name)
            results = json_files_by_stem(directory / "results", "eval_")
            for task, path in trajectory_files(directory / "trajectories").items():
                try:
                    steps, models = parse_trajectory_file(path)
                except (OSError, ValueError) as error:
                    log.warning("skipping %s: %s", path, error)
                    skipped += 1
                    continue
                if not steps:
                    skipped += 1
                    continue
                yield self.trajectory(submission, task, steps, models, outcome(results, task))
        if skipped:
            log.warning("%s: skipped %d unparsable or empty trajectories", NAME, skipped)

    def trajectory(
        self,
        submission: Submission,
        task: str,
        steps: list[Step],
        models: Counter[str],
        result: dict[str, Any],
    ) -> Trajectory:
        meta: dict[str, Any] = {"submission": submission.name, "task": task, **result}
        if submission.harness_version:
            meta["harness_version"] = submission.harness_version
        if submission.environment_model:
            meta["environment_model"] = submission.environment_model
        if models:
            meta["response_models"] = sorted(models)
        author = submission.history_author
        if author is None and models:
            author = models.most_common(1)[0][0]
        return Trajectory(
            trajectory_id=f"{NAME}:{submission.name}/{task}",
            source=NAME,
            domain="workplace",
            real_user=False,
            history_author=author,
            harness=submission.harness,
            task_env=f"theagentcompany:{task}",
            license=None,
            steps=steps,
            meta=meta,
        )
