"""Hyperswitch traces: agent runs on juspay/hyperswitch task instances (archit11/claude_traces_hs)."""

from __future__ import annotations

from collections.abc import Iterator

import pandas as pd

from steward.config import Settings
from steward.schema import Trajectory
from steward.sources.base import Source, SourceInfo
from steward.sources.chat_messages import parse_chatml_tool_markup
from steward.sources.hf import download_hf

MODEL_BY_DIR = {"glm": "glm", "kat": "kat", "qwen": "qwen"}


def model_from_dir(parent_dir: str) -> str | None:
    return next((model for key, model in MODEL_BY_DIR.items() if key in parent_dir), None)


class Hyperswitch(Source):
    info = SourceInfo(
        name="hyperswitch",
        domain="swe",
        real_user=False,
        location="hf:archit11/claude_traces_hs",
        license=None,
    )

    def download(self, settings: Settings) -> None:
        download_hf("archit11/claude_traces_hs", self.raw_dir(settings))

    def load(self, settings: Settings) -> Iterator[Trajectory]:
        frame = pd.read_parquet(next(self.raw_dir(settings).glob("data/*.parquet")))
        frame = frame.sort_values(["prefix", "parent_dir"])
        for row in frame.itertuples(index=False):
            steps = parse_chatml_tool_markup(str(row.chatml))
            if not steps:
                continue
            native_id = f"{row.parent_dir}/{row.prefix}"
            yield Trajectory(
                trajectory_id=f"hyperswitch:{native_id}",
                source="hyperswitch",
                domain="swe",
                real_user=False,
                history_author=model_from_dir(str(row.parent_dir)),
                harness="claude-code",
                repo="juspay/hyperswitch",
                base_commit=None,
                task_env=str(row.prefix),
                license=None,
                steps=steps,
                meta={"parent_dir": row.parent_dir, "has_gitdiff": bool(row.gitdiff)},
            )
