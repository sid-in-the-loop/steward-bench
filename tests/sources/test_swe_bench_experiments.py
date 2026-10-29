from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from steward.config import Paths, Settings
from steward.schema import Trajectory
from steward.sources.swe_bench_download import choose_instances, instance_of
from steward.sources.swe_bench_experiments import SweBenchExperiments, repo_from_instance
from steward.sources.swe_bench_formats import detect_format, read_traj_file

FIXTURES = Path(__file__).parent.parent / "fixtures" / "swe_bench_experiments"
IID = "scikit-learn__scikit-learn-10297"


@pytest.fixture(scope="module")
def loaded(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Trajectory]:
    data = tmp_path_factory.mktemp("data")
    shutil.copytree(FIXTURES, data / "raw" / "swe_bench_experiments")
    settings = Settings(paths=Paths(root=Path(__file__).parents[2], data=data))
    return {t.meta["submission"]: t for t in SweBenchExperiments().load(settings)}


def kinds(trajectory: Trajectory) -> list[str]:
    return [step.kind for step in trajectory.steps]


def test_every_format_loads_and_the_flattened_extra_is_excluded(
    loaded: dict[str, Trajectory],
) -> None:
    assert "20251127_openhands_claude-opus-4-5" not in loaded
    assert {t.meta["format"] for t in loaded.values()} == {
        "mini_text",
        "mini_tool_calls",
        "mini_responses",
        "swe_agent",
        "openhands_tool_calls",
        "openhands_prompt",
        "llamaindex",
        "nova_log",
    }


def test_identity_and_task_fields(loaded: dict[str, Trajectory]) -> None:
    trajectory = loaded["20260217_mini-v2.0.0_glm-5-high"]
    assert (
        trajectory.trajectory_id == f"swe_bench_experiments:20260217_mini-v2.0.0_glm-5-high/{IID}"
    )
    assert trajectory.task_env == f"swe-bench-verified:{IID}"
    assert trajectory.repo == "scikit-learn/scikit-learn"
    assert trajectory.history_author == "glm-5"
    assert trajectory.harness == "mini-swe-agent"
    assert trajectory.domain == "swe"
    assert trajectory.real_user is False
    assert [s.index for s in trajectory.steps] == list(range(len(trajectory.steps)))


def test_resolved_read_from_both_report_shapes(loaded: dict[str, Trajectory]) -> None:
    assert loaded["20251120_livesweagent_gemini-3-pro-preview"].meta["resolved"] is True
    assert loaded["20260217_mini-v2.0.0_glm-5-high"].meta["resolved"] is False
    assert loaded["20250716_openhands_kimi_k2"].meta["resolved"] is None


def test_mini_text_runs_only_single_block_turns(loaded: dict[str, Trajectory]) -> None:
    trajectory = loaded["20251120_livesweagent_gemini-3-pro-preview"]
    actions = [s for s in trajectory.steps if s.kind == "action"]
    assert [a.content for a in actions] == [
        "grep -n store_cv_values sklearn/linear_model/ridge.py",
        "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
    ]
    assert kinds(trajectory)[:6] == [
        "system",
        "user",
        "agent_text",
        "action",
        "result",
        "agent_text",
    ]
    assert kinds(trajectory)[6] == "system"
    assert trajectory.harness == "live-swe-agent"


def test_reasoning_is_dropped_and_harness_feedback_is_system(loaded: dict[str, Trajectory]) -> None:
    trajectory = loaded["20260217_mini-v2.0.0_glm-5-high"]
    assert kinds(trajectory) == ["system", "user", "agent_text", "action", "result", "system"]
    assert trajectory.steps[3].tool == "bash"
    assert trajectory.steps[3].tool_input == {"command": "cat setup.py"}
    assert all("secret reasoning" not in s.content for s in trajectory.steps)
    assert trajectory.meta["exit_status"] == "LimitsExceeded"


def test_responses_api(loaded: dict[str, Trajectory]) -> None:
    trajectory = loaded["20260217_mini-v2.0.0_gpt-5-2-high"]
    assert kinds(trajectory) == ["system", "user", "agent_text", "action", "result"]
    assert trajectory.steps[3].content == "pytest -q"


def test_swe_agent_traj_uses_thought_and_action(loaded: dict[str, Trajectory]) -> None:
    trajectory = loaded["20250511_sweagent_lm_32b"]
    tools = [s.tool for s in trajectory.steps if s.kind == "action"]
    assert tools == ["str_replace_editor", "bash", "submit"]
    assert trajectory.history_author == "Qwen 2.5"
    maverick = loaded["20250720_mini-v0.0.0-Llama-4-Maverick-17B-Instruct"]
    assert maverick.harness == "swe-agent"


def test_openhands_native_tool_calls(loaded: dict[str, Trajectory]) -> None:
    trajectory = loaded["20250716_openhands_kimi_k2"]
    assert [s.tool for s in trajectory.steps if s.kind == "action"] == ["execute_bash", "finish"]
    assert trajectory.harness == "openhands"


def test_openhands_prompt_markup_and_think_tags(loaded: dict[str, Trajectory]) -> None:
    trajectory = loaded["20250928_trae_doubao_seed_code"]
    actions = [s for s in trajectory.steps if s.kind == "action"]
    assert actions[0].tool == "str_replace_editor"
    assert actions[0].tool_input == {"command": "view", "path": "/testbed/setup.py"}
    assert actions[1].tool_input == {"command": "pytest -q"}
    assert kinds(trajectory).count("result") == 2
    assert all("private plan" not in s.content for s in trajectory.steps)
    assert trajectory.history_author == "Doubao-Seed-Code"
    assert trajectory.meta["models"] == ["Doubao-Seed-Code", "Doubao-Seed-1.6"]


def test_llamaindex_drops_thinking_blocks(loaded: dict[str, Trajectory]) -> None:
    trajectory = loaded["20251205_sonar-foundation-agent_claude-opus-4-5"]
    assert kinds(trajectory) == ["system", "user", "agent_text", "action", "result"]
    assert all("deep thoughts" not in s.content for s in trajectory.steps)
    assert trajectory.harness == "sonar-foundation-agent"


def test_nova_log_emits_prompt_once_and_commands_as_actions(loaded: dict[str, Trajectory]) -> None:
    trajectory = loaded["20250527_amazon.nova-premier-v1.0"]
    assert kinds(trajectory) == [
        "system",
        "user",
        "agent_text",
        "action",
        "result",
        "agent_text",
        "action",
        "action",
    ]
    tools = [s.tool for s in trajectory.steps if s.kind == "action"]
    assert tools == ["open_file", "find_and_replace_code", "complete"]
    assert trajectory.steps[6].tool_input == {"file_path": "setup.py", "find": "a", "replace": "b"}
    assert trajectory.steps[2].content == "Open setup.py first."


def test_detect_format_rejects_unknown_shapes() -> None:
    assert detect_format({"foo": 1}) is None
    assert detect_format("text") is None
    flattened = FIXTURES / "20251127_openhands_claude-opus-4-5" / "trajs" / f"{IID}.json"
    assert detect_format(read_traj_file(flattened)) == "openhands_flattened"


def test_repo_from_instance() -> None:
    assert repo_from_instance("django__django-11087") == "django/django"
    assert repo_from_instance("no-separator") is None


def test_download_helpers() -> None:
    assert instance_of(f"{IID}/{IID}.traj.json") == IID
    assert instance_of(f"{IID}.log") == IID
    assert instance_of("preds.json") is None
    assert choose_instances([{"a__b-1", "c__d-2"}, {"a__b-1"}], 5) == ["a__b-1"]
