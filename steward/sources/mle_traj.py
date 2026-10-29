"""MLE trajectories on 7 Kaggle competitions: human kernels and agent runs (HF jerryyan/mle-traj-v1).

The data are code-version snapshots, not chat. data/state.parquet lists each trajectory's
versions in order (for MLEvolve branches, orig_version_number points at the run's version
file). Each version becomes an action step (tool "edit", content the unified diff from the
previous version) followed by a result step with what the author saw after saving or running
that version: Kaggle public and private scores and run time for humans, the run's own score
tag for Codex, the CV metric and bug flag for MLEvolve. MLEvolve node plans, written by the
agent, become agent_text before the action. The gpt-5-mini annotations (goal_nl,
diff_summary, tags) are not authored by the trajectory's author and are not turned into steps.
"""

from __future__ import annotations

import difflib
import json
import logging
import math
import re
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel

from steward.config import Settings
from steward.schema import Trajectory
from steward.sources.base import Source, SourceInfo
from steward.sources.hf import download_hf
from steward.sources.transcripts import StepBuilder, parse_timestamp

log = logging.getLogger(__name__)

NAME = "mle_traj"
REPO_ID = "jerryyan/mle-traj-v1"
HUMAN_GROUPS = ("Grandmaster", "Master", "Expert", "Contributor")
RUN_MODEL = re.compile(r"^run_\d{8}_\d{6}_(.+?)_run\d+")

Author = Literal["human", "codex", "mlevolve"]


class VersionRef(BaseModel):
    version_number: int
    file_number: int


class TrajectoryRef(BaseModel):
    key_id: str
    comp: str
    group: str
    is_best_branch: bool
    min_score: float | None
    max_score: float | None
    versions: list[VersionRef]

    @property
    def author(self) -> Author:
        if self.group in HUMAN_GROUPS:
            return "human"
        return "codex" if self.group == "codex" else "mlevolve"


def notebook_to_text(raw: str) -> str:
    """Code and markdown cells in jupytext percent format; outputs are already stripped."""
    notebook = json.loads(raw)
    parts: list[str] = []
    for cell in notebook.get("cells") or []:
        source = cell.get("source") or ""
        text = "".join(source) if isinstance(source, list) else str(source)
        if cell.get("cell_type") == "code":
            parts.append(f"# %%\n{text}")
        elif cell.get("cell_type") == "markdown":
            commented = "\n".join(f"# {line}" if line else "#" for line in text.splitlines())
            parts.append(f"# %% [markdown]\n{commented}")
    return "\n\n".join(parts) + "\n"


def read_version(versions_dir: Path, number: int) -> tuple[str, str] | None:
    """(file name, source text) for version `number`, preferring .py over .ipynb."""
    for suffix in (".py", ".ipynb"):
        path = versions_dir / f"v{number:03d}{suffix}"
        if path.is_file():
            raw = path.read_text(errors="replace")
            return path.name, notebook_to_text(raw) if suffix == ".ipynb" else raw
    return None


def unified_diff(old: str, new: str, old_name: str, new_name: str) -> str:
    lines = difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True), old_name, new_name
    )
    return "".join(line if line.endswith("\n") else line + "\n" for line in lines)


def run_model(run_id: str) -> str | None:
    match = RUN_MODEL.match(run_id)
    return match.group(1) if match else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return None if math.isnan(value) else float(value)


def result_text(author: Author, version: dict[str, Any]) -> str:
    parts: list[str] = []
    score = version.get("linked_score")
    if author == "human":
        for board in ("public", "private"):
            if isinstance(score, dict) and _number(score.get(board)) is not None:
                parts.append(f"{board} score: {score[board]}")
        if _number(version.get("running_time_ms")) is not None:
            parts.append(f"run time: {int(version['running_time_ms']) / 1000:.0f}s")
    elif author == "codex" and isinstance(score, dict):
        if _number(score.get("public")) is not None:
            parts.append(f"score: {score['public']}")
    elif author == "mlevolve":
        if _number(score) is not None:
            parts.append(f"cv score: {score}")
        for flag in ("is_buggy", "is_valid"):
            if isinstance(version.get(flag), bool):
                parts.append(f"{flag}: {version[flag]}")
    return "; ".join(parts)


class MleTraj(Source):
    info = SourceInfo(
        name=NAME,
        domain="ai_rnd",
        real_user=True,
        location=f"hf:{REPO_ID}",
        license="cc-by-4.0",
        notes=(
            "622 trajectories on 7 Kaggle competitions: 422 human Kaggle kernels, 11 Codex "
            "runs and 189 MLEvolve root-to-leaf branches. Snapshots, not chat: each version is "
            "an 'edit' action whose content is the unified diff from the previous version, "
            "then a result with the scores the author saw (Kaggle public/private and run time "
            "for humans, the run's score tag for Codex, CV metric and bug flags for MLEvolve). "
            "MLEvolve plans become agent_text. real_user is True only for human kernels "
            "(history_author 'human', harness 'kaggle-kernel'); agent runs are real_user "
            "False with the model from the run id. Human kernels keep their own Kaggle "
            "licenses (Apache-2.0 and similar), annotations and agent runs are CC-BY-4.0. "
            "Human kernels have no user turns and no execution output, so they fail Stage A's "
            "real-user message minimum."
        ),
    )

    def download(self, settings: Settings) -> None:
        download_hf(REPO_ID, self.raw_dir(settings))

    def trajectory_refs(self, root: Path) -> list[TrajectoryRef]:
        index = pd.read_parquet(root / "data" / "trajectory_index.parquet")
        state = pd.read_parquet(
            root / "data" / "state.parquet",
            columns=["key_id", "version_number", "orig_version_number"],
        )
        versions: dict[str, list[VersionRef]] = {}
        state = state.sort_values(["key_id", "version_number"])
        for key_id, version_number, orig_number in zip(
            state["key_id"].tolist(),
            state["version_number"].tolist(),
            state["orig_version_number"].tolist(),
            strict=True,
        ):
            orig = _number(orig_number)
            number = int(version_number)
            versions.setdefault(str(key_id), []).append(
                VersionRef(version_number=number, file_number=int(orig) if orig else number)
            )
        refs = []
        for row in index.sort_values("key_id").itertuples(index=False):
            refs.append(
                TrajectoryRef(
                    key_id=str(row.key_id),
                    comp=str(row.comp),
                    group=str(row.group),
                    is_best_branch=bool(row.is_best_branch),
                    min_score=_number(row.min_score),
                    max_score=_number(row.max_score),
                    versions=versions.get(str(row.key_id), []),
                )
            )
        return refs

    def run_dir(self, root: Path, ref: TrajectoryRef) -> Path:
        if ref.author == "human":
            return root / "trajectories" / "human" / "kernels" / ref.key_id
        if ref.author == "codex":
            return root / "trajectories" / "codex" / ref.key_id
        # MLEvolve branch keys are mlev__<run_id>__<competition>__branch<N>.
        run = ref.key_id.removeprefix("mlev__").rsplit("__", 2)[0]
        return root / "trajectories" / "mlevolve" / run

    def load(self, settings: Settings) -> Iterator[Trajectory]:
        root = self.raw_dir(settings)
        competitions = json.loads((root / "manifests" / "competitions.json").read_text())
        inventory = json.loads((root / "trajectories" / "human" / "inventory.json").read_text())
        kernels = {str(k["kernel_id"]): k for k in inventory.get("kernels") or []}
        counts: Counter[str] = Counter()
        for ref in self.trajectory_refs(root):
            trajectory = self.build(root, ref, competitions.get(ref.comp) or {}, kernels, counts)
            if trajectory is not None:
                counts[f"loaded:{ref.author}"] += 1
                yield trajectory
        log.info("%s: %s", NAME, dict(counts))

    def build(
        self,
        root: Path,
        ref: TrajectoryRef,
        competition: dict[str, Any],
        kernels: dict[str, dict[str, Any]],
        counts: Counter[str],
    ) -> Trajectory | None:
        run_dir = self.run_dir(root, ref)
        traj_path = run_dir / "trajectory.json"
        if not traj_path.exists() or not ref.versions:
            counts["missing_trajectory"] += 1
            return None
        run = json.loads(traj_path.read_text())
        by_number = {int(v["version_number"]): v for v in run.get("versions") or []}
        builder = StepBuilder()
        if competition:
            builder.add(
                "system",
                f"Kaggle competition {ref.comp}: {competition.get('name')}. "
                f"Metric: {competition.get('metric')} ({competition.get('score_direction')} "
                "is better).",
            )
        previous_name, previous_text = "/dev/null", ""
        for version in ref.versions:
            loaded = read_version(run_dir / "versions", version.file_number)
            if loaded is None:
                counts["missing_version_file"] += 1
                return None
            name, text = loaded
            record = by_number.get(version.file_number) or {}
            timestamp = parse_timestamp(record.get("date") or record.get("ctime"))
            plan = record.get("plan")
            if ref.author == "mlevolve" and isinstance(plan, str):
                builder.add("agent_text", plan, timestamp=timestamp)
            builder.add(
                "action",
                unified_diff(previous_text, text, previous_name, name),
                tool="edit",
                tool_input={
                    "path": f"versions/{name}",
                    "version": version.version_number,
                    "lines_changed": record.get("lines_changed_from_prev"),
                },
                timestamp=timestamp,
            )
            outcome = result_text(ref.author, record)
            if outcome:
                builder.add("result", outcome, timestamp=timestamp)
            previous_name, previous_text = name, text
        return Trajectory(
            trajectory_id=f"{NAME}:{ref.key_id}",
            source=NAME,
            domain="ai_rnd",
            real_user=ref.author == "human",
            history_author="human" if ref.author == "human" else run_model(run_dir.name),
            harness="kaggle-kernel" if ref.author == "human" else ref.author,
            repo=None,
            base_commit=None,
            task_env=f"kaggle:{ref.comp}",
            license=self.license_for(ref, kernels),
            steps=builder.steps,
            meta=self.meta_for(ref, kernels),
        )

    def license_for(self, ref: TrajectoryRef, kernels: dict[str, dict[str, Any]]) -> str:
        if ref.author == "human":
            value = (kernels.get(ref.key_id) or {}).get("license")
            return str(value) if value else "apache-2.0"
        return "cc-by-4.0"

    def meta_for(self, ref: TrajectoryRef, kernels: dict[str, dict[str, Any]]) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "key_id": ref.key_id,
            "competition": ref.comp,
            "group": ref.group,
            "author_kind": ref.author,
            "n_versions": len(ref.versions),
            "min_heldout_score": ref.min_score,
            "max_heldout_score": ref.max_score,
        }
        if ref.author == "mlevolve":
            meta["is_best_branch"] = ref.is_best_branch
        kernel = kernels.get(ref.key_id)
        if ref.author == "human" and kernel:
            for field in (
                "author_tier",
                "kernel_id",
                "medal",
                "percentile",
                "license_verified_via",
            ):
                meta[field] = kernel.get(field)
        return meta
