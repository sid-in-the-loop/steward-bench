import pytest

from steward.filter import batch
from steward.filter.batch import BatchRequest, CostGuardError
from steward.schema import CostRecord
from steward.store import append_jsonl, read_jsonl
from tests.fakes import FakeBatches, b_reply


def requests(n: int, size: int = 100) -> list[BatchRequest]:
    return [
        BatchRequest(custom_id=f"x:{i}", system="s", user="u" * size, max_tokens=10)
        for i in range(n)
    ]


def test_estimate_uses_list_price(settings) -> None:
    job = batch.estimate(requests(10, 2200), settings.model)
    assert job.requests == 10
    assert job.prompt_tokens == 10 * (1 + 1000 + 10)
    assert job.usd == pytest.approx((job.prompt_tokens * 0.05 + 100 * 0.25) / 1e6)


def test_guard_requires_confirm_and_respects_caps(settings) -> None:
    job = batch.estimate(requests(2), settings.model)
    with pytest.raises(CostGuardError, match="--confirm"):
        batch.check_budget(settings, job, 1.0, False)
    with pytest.raises(CostGuardError, match="max-usd"):
        batch.check_budget(settings, job.model_copy(update={"usd": 5.0}), 1.0, True)
    append_jsonl(
        settings.paths.costs,
        [
            CostRecord(
                batch_id="b",
                stage="stage_b",
                model="m",
                requests=1,
                prompt_tokens=1,
                completion_tokens=1,
                cost_usd=14.99,
                submitted_at="2026-01-01T00:00:00Z",
            )
        ],
    )
    with pytest.raises(CostGuardError, match="cap"):
        batch.check_budget(settings, job.model_copy(update={"usd": 0.5}), 1.0, True)
    with pytest.raises(CostGuardError, match="cap"):
        batch.check_budget(settings, job.model_copy(update={"usd": 0.02}), 1.0, True)


def test_prompt_size_guard(settings) -> None:
    with pytest.raises(CostGuardError):
        batch.check_prompt_sizes(requests(1, 400_000), settings.model)


def test_chunking_by_count() -> None:
    parts = batch.chunk(requests(5), 2)
    assert [len(p) for p in parts] == [2, 2, 1]


def test_parse_json_object_tolerates_fences() -> None:
    assert batch.parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ValueError):
        batch.parse_json_object("no json")


def test_submit_and_collect_resume(settings, monkeypatch) -> None:
    monkeypatch.setattr(batch, "OpenRouterBatches", FakeBatches)
    FakeBatches.replies["default"] = b_reply
    batch.submit(settings, "stage_b", requests(3))
    assert batch.submitted_ids(settings, "stage_b") == {"x:0", "x:1", "x:2"}
    results = batch.collect(settings, "stage_b")
    assert {r.custom_id for r in results} == {"x:0", "x:1", "x:2"}
    assert all(r.text for r in results)
    costs = read_jsonl(settings.paths.costs, CostRecord)
    assert len(costs) == 1 and costs[0].cost_usd == pytest.approx(0.003)
    again = batch.collect(settings, "stage_b")
    assert len(again) == 3
    assert len(read_jsonl(settings.paths.costs, CostRecord)) == 1


def test_wire_ids_are_provider_safe_and_distinct() -> None:
    ids = ["openagentsafety:claude-sonnet/safety-issue-reporting", "swe_chat:a/b", "swe_chat:a_b"]
    wires = [batch.wire_id(i) for i in ids]
    assert len(set(wires)) == 3
    assert all(len(w) <= 64 and w.replace("_", "").replace("-", "").isalnum() for w in wires)
