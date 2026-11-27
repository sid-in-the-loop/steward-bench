"""The rough cost estimate and `steward bench-estimate`."""

from pathlib import Path

import pytest

from steward.cases.estimate import model_profile, session_usd, simulator_profile
from steward.cases.prices import pricing_of
from steward.cases.session_commands import sessions_path
from steward.cases.sessions import SessionRun, Setting
from steward.cli import build_parser, cmd_bench_estimate
from steward.config import Paths, Settings
from steward.execution.policy import Usage

SETTING = Setting(items=12, changed=(3,), per_day=6)


def stored(model: str, prompt: int, output: int, simulator: Usage | None = None) -> SessionRun:
    return SessionRun(
        case="t",
        session_version="1",
        setting=SETTING,
        model=model,
        trial=0,
        simulator="claude-sonnet-5-5",
        events=[],
        items=[],
        usage=Usage(prompt_tokens=prompt, cached_tokens=prompt // 2, completion_tokens=output),
        cost_usd=0.0,
        simulator_usage=simulator,
    )


def test_a_model_with_sessions_of_its_own_is_estimated_from_them() -> None:
    runs = {"gpt-6-luna": [stored("gpt-6-luna", 1_200, 120), stored("gpt-6-luna", 2_400, 240)]}
    profile = model_profile("gpt-6-luna", runs)
    assert profile is not None and profile.source == "2 own sessions"
    assert profile.session(12) == Usage(
        prompt_tokens=1_800, cached_tokens=900, completion_tokens=180
    )


def test_a_model_without_sessions_takes_the_average_of_the_stored_models() -> None:
    runs = {"a": [stored("a", 1_200, 0)], "b": [stored("b", 3_600, 0)], "c": []}
    profile = model_profile("gpt-6.1-terra", runs)
    assert profile is not None and profile.source == "average of a, b"
    assert profile.session(12).prompt_tokens == 2_400
    assert model_profile("gpt-6.1-terra", {}) is None


def test_the_simulator_is_estimated_from_its_counted_sessions_or_assumed() -> None:
    counted = Usage(prompt_tokens=12_000, completion_tokens=1_200)
    runs = {"gpt-6-luna": [stored("gpt-6-luna", 1, 1, counted), stored("gpt-6-luna", 1, 1)]}
    profile = simulator_profile("claude-sonnet-5-5", runs)
    assert profile.source == "1 stored sessions"
    dollars = session_usd(pricing_of("claude-sonnet-5-5"), profile, 12)
    assert dollars == pytest.approx((12_000 * 2.00 + 1_200 * 10.00) / 1e6)
    assert simulator_profile("gpt-6-luna", runs).source == "assumed"


def test_bench_estimate_prints_each_model_the_simulator_and_the_total(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = Path(__file__).parents[2]
    settings = Settings(paths=Paths(root=root, data=tmp_path))
    path = sessions_path(settings, "gpt-6-luna")
    path.parent.mkdir(parents=True)
    path.write_text(stored("gpt-6-luna", 1_200_000, 12_000).model_dump_json() + "\n")
    listed = tmp_path / "cases.txt"
    listed.write_text("dbops-18b393cdd5\n")
    argv = ["bench-estimate", "--model", "gpt-6-luna", "--model", "gemini-4-neon"]
    args = build_parser().parse_args([*argv, "--cases", str(listed), "--trials", "2"])
    assert cmd_bench_estimate(settings, args) == 0
    out = capsys.readouterr().out
    sessions = 4
    assert f"{sessions} sessions per model" in out
    luna = sessions * pricing_of("gpt-6-luna").cost(stored("x", 1_200_000, 12_000).usage)
    assert f"${luna:9.2f}  tokens from 1 own sessions" in out
    assert "gemini-4-neon" in out and "tokens from average of gpt-6-luna" in out
    assert "simulator claude-sonnet-5-5" in out and "tokens from assumed" in out


def test_bench_estimate_refuses_a_model_without_a_price(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings(paths=Paths(root=tmp_path, data=tmp_path))
    args = build_parser().parse_args(["bench-estimate", "--model", "some-model-1"])
    assert cmd_bench_estimate(settings, args) == 2
    assert "no price confirmed" in capsys.readouterr().err
