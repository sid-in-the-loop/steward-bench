"""The fixed sample of tasks for ablations."""

from pathlib import Path

from steward.cases.ablation_sample import (
    SIZE,
    benchmark_environments,
    draw,
    read_sample,
    sample_path,
    shares,
)
from steward.cases.session_commands import load_sessions
from steward.config import Paths, Settings


def test_shares_follow_the_environments_by_largest_remainder() -> None:
    counts = {"terminal": 30, "swe": 22, "dbops": 20, "mlops": 20, "misc": 19, "workplace": 12}
    given = shares(counts, 40)
    assert given == {"terminal": 10, "swe": 7, "dbops": 7, "mlops": 6, "misc": 6, "workplace": 4}


def test_the_draw_is_fixed_and_balanced() -> None:
    environments = {f"{name}-{i}": name for name in ("a", "b") for i in range(10)}
    environments |= {f"c-{i}": "c" for i in range(20)}
    sample = draw(environments, size=8)
    assert sample == draw(environments, size=8)
    assert [environments[t].startswith("c") for t in sample].count(True) == 4
    assert sample == sorted(sample) and len(set(sample)) == 8


def test_the_checked_in_sample_is_the_draw() -> None:
    root = Path(__file__).parents[2]
    settings = Settings(paths=Paths(root=root, data=root / "data"))
    environments = benchmark_environments(load_sessions(settings))
    assert len(environments) == 123
    assert read_sample(sample_path(settings)) == draw(environments)
    assert len(draw(environments)) == SIZE
