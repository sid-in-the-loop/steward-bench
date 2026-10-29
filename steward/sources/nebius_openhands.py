"""Nebius SWE-rebench OpenHands trajectories from Qwen3-Coder-480B, sampled (HF nebius/...)."""

from __future__ import annotations

import random
from collections.abc import Iterator

import pyarrow.parquet as pq

from steward.config import Settings
from steward.schema import Trajectory
from steward.sources.base import Source, SourceInfo
from steward.sources.chat_messages import parse_openai_messages
from steward.sources.hf import download_hf

COLUMNS = ["trajectory_id", "instance_id", "repo", "trajectory", "exit_status", "resolved"]


class NebiusOpenHands(Source):
    info = SourceInfo(
        name="nebius_openhands",
        domain="swe",
        real_user=False,
        location="hf:nebius/SWE-rebench-openhands-trajectories",
        license="cc-by-4.0",
    )

    def download(self, settings: Settings) -> None:
        download_hf("nebius/SWE-rebench-openhands-trajectories", self.raw_dir(settings))

    def sample_rows(self, settings: Settings) -> set[int]:
        path = self.raw_dir(settings) / "trajectories.parquet"
        total = pq.ParquetFile(path).metadata.num_rows
        rng = random.Random(settings.sampling.seed)
        return set(rng.sample(range(total), min(total, settings.sampling.nebius_sample)))

    def load(self, settings: Settings) -> Iterator[Trajectory]:
        path = self.raw_dir(settings) / "trajectories.parquet"
        wanted = self.sample_rows(settings)
        parquet = pq.ParquetFile(path)
        offset = 0
        for group in range(parquet.num_row_groups):
            table = parquet.read_row_group(group, columns=COLUMNS)
            rows = table.to_pylist()
            for position, row in enumerate(rows, start=offset):
                if position not in wanted:
                    continue
                steps = parse_openai_messages(row["trajectory"] or [])
                if not steps:
                    continue
                yield Trajectory(
                    trajectory_id=f"nebius_openhands:{row['trajectory_id']}",
                    source="nebius_openhands",
                    domain="swe",
                    real_user=False,
                    history_author="qwen3-coder-480b-a35b-instruct",
                    harness="openhands",
                    repo=row["repo"],
                    base_commit=None,
                    task_env=f"swe-rebench:{row['instance_id']}",
                    license=self.info.license,
                    steps=steps,
                    meta={
                        "instance_id": row["instance_id"],
                        "exit_status": row["exit_status"],
                        "resolved": row["resolved"],
                    },
                )
            offset += len(rows)
