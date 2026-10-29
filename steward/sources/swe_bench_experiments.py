"""SWE-bench Verified submissions from swe-bench/experiments: 12 model families x 150 instances."""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from steward.config import Settings
from steward.schema import Trajectory
from steward.sources.base import Source, SourceInfo
from steward.sources.swe_bench_download import SELECTED_FILE, download_submissions
from steward.sources.swe_bench_formats import (
    PARSERS,
    UNSUPPORTED_FORMATS,
    TrajFormat,
    detect_format,
    read_traj_file,
)

log = logging.getLogger(__name__)

NAME = "swe_bench_experiments"

SUBMISSIONS: dict[str, str] = {
    "20250511_sweagent_lm_32b": "verified",
    "20250520_openhands_devstral_small": "verified",
    "20250527_amazon.nova-premier-v1.0": "verified",
    "20250716_openhands_kimi_k2": "verified",
    "20250720_mini-v0.0.0-Llama-4-Maverick-17B-Instruct": "bash-only",
    "20250928_trae_doubao_seed_code": "verified",
    "20251120_livesweagent_gemini-3-pro-preview": "verified",
    "20251205_sonar-foundation-agent_claude-opus-4-5": "verified",
    "20260217_mini-v2.0.0_deepseek-3-2-high": "bash-only",
    "20260217_mini-v2.0.0_glm-5-high": "bash-only",
    "20260217_mini-v2.0.0_gpt-5-2-high": "bash-only",
    "20260217_mini-v2.0.0_minimax-2-5-high": "bash-only",
}

EXCLUDED: dict[str, str] = {
    "20251127_openhands_claude-opus-4-5": (
        "flattened OpenHands export with no tool calls and tool output under role assistant; "
        "a second Claude Opus 4.5 entry (Sonar) is used instead"
    ),
}

# The .traj file is SWE-agent output even where metadata.yaml names a different agent.
HARNESS_BY_FORMAT: dict[TrajFormat, str] = {"swe_agent": "swe-agent"}

TRAJ_CANDIDATES = (
    "{iid}.traj",
    "{iid}/{iid}.traj",
    "{iid}/{iid}.traj.json",
    "{iid}.json",
    "{iid}.log",
)


class SubmissionMeta(BaseModel):
    name: str
    models: list[str]
    agent: str | None
    display_name: str | None


def read_submission_meta(path: Path, name: str) -> SubmissionMeta:
    data: dict[str, Any] = {}
    if path.exists():
        data = yaml.safe_load(path.read_text()) or {}
    tags = data.get("tags") or {}
    info = data.get("info") or {}
    models = tags.get("model") or []
    return SubmissionMeta(
        name=name,
        models=[str(m) for m in (models if isinstance(models, list) else [models])],
        agent=str(tags["agent"]) if tags.get("agent") else None,
        display_name=str(info["name"]) if info.get("name") else None,
    )


def repo_from_instance(instance_id: str) -> str | None:
    """'scikit-learn__scikit-learn-12345' -> 'scikit-learn/scikit-learn'."""
    owner, sep, rest = instance_id.partition("__")
    if not sep:
        return None
    name = re.sub(r"-\d+$", "", rest)
    return f"{owner}/{name}"


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", value.lower()).strip("-")


def find_traj(trajs_dir: Path, instance_id: str) -> Path | None:
    for pattern in TRAJ_CANDIDATES:
        path = trajs_dir / pattern.format(iid=instance_id)
        if path.is_file():
            return path
    return None


def read_resolved(report_path: Path, instance_id: str) -> bool | None:
    """report.json is either {instance_id: {...}} or the inner dict itself."""
    if not report_path.exists():
        return None
    try:
        report = json.loads(report_path.read_text())
    except json.JSONDecodeError:
        return None
    if not isinstance(report, dict):
        return None
    inner = report.get(instance_id, report)
    resolved = inner.get("resolved") if isinstance(inner, dict) else None
    return resolved if isinstance(resolved, bool) else None


def exit_status(data: Any) -> str | None:
    """mini-SWE-agent and SWE-agent files keep the exit status under info."""
    info = data.get("info") if isinstance(data, dict) else None
    value = info.get("exit_status") if isinstance(info, dict) else None
    return str(value) if value else None


class SweBenchExperiments(Source):
    info = SourceInfo(
        name=NAME,
        domain="swe",
        real_user=False,
        location="github:swe-bench/experiments (metadata) + s3://swe-bench-submissions (trajs)",
        license=None,
        notes=(
            "12 SWE-bench Verified submissions from distinct model families x the same 150 "
            "instances (selected_instances.txt). Six trajectory formats, one parser each in "
            "swe_bench_formats.py. Excluded: 20251127_openhands_claude-opus-4-5, a flattened "
            "export without tool calls. No license declared; do not redistribute raw files. "
            "history_author is tags.model[0] from metadata.yaml as given (SWE-agent-LM-32B is "
            "listed there as 'Qwen 2.5'). base_commit needs a join to SWE-bench Verified."
        ),
    )

    def download(self, settings: Settings) -> None:
        download_submissions(
            self.raw_dir(settings), SUBMISSIONS, settings.sampling.swe_bench_instances
        )

    def load(self, settings: Settings) -> Iterator[Trajectory]:
        root = self.raw_dir(settings)
        instances = (root / SELECTED_FILE).read_text().split()
        counts: Counter[str] = Counter()
        for submission in sorted(SUBMISSIONS):
            meta = read_submission_meta(root / submission / "metadata.yaml", submission)
            for instance_id in instances:
                trajectory = self.load_one(root / submission, meta, instance_id, counts)
                if trajectory is not None:
                    counts["loaded"] += 1
                    yield trajectory
        log.info("%s: %s", NAME, dict(counts))

    def load_one(
        self, submission_dir: Path, meta: SubmissionMeta, instance_id: str, counts: Counter[str]
    ) -> Trajectory | None:
        path = find_traj(submission_dir / "trajs", instance_id)
        if path is None:
            counts["missing"] += 1
            return None
        try:
            data = read_traj_file(path)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            counts["unparsable"] += 1
            return None
        fmt = detect_format(data)
        if fmt is None or fmt in UNSUPPORTED_FORMATS:
            counts[f"unsupported:{fmt}"] += 1
            return None
        steps = PARSERS[fmt](data)
        if not steps:
            counts["empty"] += 1
            return None
        harness = HARNESS_BY_FORMAT.get(fmt) or (slug(meta.agent) if meta.agent else None)
        report = submission_dir / "logs" / instance_id / "report.json"
        native_id = f"{meta.name}/{instance_id}"
        return Trajectory(
            trajectory_id=f"{NAME}:{native_id}",
            source=NAME,
            domain="swe",
            real_user=False,
            history_author=meta.models[0] if meta.models else None,
            harness=harness,
            repo=repo_from_instance(instance_id),
            base_commit=None,
            task_env=f"swe-bench-verified:{instance_id}",
            license=None,
            steps=steps,
            meta={
                "submission": meta.name,
                "instance_id": instance_id,
                "format": fmt,
                "resolved": read_resolved(report, instance_id),
                "exit_status": exit_status(data),
                "models": meta.models,
                "agent": meta.agent,
                "display_name": meta.display_name,
            },
        )
