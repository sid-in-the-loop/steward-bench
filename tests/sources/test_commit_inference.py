import json
import os
import subprocess
from datetime import datetime

from steward.sources import commit_inference
from steward.store import read_meta, write_source
from tests.conftest import make_trajectory


def make_repo(path) -> list[str]:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    shas = []
    for day in ("2026-01-01", "2026-01-05"):
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"{day}T12:00:00+00:00"
        subprocess.run(
            ["git", "-C", str(path), "commit", "-q", "--allow-empty", "-m", day],
            check=True,
            env=env,
        )
        shas.append(
            subprocess.run(
                ["git", "-C", str(path), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
    return shas


def test_infers_last_commit_before_session_start(settings, tmp_path, monkeypatch) -> None:
    shas = make_repo(tmp_path / "upstream")
    trajectory = make_trajectory("s1").model_copy(update={"base_commit": None})
    trajectory.steps[0].timestamp = datetime.fromisoformat("2026-01-03T00:00:00+00:00")
    later = make_trajectory("s2").model_copy(update={"base_commit": None})
    later.steps[0].timestamp = datetime.fromisoformat("2026-02-01T00:00:00+00:00")
    write_source(settings.paths.normalized, "fixture", [trajectory, later, make_trajectory("s3")])
    monkeypatch.setattr(
        commit_inference, "fetch_history", lambda s, repo: tmp_path / "upstream" / ".git"
    )
    assert commit_inference.infer_for_source(settings, "fixture", 40) == 2
    metas = {m.trajectory_id: m for m in read_meta(settings.paths.normalized, "fixture")}
    assert metas["fixture:s1"].base_commit == shas[0]
    assert metas["fixture:s2"].base_commit == shas[1]
    assert metas["fixture:s3"].base_commit == "a" * 40
    assert (
        json.loads(metas["fixture:s1"].meta_json)["base_commit_method"] == commit_inference.METHOD
    )


def test_unreachable_repo_leaves_commit_empty(settings, monkeypatch) -> None:
    trajectory = make_trajectory("s1").model_copy(update={"base_commit": None})
    trajectory.steps[0].timestamp = datetime.fromisoformat("2026-01-03T00:00:00+00:00")
    write_source(settings.paths.normalized, "fixture", [trajectory])
    monkeypatch.setattr(commit_inference, "fetch_history", lambda s, repo: None)
    assert commit_inference.infer_for_source(settings, "fixture", 40) == 0
    assert read_meta(settings.paths.normalized, "fixture")[0].base_commit is None
