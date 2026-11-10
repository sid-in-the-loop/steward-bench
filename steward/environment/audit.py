"""Feasibility audit: which sessions in the deep-read pool can be rebuilt."""

from __future__ import annotations

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from steward.config import Settings
from steward.environment.repository import ReplayReport, rebuild
from steward.filter.rank import best_candidate
from steward.filter.selection import load_by_ids
from steward.schema import StageC2Result, TrajectoryMeta
from steward.store import read_jsonl, write_jsonl

log = logging.getLogger(__name__)


def audit_path(settings: Settings) -> Path:
    return settings.paths.data / "phase2" / "repository_audit.jsonl"


def checkout_dir(settings: Settings, trajectory_id: str) -> Path:
    key = hashlib.sha1(trajectory_id.encode()).hexdigest()[:12]
    return settings.paths.data / "phase2" / "checkouts" / key


def repository_sessions(
    results: list[StageC2Result], metas: dict[str, TrajectoryMeta]
) -> list[tuple[StageC2Result, TrajectoryMeta, int]]:
    """Deep-read sessions that start from a repository and commit, with their takeover step."""
    sessions = []
    for result in results:
        meta = metas.get(result.trajectory_id)
        candidate = best_candidate(result)
        if meta and meta.repo and meta.base_commit and not meta.task_env and candidate:
            sessions.append((result, meta, candidate.step))
    return sessions


def audit_repositories(
    settings: Settings,
    results: list[StageC2Result],
    metas: dict[str, TrajectoryMeta],
    workers: int = 4,
) -> list[ReplayReport]:
    """Rebuild every repository session not yet audited; append to the audit file."""
    path = audit_path(settings)
    done = {r.trajectory_id: r for r in read_jsonl(path, ReplayReport)} if path.exists() else {}
    todo = [s for s in repository_sessions(results, metas) if s[0].trajectory_id not in done]

    def audit(session: tuple[StageC2Result, TrajectoryMeta, int]) -> ReplayReport:
        result, meta, step = session
        assert meta.repo and meta.base_commit
        trajectory = next(load_by_ids(settings, [result.trajectory_id]))
        destination = checkout_dir(settings, result.trajectory_id)
        report = rebuild(
            result.trajectory_id, meta.repo, meta.base_commit, trajectory.steps, step, destination
        )
        log.info(
            "audit %s: checked out %s, edits %d of %d applied",
            result.trajectory_id,
            report.checked_out,
            report.edits_applied,
            report.edits,
        )
        return report

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for report in pool.map(audit, todo):
            done[report.trajectory_id] = report
            write_jsonl(path, [done[key] for key in sorted(done)])
    return list(done.values())
