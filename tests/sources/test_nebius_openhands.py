import pandas as pd

from steward.sources.nebius_openhands import NebiusOpenHands


def test_nebius_sampling_is_deterministic(settings) -> None:
    raw = settings.paths.raw / "nebius_openhands"
    raw.mkdir(parents=True)
    message = [
        {"role": "user", "content": "fix", "name": None, "tool_call_id": None, "tool_calls": None}
    ]
    pd.DataFrame(
        {
            "trajectory_id": [f"t{i}" for i in range(10)],
            "instance_id": [f"o__r-{i}" for i in range(10)],
            "repo": ["o/r"] * 10,
            "trajectory": [message] * 10,
            "exit_status": ["submitted"] * 10,
            "resolved": [1] * 10,
        }
    ).to_parquet(raw / "trajectories.parquet")
    settings.sampling.nebius_sample = 4
    first = [t.trajectory_id for t in NebiusOpenHands().load(settings)]
    second = [t.trajectory_id for t in NebiusOpenHands().load(settings)]
    assert first == second and len(first) == 4
    assert all(t.startswith("nebius_openhands:t") for t in first)
