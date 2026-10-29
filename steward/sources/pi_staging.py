"""Pi staging: pi coding agent sessions shared by the pi author (HF badlogicgames/pi-mono, pi-diff-review)."""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from steward.config import Settings
from steward.schema import Trajectory
from steward.sources.base import Source, SourceInfo
from steward.sources.domain_rule import assign_domain
from steward.sources.hf import download_hf
from steward.sources.hf_card import card_license
from steward.sources.transcripts import parse_pi, read_jsonl_records

log = logging.getLogger(__name__)

DATASETS = ("badlogicgames/pi-mono", "badlogicgames/pi-diff-review")
NON_SESSION_FILES = frozenset({"manifest.jsonl"})
CARD_REPO = re.compile(
    r"(?:Source git repo:|working on)\s*https://github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?[\s.]"
)


def card_repo(readme: Path) -> str | None:
    """The GitHub repo the sessions were recorded in, as stated on the dataset card."""
    if not readme.is_file():
        return None
    match = CARD_REPO.search(readme.read_text(errors="replace"))
    return match.group(1) if match else None


def session_header(records: list[dict[str, Any]]) -> dict[str, Any]:
    return next((r for r in records if r.get("type") == "session"), {})


class PiStaging(Source):
    info = SourceInfo(
        name="pi_staging",
        domain="swe",
        real_user=True,
        location="hf:badlogicgames/pi-mono, hf:badlogicgames/pi-diff-review",
        license="other",
        notes="Dataset cards declare license 'other' without terms.",
    )

    def download(self, settings: Settings) -> None:
        for dataset in DATASETS:
            download_hf(dataset, self.raw_dir(settings) / dataset.replace("/", "__", 1))

    def load(self, settings: Settings) -> Iterator[Trajectory]:
        raw = self.raw_dir(settings)
        skipped: Counter[str] = Counter()
        for dataset_dir in sorted(p for p in raw.iterdir() if p.is_dir()):
            dataset = dataset_dir.name.replace("__", "/", 1)
            repo = card_repo(dataset_dir / "README.md")
            license_id = card_license(dataset_dir / "README.md") or self.info.license
            for path in sorted(dataset_dir.glob("*.jsonl")):
                if path.name in NON_SESSION_FILES:
                    continue
                records = read_jsonl_records(str(path))
                header = session_header(records)
                session_id = header.get("id")
                if not isinstance(session_id, str) or not session_id:
                    skipped["no_session_header"] += 1
                    continue
                parsed = parse_pi(records)
                if not parsed.steps:
                    skipped["no_steps"] += 1
                    continue
                domain = assign_domain(parsed.steps)
                kinds = Counter(str(r.get("type")) for r in records)
                yield Trajectory(
                    trajectory_id=f"pi_staging:{session_id}",
                    source="pi_staging",
                    domain=domain or "swe",
                    domain_decided_by_rule=domain is not None,
                    real_user=True,
                    history_author=(parsed.models.most_common(1)[0][0] if parsed.models else None),
                    harness="pi",
                    repo=repo,
                    base_commit=None,
                    task_env=None,
                    license=license_id,
                    steps=parsed.steps,
                    meta=json.loads(
                        json.dumps(
                            {
                                "dataset": dataset,
                                "file": path.name,
                                "session_version": header.get("version"),
                                "started_at": header.get("timestamp"),
                                "models_seen": dict(parsed.models),
                                "is_continuation": parsed.is_continuation,
                                "has_branches": kinds["branch_summary"] > 0,
                                "entry_types": dict(sorted(kinds.items())),
                            },
                            default=str,
                        )
                    ),
                )
        log.info("pi_staging skipped: %s", dict(skipped))
