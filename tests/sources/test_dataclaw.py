import shutil
import tarfile
from pathlib import Path

import pandas as pd
import pytest

from steward.config import Settings
from steward.schema import Trajectory
from steward.sources.dataclaw import DataClaw, dataset_dir_name, dataset_from_dir
from steward.sources.hf_card import card_license

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "dataclaw"


def place_fixture(settings: Settings) -> None:
    raw = settings.paths.raw
    shutil.copytree(FIXTURE / "dataclaw", raw / "dataclaw")
    harbor = raw / "dataclaw_harbor"
    (harbor / "data").mkdir(parents=True)
    shutil.copy(FIXTURE / "harbor_README.md", harbor / "README.md")
    with tarfile.open(harbor / "sessions_raw.tar.gz", "w:gz") as tar:
        for path in sorted((FIXTURE / "harbor_sessions").glob("*.json")):
            tar.add(path, arcname=f"sessions_raw/{path.name}")
    pd.DataFrame(
        {"session_id": ["s-harbor"], "github_repo": ["carol/gamma"], "stars_approx": [42]}
    ).to_parquet(harbor / "data" / "candidates.parquet")
    pd.DataFrame(
        {
            "session_id": ["s-harbor", "s-codex"],
            "github_repo": ["ignored/by-priority", "not a repo"],
            "stars_approx": [1.0, None],
        }
    ).to_parquet(harbor / "data" / "all_screened.parquet")


@pytest.fixture
def loaded(settings: Settings) -> dict[str, Trajectory]:
    place_fixture(settings)
    return {t.trajectory_id: t for t in DataClaw().load(settings)}


def test_dedup_by_id_and_content(loaded: dict[str, Trajectory]) -> None:
    assert list(loaded) == [
        "dataclaw:s-alpha",
        "dataclaw:s-legacy",
        "dataclaw:s-codex",
        "dataclaw:s-harbor",
    ]
    assert loaded["dataclaw:s-legacy"].meta["dataset"] == "alice/dataclaw-alice"
    assert loaded["dataclaw:s-legacy"].history_author == "claude-opus-4-5-20251101"
    assert loaded["dataclaw:s-codex"].meta["dataset"] == "bob/my-personal-codex-data"


def test_flat_messages_to_steps(loaded: dict[str, Trajectory]) -> None:
    trajectory = loaded["dataclaw:s-alpha"]
    kinds = [s.kind for s in trajectory.steps]
    assert kinds == [
        "user",
        "agent_text",
        "action",
        "result",
        "action",
        "result",
        "user",
        "user",
        "user",
    ]
    assert [s.index for s in trajectory.steps] == list(range(len(kinds)))
    assert not any("private reasoning" in s.content for s in trajectory.steps)
    bash = trajectory.steps[2]
    assert bash.tool == "Bash" and bash.content == "pytest -q"
    assert bash.tool_input == {"command": "pytest -q", "description": "run tests"}
    assert trajectory.steps[3].content == "1 failed\n[exit_code: 1]"
    assert trajectory.steps[6].is_interruption
    assert trajectory.steps[8].content == "[image]"
    assert trajectory.harness == "claude-code"
    assert trajectory.real_user and trajectory.license == "mit"
    assert "input_tokens" not in trajectory.meta["stats"]


def test_legacy_string_inputs(loaded: dict[str, Trajectory]) -> None:
    trajectory = loaded["dataclaw:s-legacy"]
    assert [s.kind for s in trajectory.steps] == ["user", "action"]
    assert trajectory.steps[1].tool_input == {"input": "ls -la"}
    assert trajectory.meta["export_family"] == "A"


def test_license_harness_and_domain_per_dataset(loaded: dict[str, Trajectory]) -> None:
    codex = loaded["dataclaw:s-codex"]
    assert codex.license == "apache-2.0"
    assert codex.harness == "codex"
    assert codex.steps[1].content == "kubectl apply -f k8s/app.yaml"
    assert codex.domain == "swe"


def test_harbor_session_gets_repo(loaded: dict[str, Trajectory]) -> None:
    harbor = loaded["dataclaw:s-harbor"]
    assert harbor.repo == "carol/gamma"
    assert harbor.history_author is None
    assert harbor.meta["dataset"] == "alexshengzhili/dataclaw-harbor-candidates"
    assert harbor.meta["upstream_hf_repo"] == "carol/dataclaw-carol"
    assert harbor.meta["stars_approx"] == 42
    assert loaded["dataclaw:s-codex"].repo is None


def test_dataset_names_round_trip() -> None:
    assert dataset_from_dir(dataset_dir_name("REXX-NEW/my-personal-codex-data")) == (
        "REXX-NEW/my-personal-codex-data"
    )


def test_card_license(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("---\npretty_name: x\nlicense: 'cc-by-4.0'\n---\nlicense: mit\n")
    assert card_license(readme) == "cc-by-4.0"
    readme.write_text("# no front matter\nlicense: mit\n")
    assert card_license(readme) is None
