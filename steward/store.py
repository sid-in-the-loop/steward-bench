"""Read and write normalized trajectories and JSONL stage outputs."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from pathlib import Path

import pandas as pd
from pydantic import BaseModel

from steward.schema import Step, Trajectory, TrajectoryMeta

META_FILE = "meta.parquet"
STEPS_FILE = "steps.jsonl"
DONE_FILE = "_DONE"


def to_meta(trajectory: Trajectory) -> TrajectoryMeta:
    return TrajectoryMeta(
        trajectory_id=trajectory.trajectory_id,
        source=trajectory.source,
        domain=trajectory.domain,
        real_user=trajectory.real_user,
        history_author=trajectory.history_author,
        harness=trajectory.harness,
        repo=trajectory.repo,
        base_commit=trajectory.base_commit,
        task_env=trajectory.task_env,
        license=trajectory.license,
        domain_decided_by_rule=trajectory.domain_decided_by_rule,
        n_steps=len(trajectory.steps),
        agent_actions=trajectory.agent_actions(),
        user_messages=trajectory.user_messages(),
        meta_json=json.dumps(trajectory.meta, default=str, sort_keys=True),
    )


def source_dir(normalized_root: Path, source: str) -> Path:
    return normalized_root / source


def is_written(normalized_root: Path, source: str) -> bool:
    return (source_dir(normalized_root, source) / DONE_FILE).exists()


def write_source(normalized_root: Path, source: str, trajectories: Iterable[Trajectory]) -> int:
    """Write one source atomically: steps stream to a temp file, then everything is renamed."""
    out = source_dir(normalized_root, source)
    out.mkdir(parents=True, exist_ok=True)
    steps_tmp = out / f"{STEPS_FILE}.tmp"
    metas: list[dict[str, object]] = []
    seen: set[str] = set()
    with steps_tmp.open("w") as handle:
        for trajectory in trajectories:
            if trajectory.trajectory_id in seen:
                continue
            seen.add(trajectory.trajectory_id)
            metas.append(to_meta(trajectory).model_dump())
            for step in trajectory.steps:
                record = {"trajectory_id": trajectory.trajectory_id, **step.model_dump(mode="json")}
                handle.write(json.dumps(record) + "\n")
    meta_tmp = out / f"{META_FILE}.tmp"
    columns = list(TrajectoryMeta.model_fields)
    pd.DataFrame(metas, columns=columns).to_parquet(meta_tmp, index=False)
    os.replace(steps_tmp, out / STEPS_FILE)
    os.replace(meta_tmp, out / META_FILE)
    (out / DONE_FILE).write_text(f"{len(metas)}\n")
    return len(metas)


def read_meta(normalized_root: Path, source: str) -> list[TrajectoryMeta]:
    frame = pd.read_parquet(source_dir(normalized_root, source) / META_FILE)
    records = frame.astype(object).where(frame.notna(), None).to_dict("records")
    return [TrajectoryMeta.model_validate(record) for record in records]


def iter_trajectories(
    normalized_root: Path, source: str, only: set[str] | None = None
) -> Iterator[Trajectory]:
    """Stream trajectories of one source, optionally restricted to a set of ids."""
    metas = {m.trajectory_id: m for m in read_meta(normalized_root, source)}
    current_id: str | None = None
    steps: list[Step] = []

    def build(trajectory_id: str) -> Trajectory:
        meta = metas[trajectory_id]
        return Trajectory(
            trajectory_id=meta.trajectory_id,
            source=meta.source,
            domain=meta.domain,
            real_user=meta.real_user,
            history_author=meta.history_author,
            harness=meta.harness,
            repo=meta.repo,
            base_commit=meta.base_commit,
            task_env=meta.task_env,
            license=meta.license,
            domain_decided_by_rule=meta.domain_decided_by_rule,
            steps=steps,
            meta=json.loads(meta.meta_json),
        )

    with (source_dir(normalized_root, source) / STEPS_FILE).open() as handle:
        for line in handle:
            record = json.loads(line)
            trajectory_id = record.pop("trajectory_id")
            if only is not None and trajectory_id not in only:
                continue
            if trajectory_id != current_id:
                if current_id is not None:
                    yield build(current_id)
                current_id = trajectory_id
                steps = []
            steps.append(Step.model_validate(record))
    if current_id is not None:
        yield build(current_id)


def written_sources(normalized_root: Path) -> list[str]:
    if not normalized_root.exists():
        return []
    return sorted(p.name for p in normalized_root.iterdir() if (p / DONE_FILE).exists())


def read_jsonl[M: BaseModel](path: Path, model: type[M]) -> list[M]:
    if not path.exists():
        return []
    with path.open() as handle:
        return [model.model_validate_json(line) for line in handle if line.strip()]


def append_jsonl(path: Path, records: Iterable[BaseModel]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("a") as handle:
        for record in records:
            handle.write(record.model_dump_json() + "\n")
            count += 1
    return count


def write_jsonl(path: Path, records: Iterable[BaseModel]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    count = 0
    with tmp.open("w") as handle:
        for record in records:
            handle.write(record.model_dump_json() + "\n")
            count += 1
    os.replace(tmp, path)
    return count
