"""The price table and the refusal to run a model without a price or an endpoint."""

from pathlib import Path

import pytest

from steward.cli import build_parser, run_benchmark
from steward.config import Paths, Settings
from steward.models import MODELS
from steward.prices import PRICES, UnpricedModelError, pricing_of
from steward.session import SessionSpec
from steward.task import Case

BENCHMARK_MODELS = [
    "gpt-6-luna",
    "gpt-6.2-sol",
    "gpt-6.1-terra",
    "claude-opus-5-5",
    "claude-haiku-5-5",
    "claude-sonnet-5-5",
    "muse-wonder-1.0",
    "muse-spark-1.3",
    "muse-ember-1.0",
    "gemini-4-argon",
    "gemini-4-neon",
    "gemini-4-carbon",
    "gemini-4-hydrogen",
]


def test_every_benchmark_model_has_a_price_with_its_source_and_date() -> None:
    for name in BENCHMARK_MODELS:
        price = PRICES[name]
        assert price.source.startswith("https://") and price.looked_up == "2026-11-27"
        assert price.pricing.cached is not None
        assert price.pricing.cached < price.pricing.prompt < price.pricing.completion


def test_every_runnable_model_is_at_its_listed_price() -> None:
    for name, model in MODELS.items():
        assert model.pricing == PRICES[name].pricing


def test_a_model_without_a_price_has_none_assumed() -> None:
    with pytest.raises(UnpricedModelError, match="no price confirmed"):
        pricing_of("some-model-1")


@pytest.mark.parametrize(
    ("name", "reason"), [("some-model-1", "no price confirmed"), ("some-model-2", "no endpoint")]
)
def test_the_run_refuses_a_model_it_cannot_price_or_reach(
    name: str,
    reason: str,
    case: Case,
    full: SessionSpec,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Every priced model has an endpoint, so one with a price alone is made for the test.
    monkeypatch.setitem(PRICES, "some-model-2", PRICES["gpt-6-luna"])
    settings = Settings(paths=Paths(root=tmp_path, data=tmp_path))
    argv = ["run", "--model", name, "--condition", "sd", "--confirm"]
    assert run_benchmark(settings, build_parser().parse_args(argv), [(case, full)]) == 2
    assert reason in capsys.readouterr().err
