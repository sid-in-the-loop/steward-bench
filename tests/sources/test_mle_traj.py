from __future__ import annotations

import math
import shutil
from pathlib import Path

import pandas as pd
import pytest

from steward.config import Paths, Settings
from steward.schema import Trajectory
from steward.sources.mle_traj import MleTraj, notebook_to_text, run_model

FIXTURES = Path(__file__).parent.parent / "fixtures" / "mle_traj"
CODEX = "run_20260421_000000_gpt_5_4_mini_run29_toy-comp"
MLEV_RUN = "run_20260421_000001_gpt_5_4_mini_run29_24h_toy-comp"
MLEV = f"mlev__{MLEV_RUN}__toy-comp__branch1"


def write_tables(root: Path) -> None:
    """The flat parquet tables, built here because the fixtures are otherwise plain text."""
    data = root / "data"
    data.mkdir()
    pd.DataFrame(
        [
            {
                "key_id": "111",
                "comp": "toy-comp",
                "group": "Expert",
                "is_agent": False,
                "is_best_branch": False,
                "n_versions": 2,
                "n_scored": 1,
                "min_score": 0.7,
                "max_score": 0.7,
            },
            {
                "key_id": CODEX,
                "comp": "toy-comp",
                "group": "codex",
                "is_agent": True,
                "is_best_branch": False,
                "n_versions": 2,
                "n_scored": 1,
                "min_score": 0.8,
                "max_score": 0.8,
            },
            {
                "key_id": MLEV,
                "comp": "toy-comp",
                "group": "mlevolve",
                "is_agent": True,
                "is_best_branch": True,
                "n_versions": 2,
                "n_scored": 2,
                "min_score": 0.6,
                "max_score": math.nan,
            },
        ]
    ).to_parquet(data / "trajectory_index.parquet")
    pd.DataFrame(
        [
            {"key_id": "111", "version_number": 1, "orig_version_number": math.nan},
            {"key_id": "111", "version_number": 2, "orig_version_number": math.nan},
            {"key_id": CODEX, "version_number": 2, "orig_version_number": math.nan},
            {"key_id": CODEX, "version_number": 1, "orig_version_number": math.nan},
            {"key_id": MLEV, "version_number": 1, "orig_version_number": 1.0},
            {"key_id": MLEV, "version_number": 2, "orig_version_number": 3.0},
        ]
    ).to_parquet(data / "state.parquet")


@pytest.fixture(scope="module")
def loaded(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Trajectory]:
    data = tmp_path_factory.mktemp("data")
    root = data / "raw" / "mle_traj"
    shutil.copytree(FIXTURES, root)
    write_tables(root)
    settings = Settings(paths=Paths(root=Path(__file__).parents[2], data=data))
    return {t.trajectory_id: t for t in MleTraj().load(settings)}


def test_all_author_kinds_load(loaded: dict[str, Trajectory]) -> None:
    assert sorted(loaded) == [f"mle_traj:{key}" for key in sorted(["111", CODEX, MLEV])]
    for trajectory in loaded.values():
        assert trajectory.domain == "ai_rnd"
        assert trajectory.task_env == "kaggle:toy-comp"
        assert [s.index for s in trajectory.steps] == list(range(len(trajectory.steps)))


def test_human_kernel(loaded: dict[str, Trajectory]) -> None:
    trajectory = loaded["mle_traj:111"]
    assert trajectory.real_user is True
    assert trajectory.history_author == "human"
    assert trajectory.harness == "kaggle-kernel"
    assert trajectory.license == "Apache-2.0"
    assert [s.kind for s in trajectory.steps] == ["system", "action", "result", "action", "result"]
    second = trajectory.steps[3]
    assert second.tool == "edit"
    assert "-model = 1\n" in second.content and "+model = 2\n" in second.content
    assert trajectory.steps[2].content == "public score: 0.7; run time: 4s"
    assert trajectory.steps[4].content == "run time: 5s"
    assert trajectory.meta["author_tier"] == "Expert"
    assert "author_username" not in trajectory.meta


def test_codex_run(loaded: dict[str, Trajectory]) -> None:
    trajectory = loaded[f"mle_traj:{CODEX}"]
    assert trajectory.real_user is False
    assert trajectory.history_author == "gpt_5_4_mini"
    assert trajectory.harness == "codex"
    assert [s.kind for s in trajectory.steps] == ["system", "action", "action", "result"]
    assert trajectory.steps[1].tool_input == {
        "path": "versions/v001.py",
        "version": 1,
        "lines_changed": None,
    }
    assert trajectory.steps[3].content == "score: 0.81"


def test_mlevolve_branch_follows_orig_versions(loaded: dict[str, Trajectory]) -> None:
    trajectory = loaded[f"mle_traj:{MLEV}"]
    assert trajectory.harness == "mlevolve"
    assert trajectory.history_author == "gpt_5_4_mini"
    assert [s.kind for s in trajectory.steps] == [
        "system",
        "agent_text",
        "action",
        "result",
        "agent_text",
        "action",
        "result",
    ]
    assert trajectory.steps[4].content == "Add gradient boosting."
    assert '+model = "gbm"\n' in trajectory.steps[5].content
    assert all("broken(" not in s.content for s in trajectory.steps)
    assert trajectory.steps[6].content == "cv score: 0.7; is_buggy: False; is_valid: True"
    assert trajectory.meta["max_heldout_score"] is None


def test_notebook_to_text() -> None:
    raw = '{"cells": [{"cell_type": "markdown", "source": "Title"}, {"cell_type": "code", "source": ["a = 1"]}]}'
    assert notebook_to_text(raw) == "# %% [markdown]\n# Title\n\n# %%\na = 1\n"


def test_run_model() -> None:
    assert run_model(CODEX) == "gpt_5_4_mini"
    assert run_model("not-a-run") is None
