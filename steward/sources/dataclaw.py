"""DataClaw: personal coding-agent session exports on Hugging Face (tag `dataclaw`).

Covers the canonical copy of each tagged dataset under data/raw/dataclaw/<owner>__<name>/ and the
merged sessions in alexshengzhili/dataclaw-harbor-candidates (data/raw/dataclaw_harbor). Sessions
repeat across datasets, so they are deduplicated by session id and by a hash of their steps,
keeping the first seen: datasets in sorted order, then the harbor archive.
"""

from __future__ import annotations

import json
import logging
import re
import tarfile
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel

from steward.config import Settings
from steward.schema import Trajectory
from steward.sources.base import Source, SourceInfo
from steward.sources.domain_rule import assign_domain
from steward.sources.hf import download_hf
from steward.sources.hf_card import card_license
from steward.sources.session_export_formats import (
    clean_model,
    infer_harness,
    parse_session_messages,
    steps_hash,
)

log = logging.getLogger(__name__)

HARBOR_REPO = "alexshengzhili/dataclaw-harbor-candidates"
HARBOR_DIR = "dataclaw_harbor"
HARBOR_ARCHIVE = "sessions_raw.tar.gz"
HARBOR_TABLES = ("data/candidates.parquet", "data/all_screened.parquet")

DATASETS = (
    "A99311/my-dataclaw-data",
    "Batman787/dataclaw-Batman787",
    "DJTRIXUK/dataclaw-DJTRIXUK",
    "GolienHzmsr/dataclaw-GolienHzmsr",
    "MRiabov/dataclaw-march-26",
    "REXX-NEW/my-personal-codex-data",
    "akenove/my-personal-codex-data",
    "gutenbergpbc/john-masterclass-cc",
    "masterda/my-personal-codex-data",
    "michaelwaves/my-personal-codex-data",
    "nixjoe/nes-cpu",
    "nixjoe/nes-cpu-260316",
    "nixjoe/new-cpu-260308",
    "nixjoe/new-cpu-260310",
    "nixjoe/vue2egg-260310",
    "parani01/dataclaw-parani01",
    "peteromallet/dataclaw-peteromallet",
    "peteromallet/my-personal-codex-data",
    "sinhaankur/my-personal-codex-data",
    "sunsun123new/dataclaw-sunsun123new",
    "tillg/dataclaw-tillg",
    "vaynelee/dataclaw-vaynelee",
    "woctordho/dataclaw",
    "woctordho/dataclaw-windows",
    "wop/my-personal-codex-data",
    "wuuski/my-personal-codex-data",
    "xuechengjiang/my-personal-codex-data",
    "zhiyaowang/dataclaw-zhiyaowang",
)

GITHUB_SLUG = re.compile(r"^[\w.-]+/[\w.-]+$")
SECRET_NAME = re.compile(r"key|token|secret|password", re.IGNORECASE)
RECORD_FIELDS = ("project", "git_branch", "start_time", "end_time", "source")


class SessionRecord(BaseModel):
    """One exported session with where it came from."""

    dataset: str
    license: str | None
    record: dict[str, Any]


class RepoInfo(BaseModel):
    github_repo: str
    stars_approx: int | None = None
    harbor_candidate: bool = False


def dataset_dir_name(dataset: str) -> str:
    return dataset.replace("/", "__", 1)


def dataset_from_dir(name: str) -> str:
    return name.replace("__", "/", 1)


def _safe_small(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if not SECRET_NAME.search(str(k))}
    return value


def read_dataset(path: Path, skipped: Counter[str]) -> Iterator[SessionRecord]:
    dataset = dataset_from_dir(path.name)
    license_id = card_license(path / "README.md")
    files = sorted(p for p in path.rglob("*.jsonl") if ".cache" not in p.parts)
    for file in files:
        with file.open(errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    skipped["unparsable_line"] += 1
                    continue
                if isinstance(record, dict):
                    yield SessionRecord(dataset=dataset, license=license_id, record=record)


def read_harbor(path: Path, skipped: Counter[str]) -> Iterator[SessionRecord]:
    """Sessions from the harbor archive, read in archive order to avoid seeking in the gzip."""
    archive = path / HARBOR_ARCHIVE
    if not archive.is_file():
        return
    license_id = card_license(path / "README.md")
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            if not member.isfile() or not member.name.endswith(".json"):
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            try:
                record = json.load(handle)
            except (json.JSONDecodeError, UnicodeDecodeError):
                skipped["unparsable_file"] += 1
                continue
            if isinstance(record, dict):
                yield SessionRecord(dataset=HARBOR_REPO, license=license_id, record=record)


def harbor_repos(path: Path) -> dict[str, RepoInfo]:
    """GitHub repo per session id from the harbor screening tables; candidates take priority."""
    lookup: dict[str, RepoInfo] = {}
    for table in HARBOR_TABLES:
        file = path / table
        if not file.is_file():
            continue
        frame = pd.read_parquet(file, columns=["session_id", "github_repo", "stars_approx"])
        for row in frame.to_dict("records"):
            session_id = str(row["session_id"])
            repo = row.get("github_repo")
            if session_id in lookup or not isinstance(repo, str) or not GITHUB_SLUG.match(repo):
                continue
            stars = row.get("stars_approx")
            lookup[session_id] = RepoInfo(
                github_repo=repo,
                stars_approx=int(stars) if pd.notna(stars) else None,
                harbor_candidate=table == HARBOR_TABLES[0],
            )
    return lookup


def export_family(record: dict[str, Any]) -> str:
    """'A' for the older export whose tool inputs are flattened strings without outputs, else 'B'."""
    for message in record.get("messages") or []:
        for use in (message.get("tool_uses") or []) if isinstance(message, dict) else []:
            if isinstance(use, dict):
                return "A" if isinstance(use.get("input"), str) and "output" not in use else "B"
    return "B"


class DataClaw(Source):
    info = SourceInfo(
        name="dataclaw",
        domain="swe",
        real_user=True,
        location="hf:tag dataclaw (28 canonical datasets) + hf:" + HARBOR_REPO,
        license="mit",
        notes="License is read per dataset from its card and recorded on each trajectory.",
    )

    def download(self, settings: Settings) -> None:
        for dataset in DATASETS:
            download_hf(dataset, self.raw_dir(settings) / dataset_dir_name(dataset))
        download_hf(
            HARBOR_REPO,
            settings.paths.raw / HARBOR_DIR,
            ["README.md", "PLAN.md", HARBOR_ARCHIVE, *HARBOR_TABLES],
        )

    def records(self, settings: Settings, skipped: Counter[str]) -> Iterator[SessionRecord]:
        raw = self.raw_dir(settings)
        if raw.is_dir():
            for path in sorted(p for p in raw.iterdir() if p.is_dir()):
                yield from read_dataset(path, skipped)
        yield from read_harbor(settings.paths.raw / HARBOR_DIR, skipped)

    def load(self, settings: Settings) -> Iterator[Trajectory]:
        repos = harbor_repos(settings.paths.raw / HARBOR_DIR)
        seen_ids: set[str] = set()
        seen_hashes: set[str] = set()
        skipped: Counter[str] = Counter()
        for item in self.records(settings, skipped):
            trajectory = self.to_trajectory(item, repos, seen_ids, seen_hashes, skipped)
            if trajectory is not None:
                yield trajectory
        log.info("dataclaw skipped: %s", dict(skipped))

    def to_trajectory(
        self,
        item: SessionRecord,
        repos: dict[str, RepoInfo],
        seen_ids: set[str],
        seen_hashes: set[str],
        skipped: Counter[str],
    ) -> Trajectory | None:
        record = item.record
        session_id = record.get("session_id")
        if not isinstance(session_id, str) or not session_id or "REDACTED" in session_id:
            skipped["no_session_id"] += 1
            return None
        if session_id in seen_ids:
            skipped["duplicate_id"] += 1
            return None
        steps = parse_session_messages(record.get("messages") or [])
        if not steps:
            skipped["no_steps"] += 1
            return None
        seen_ids.add(session_id)
        digest = steps_hash(steps)
        if digest in seen_hashes:
            skipped["duplicate_content"] += 1
            return None
        seen_hashes.add(digest)
        source = record.get("source") if isinstance(record.get("source"), str) else None
        repo_info = repos.get(session_id)
        domain = assign_domain(steps)
        meta: dict[str, Any] = {field: record.get(field) for field in RECORD_FIELDS}
        meta.update(
            {
                "dataset": item.dataset,
                "upstream_hf_repo": record.get("_hf_repo"),
                "donor": record.get("_donor"),
                "model": record.get("model"),
                "stats": _safe_small(record.get("stats")),
                "export_family": export_family(record),
                "content_hash": digest,
                "stars_approx": repo_info.stars_approx if repo_info else None,
                "harbor_candidate": repo_info.harbor_candidate if repo_info else False,
            }
        )
        return Trajectory(
            trajectory_id=f"dataclaw:{session_id}",
            source="dataclaw",
            domain=domain or "swe",
            domain_decided_by_rule=domain is not None,
            real_user=True,
            history_author=clean_model(record.get("model")),
            harness=infer_harness(source, steps),
            repo=repo_info.github_repo if repo_info else None,
            base_commit=None,
            task_env=None,
            license=item.license,
            steps=steps,
            meta=json.loads(json.dumps(meta, default=str)),
        )
