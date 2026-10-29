import gzip
import shutil
from pathlib import Path

import pytest

from steward.config import Settings
from steward.schema import Trajectory
from steward.sources.the_agent_company import TheAgentCompany, parse_submission, trajectory_files
from steward.sources.the_agent_company_formats import redact_credentials

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "the_agent_company"
OPENHANDS = "20250510_OpenHands-0.28.1-sonnet-20250219"


@pytest.fixture
def loaded(settings: Settings) -> dict[str, Trajectory]:
    shutil.copytree(FIXTURE, settings.paths.raw / "the_agent_company")
    return {t.trajectory_id: t for t in TheAgentCompany().load(settings)}


def test_ids_cover_every_format_and_skip_state_files(loaded: dict[str, Trajectory]) -> None:
    assert sorted(loaded) == [
        "the_agent_company:20250510_OWL-RolePlay-gpt-4o-o3-mini/hr-onboarding",
        f"the_agent_company:{OPENHANDS}/admin-book-room",
        "the_agent_company:20250729_OpenHands-0.28.1-claude37-gpt4o/admin-book-room",
        "the_agent_company:20251013_MUSE-gemini-2.5-flash/sde-check-disk",
    ]


def test_openhands_events(loaded: dict[str, Trajectory]) -> None:
    t = loaded[f"the_agent_company:{OPENHANDS}/admin-book-room"]
    assert [s.kind for s in t.steps] == [
        "user",
        "agent_text",
        "action",
        "result",
        "action",
        "result",
        "user",
        "agent_text",
        "action",
    ]
    assert [s.index for s in t.steps] == list(range(len(t.steps)))
    assert [s.tool for s in t.steps if s.kind == "action"] == [
        "execute_bash",
        "str_replace_editor",
        "finish",
    ]
    assert t.steps[2].content == "cat /instruction/task.md"
    assert t.steps[2].tool_input == {"command": "cat /instruction/task.md"}
    assert t.steps[4].tool_input == {
        "path": "/workspace/notes.txt",
        "command": "create",
        "file_text": "room A",
    }
    assert all("private plan" not in s.content for s in t.steps)
    assert t.steps[7].content == "The room is booked."
    assert t.history_author == "sonnet-20250219"
    assert t.harness == "openhands"
    assert t.task_env == "theagentcompany:admin-book-room"
    assert t.domain == "workplace" and not t.real_user
    assert t.meta["final_score"] == {"total": 3, "result": 1}
    assert t.meta["harness_version"] == "0.28.1"
    assert t.meta["response_models"] == ["claude-3-7-sonnet-20250219"]


def test_agent_and_environment_models_split(loaded: dict[str, Trajectory]) -> None:
    t = loaded["the_agent_company:20250729_OpenHands-0.28.1-claude37-gpt4o/admin-book-room"]
    assert t.history_author == "claude-3-7-sonnet-20250219"
    assert t.meta["environment_model"] == "gpt4o"
    assert "final_score" not in t.meta


def test_owl_turns(loaded: dict[str, Trajectory]) -> None:
    t = loaded["the_agent_company:20250510_OWL-RolePlay-gpt-4o-o3-mini/hr-onboarding"]
    assert [s.kind for s in t.steps] == [
        "user",
        "action",
        "result",
        "action",
        "result",
        "agent_text",
        "user",
        "agent_text",
    ]
    assert t.steps[3].tool == "execute_code"
    assert t.steps[4].content == '["1"]'
    assert t.harness == "owl-roleplay"
    assert t.history_author == "gpt-4o-o3-mini"


def test_muse_text(loaded: dict[str, Trajectory]) -> None:
    t = loaded["the_agent_company:20251013_MUSE-gemini-2.5-flash/sde-check-disk"]
    assert [s.kind for s in t.steps] == [
        "system",
        "user",
        "agent_text",
        "action",
        "result",
        "agent_text",
    ]
    assert "not-a-real-password" not in t.steps[0].content
    assert "sk-fixture-not-real" not in t.steps[0].content
    assert t.steps[3].tool == "run_cmd" and t.steps[3].content == "df -h"
    assert (
        "/dev/sda1" in t.steps[4].content and "Continue with the next step." in t.steps[4].content
    )
    assert "<tool_call>" not in t.steps[2].content and "Action:" not in t.steps[2].content
    assert t.harness == "muse" and t.history_author == "gemini-2.5-flash"


def test_gzipped_copy_preferred(tmp_path: Path) -> None:
    (tmp_path / "traj_a.json").write_text("[]")
    (tmp_path / "traj_a.json.gz").write_bytes(gzip.compress(b"[]"))
    (tmp_path / "state_a.json").write_text("{}")
    assert trajectory_files(tmp_path) == {"a": tmp_path / "traj_a.json.gz"}


def test_parse_submission() -> None:
    versa = parse_submission("20250614_OpenHands-Versa-claude-sonnet-4")
    assert (versa.harness, versa.history_author) == ("openhands-versa", "claude-sonnet-4")
    tte = parse_submission("20251110_TTE-MatrixAgent-Deepseek-V3.2")
    assert (tte.harness, tte.history_author) == ("tte-matrixagent", "Deepseek-V3.2")
    unknown = parse_submission("something-else")
    assert unknown.harness is None and unknown.history_author is None


def test_redact_credentials() -> None:
    assert (
        redact_credentials("    Root Password: x\nName: y")
        == "    Root Password: [redacted]\nName: y"
    )
