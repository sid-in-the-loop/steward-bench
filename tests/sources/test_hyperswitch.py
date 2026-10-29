import pandas as pd

from steward.sources.hyperswitch import Hyperswitch


def test_hyperswitch(settings) -> None:
    raw = settings.paths.raw / "hyperswitch" / "data"
    raw.mkdir(parents=True)
    chatml = (
        "<|im_start|>user\nImplement the change<|im_end|>"
        '<|im_start|>assistant\n<think>x</think><tool_use id="1">{"name": "Edit", "arguments": {"file_path": "a.rs"}}</tool_use><|im_end|>'
        '<|im_start|>user\n<tool_result id="1">done</tool_result><|im_end|>'
    )
    pd.DataFrame(
        {
            "parent_dir": ["logs_glm"],
            "prefix": ["juspay__hyperswitch-1"],
            "chatml": [chatml],
            "gitdiff": [""],
        }
    ).to_parquet(raw / "train.parquet")
    [trajectory] = list(Hyperswitch().load(settings))
    assert trajectory.trajectory_id == "hyperswitch:logs_glm/juspay__hyperswitch-1"
    assert trajectory.task_env == "juspay__hyperswitch-1" and trajectory.history_author == "glm"
    assert [s.kind for s in trajectory.steps] == ["user", "action", "result"]
    assert not trajectory.real_user
