"""Resumable download of SWE-bench experiments trajectories from the public S3 bucket.

Metadata comes from the swe-bench/experiments GitHub repo at a pinned commit; trajectories
and per-instance reports come from s3://swe-bench-submissions over anonymous HTTPS.
Files whose local size already matches the bucket are skipped.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from pydantic import BaseModel

BUCKET = "https://swe-bench-submissions.s3.amazonaws.com"
METADATA_URL = (
    "https://raw.githubusercontent.com/swe-bench/experiments/{commit}/"
    "evaluation/verified/{submission}/metadata.yaml"
)
METADATA_COMMIT = "40f164d5"
S3_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
INSTANCE_IN_PATH = re.compile(r"(\w[\w.-]*__[\w.-]+?-\d+)(?=[./]|$)")
SELECTED_FILE = "selected_instances.txt"


class S3Object(BaseModel):
    key: str
    size: int


def list_objects(client: httpx.Client, prefix: str) -> Iterator[S3Object]:
    token: str | None = None
    while True:
        params = {"list-type": "2", "prefix": prefix}
        if token:
            params["continuation-token"] = token
        response = client.get(f"{BUCKET}/", params=params)
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
        for item in root.findall("s3:Contents", S3_NS):
            key = item.findtext("s3:Key", default="", namespaces=S3_NS)
            size = int(item.findtext("s3:Size", default="0", namespaces=S3_NS))
            yield S3Object(key=key, size=size)
        token = root.findtext("s3:NextContinuationToken", namespaces=S3_NS)
        if not token:
            return


def instance_of(relative_path: str) -> str | None:
    match = INSTANCE_IN_PATH.match(relative_path.split("/", 1)[0])
    return match.group(1) if match else None


def choose_instances(per_submission: Iterable[set[str]], count: int) -> list[str]:
    """The first `count` ids, ordered by sha256, among those present in every submission."""
    common = set.intersection(*per_submission)
    return sorted(common, key=lambda iid: hashlib.sha256(iid.encode()).hexdigest())[:count]


def _fetch(client: httpx.Client, url: str, target: Path, expected_size: int | None) -> str:
    if target.exists() and (expected_size is None or target.stat().st_size == expected_size):
        return "skip"
    response = client.get(url)
    if response.status_code == 404:
        return "404"
    response.raise_for_status()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    tmp.write_bytes(response.content)
    tmp.replace(target)
    return "ok"


def download_submissions(
    target: Path, submissions: dict[str, str], instances: int, workers: int = 16
) -> None:
    """submissions maps the submission directory name to its S3 split (verified or bash-only)."""
    target.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        for submission in submissions:
            url = METADATA_URL.format(commit=METADATA_COMMIT, submission=submission)
            _fetch(client, url, target / submission / "metadata.yaml", None)
        listings = {
            submission: list(list_objects(client, f"{split}/{submission}/trajs/"))
            for submission, split in submissions.items()
        }
        selected_path = target / SELECTED_FILE
        if selected_path.exists():
            selected = selected_path.read_text().split()
        else:
            present = [
                {
                    iid
                    for obj in objects
                    if (iid := instance_of(obj.key.split("/trajs/", 1)[1])) is not None
                }
                for objects in listings.values()
            ]
            selected = choose_instances(present, instances)
            selected_path.write_text("\n".join(selected) + "\n")
        wanted = set(selected)
        jobs: list[tuple[str, Path, int | None]] = []
        for submission, split in submissions.items():
            for obj in listings[submission]:
                relative = obj.key.split("/trajs/", 1)[1]
                if instance_of(relative) in wanted:
                    jobs.append(
                        (f"{BUCKET}/{obj.key}", target / submission / "trajs" / relative, obj.size)
                    )
            for iid in selected:
                key = f"{split}/{submission}/logs/{iid}/report.json"
                jobs.append(
                    (f"{BUCKET}/{key}", target / submission / "logs" / iid / "report.json", None)
                )
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(lambda job: _fetch(client, *job), jobs))
