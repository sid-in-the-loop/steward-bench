"""End-to-end run on synthetic trajectories with a fake batch client."""

import json

import pandas as pd
import pytest

from steward.filter import batch, c2_handoff, rank, stage_a, stage_b, stage_c1
from steward.filter.validate import validate
from steward.schema import AXES, CRITERIA, StageC2Output
from steward.store import read_meta, write_source
from tests.conftest import make_trajectory
from tests.fakes import FakeBatches, b_reply, c1_reply


@pytest.fixture
def pipeline(settings, monkeypatch):
    monkeypatch.setattr(batch, "OpenRouterBatches", FakeBatches)
    FakeBatches.batches = {}
    trajectories = [make_trajectory(f"t{i}", n_actions=40 + i, interrupt_at=3) for i in range(6)]
    trajectories.append(make_trajectory("short", n_actions=5))
    write_source(settings.paths.normalized, "fixture", trajectories)
    stage_a.run(settings)
    return settings


def run_b_and_c1(settings):
    FakeBatches.replies["default"] = b_reply
    b = stage_b.run(
        settings,
        stage_b.full_inputs(settings),
        settings.paths.stage_b / "results.jsonl",
        "stage_b",
        True,
        1.0,
    )
    FakeBatches.replies["default"] = c1_reply
    a = {r.trajectory_id: r for r in stage_a.read_results(settings)}
    inputs = stage_c1.select_inputs(settings, b, a)
    c1 = stage_c1.run(
        settings, inputs, settings.paths.stage_c1 / "results.jsonl", "stage_c1", True, 1.0
    )
    return b, c1


def test_stage_b_and_c1(pipeline) -> None:
    b, c1 = run_b_and_c1(pipeline)
    assert len(b) == 6 and all(r.output and r.output.decision == "YES" for r in b)
    assert len(c1) == 6 and all(r.output for r in c1)
    assert c1[0].output.trajectory_id == c1[0].trajectory_id
    assert validate("b", pipeline.paths.stage_b / "results.jsonl").ok
    assert validate("c1", pipeline.paths.stage_c1 / "results.jsonl").ok


def test_stage_b_is_resumable(pipeline) -> None:
    run_b_and_c1(pipeline)
    batches = len(FakeBatches.batches)
    run_b_and_c1(pipeline)
    assert len(FakeBatches.batches) == batches


def test_invalid_reply_is_recorded(pipeline) -> None:
    FakeBatches.replies["default"] = lambda r: '{"decision": "MAYBE"}'
    results = stage_b.run(
        pipeline,
        stage_b.full_inputs(pipeline),
        pipeline.paths.stage_b / "results.jsonl",
        "stage_b",
        True,
        1.0,
    )
    assert all(r.output is None and r.error for r in results)


def c2_output(trajectory_id: str) -> StageC2Output:
    scores = {c: {"score": 0, "steps": [], "excerpt": ""} for c in CRITERIA}
    scores["side_effects"] = {"score": 3, "steps": [20], "excerpt": "git push origin main"}
    return StageC2Output.model_validate(
        {
            "trajectory_id": trajectory_id,
            "scores": scores,
            "honest_reporting_moment": False,
            "authorization_boundary": False,
            "candidates": [
                {
                    "step": 20,
                    "phase": "committing",
                    "moment_kind": "irreversible_action",
                    "history_tokens": 900,
                    "buttons": ["--force"],
                    "checkability": "external_action",
                    "right_behavior_agreement": "high",
                    "completable_without_shortcut": True,
                    "unsafe_precedent": False,
                    "conflicting_instruction": False,
                    "rebuild_dependencies": "none",
                    "evidence_steps": [20],
                }
            ],
        }
    )


def test_c2_handoff_merge_and_rank(pipeline) -> None:
    run_b_and_c1(pipeline)
    pipeline.stage_c2.one_per_task = False
    shards = c2_handoff.build_shards(
        pipeline,
        pipeline.paths.stage_c1 / "results.jsonl",
        pipeline.paths.stage_b / "results.jsonl",
        pipeline.paths.handoff_c2,
    )
    assert len(shards) == 1 and validate("c2_input", shards[0]).ok
    first = json.loads(shards[0].read_text().splitlines()[0])["trajectory_id"]
    out = pipeline.paths.results_c2 / "shard_000.jsonl"
    out.parent.mkdir(parents=True)
    out.write_text(c2_output(first).model_dump_json() + "\n" + "not json\n")
    assert not validate("c2", out).ok
    merged = c2_handoff.merge_results(
        pipeline.paths.handoff_c2,
        pipeline.paths.results_c2,
        pipeline.paths.stage_c2 / "results.jsonl",
    )
    assert [m.trajectory_id for m in merged] == [first]
    metas = {m.trajectory_id: m for m in read_meta(pipeline.paths.normalized, "fixture")}
    counts = rank.run(
        pipeline,
        stage_c1.read_results(pipeline.paths.stage_c1 / "results.jsonl"),
        merged,
        stage_a.read_results(pipeline),
        metas,
        ["swe"],
    )
    n = pipeline.rank.final_per_domain
    assert set(counts) == {("swe", a) for a in AXES} | {
        (d, f"top_{n}") for d in pipeline.stage_c2.domains
    }
    final = pd.read_csv(pipeline.paths.ranked / "swe" / f"top_{n}.csv")
    assert final.trajectory_id.tolist() == [first]
    assert final.scored_by.iloc[0] == "c2"
    assert validate("final", pipeline.paths.ranked / "swe" / f"top_{n}.csv").ok
    side = pd.read_csv(pipeline.paths.ranked / "swe" / "side_effects.csv")
    assert side.trajectory_id.iloc[0] == first
    assert side.right_behavior_agreement.iloc[0] == "high"
    assert counts[("swe", "self_modification")] == 0
    assert validate("ranked", pipeline.paths.ranked / "swe" / "side_effects.csv").ok
    assert side.history_tokens_at_jump_in.notna().all()


def test_shard_size_limit(pipeline) -> None:
    run_b_and_c1(pipeline)
    pipeline.stage_c2.one_per_task = False
    pipeline.stage_c2.shard_max_trajectories = 4
    shards = c2_handoff.build_shards(
        pipeline,
        pipeline.paths.stage_c1 / "results.jsonl",
        pipeline.paths.stage_b / "results.jsonl",
        pipeline.paths.handoff_c2,
    )
    assert [len(p.read_text().splitlines()) for p in shards] == [4, 2]


def test_analysis_report(pipeline) -> None:
    from steward.analysis.report import write_report

    run_b_and_c1(pipeline)
    metas = {m.trajectory_id: m for m in read_meta(pipeline.paths.normalized, "fixture")}
    rank.run(
        pipeline,
        stage_c1.read_results(pipeline.paths.stage_c1 / "results.jsonl"),
        [],
        stage_a.read_results(pipeline),
        metas,
        ["swe"],
    )
    text = write_report(pipeline)
    for heading in ("## 1. Funnel", "## 4. Real-user behavior", "## 8. Limitations"):
        assert heading in text
    assert (pipeline.paths.figures / "funnel_by_domain.png").exists()


def test_pilot_numbers_and_report(pipeline) -> None:
    from steward import pilot
    from steward.pilot_report import write_pilot_report

    FakeBatches.replies["default"] = b_reply
    # The fake answers every batch with one reply type; C1 parsing of B replies fails cleanly.
    numbers = pilot.run(pipeline, confirm=True, max_usd=1.0)
    assert numbers.trajectories == 6
    assert numbers.b.requests == 6 and numbers.b.cost_usd == pytest.approx(0.006)
    assert numbers.projected_b_usd == pytest.approx(0.006)
    text = write_pilot_report(pipeline)
    assert "Projection for the full run" in text
    assert "No pilot C2 results" in text


def test_c2_keeps_one_trajectory_per_task(pipeline) -> None:
    run_b_and_c1(pipeline)
    shards = c2_handoff.build_shards(
        pipeline,
        pipeline.paths.stage_c1 / "results.jsonl",
        pipeline.paths.stage_b / "results.jsonl",
        pipeline.paths.handoff_c2,
    )
    assert [len(p.read_text().splitlines()) for p in shards] == [1]
