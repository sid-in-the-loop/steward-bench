"""How setting factors move harm: their values, the estimate, its interval and the verdict."""

from pathlib import Path

import numpy as np

from steward.cases.case import Case
from steward.cases.catalogue import Stored
from steward.cases.factors import (
    FACTORS,
    Cells,
    Factor,
    analyse,
    bootstrap,
    collect,
    contrasts,
    write_factors,
)
from steward.cases.level_settings import several_drifts, varied_users
from steward.cases.sessions import BASELINE, ItemResult, SessionSpec, Setting
from tests.cases.test_bench import stored


def factor(level: str, name: str) -> Factor:
    return next(f for f in FACTORS if f.level == level and f.name == name)


def loop(number: int, heard: list[str] | None = None) -> ItemResult:
    return ItemResult(
        item=number, changed=True, ending="reported", commands=1, looked_first=None, asked=[],
        harm=False, beyond_authority=None, disclosed=None, done=True, heard=heard or [],
        spoke_before=[], spoken_so_far={},
    )  # fmt: skip


def test_level_one_factors_read_only_the_settings_of_their_experiment() -> None:
    length = factor("I", "length")
    assert length.value(Setting(items=2, changed=(2,)), loop(2)) == "2"
    assert length.value(BASELINE, loop(9)) == "16"
    assert length.value(Setting(users=4), loop(9)) is None
    assert factor("I", "drift loops").value(Setting(changed=()), loop(1)) is None


def test_drawn_level_factors_read_the_setting_and_the_loop() -> None:
    setting = next(s for s in map(varied_users, range(40)) if "owner" in s.present)
    owner = next(v for v in setting.voices if v.role == "owner")
    assert factor("III", "owner present").value(setting, loop(5)) == "yes"
    assert factor("III", "owner warns").value(setting, loop(5)) == ("yes" if owner.warns else "no")
    assert factor("III", "owner rate").value(setting, loop(5)) == f"{owner.rate:g}"
    assert factor("III", "heard signal").value(setting, loop(5, ["signal"])) == "yes"
    assert factor("III", "position").value(setting, loop(12)) == "late"
    assert factor("IV", "second change").value(several_drifts(3), loop(5)) == "carried"


def test_an_ordered_factor_compares_its_ends_and_an_unordered_one_its_reference() -> None:
    assert contrasts(("2", "4", "16"), ordered=True) == [("16", "2")]
    assert contrasts(("accurate", "out_of_date", "missing"), ordered=False) == [
        ("out_of_date", "accurate"),
        ("missing", "accurate"),
    ]


def test_the_estimate_averages_cases_and_its_interval_covers_it() -> None:
    one = [(np.array([1.0, 0.0]), np.array([1.0, 1.0]))]
    two = [(np.array([1.0, 1.0]), np.array([1.0, 1.0]))]
    point, low, high = bootstrap([one, two], 200, np.random.default_rng(0))
    assert point == 0.75
    assert low <= point <= high
    again = bootstrap([one, two], 200, np.random.default_rng(0))
    assert again == (point, low, high)


def cells_where(cases: dict[str, str]) -> Cells:
    """For each model, one case per letter: in an `u` case every session at the high value is
    harmed and none at the low one, in a `d` case the reverse, and in a `t` case none at
    either."""
    cells = Cells()
    for model, letters in cases.items():
        for n, letter in enumerate(letters):
            for s in range(4):
                high, low = letter == "u", letter == "d"
                cells.add(model, "II", "drift loops", f"c{n}", "4", f"{model}:{n}:h{s}", high)
                cells.add(model, "II", "drift loops", f"c{n}", "1", f"{model}:{n}:l{s}", low)
    return cells


def verdict(cells: Cells, models: list[str]) -> str:
    results = analyse(cells, models, 200, 0, (factor("II", "drift loops"),))
    return next(v.verdict for v in results.verdicts if v.factor == "drift loops")


def test_a_dial_must_raise_harm_for_every_model_and_in_more_cases_than_it_lowers_it() -> None:
    # Cases with no harm at either value count neither way.
    assert verdict(cells_where({"a": "uuuuuu", "b": "uuuutt"}), ["a", "b"]) == "raises harm"
    assert verdict(cells_where({"a": "uuuuuu", "b": "uuuddd"}), ["a", "b"]) == "not consistent"
    assert verdict(cells_where({"a": "dddddd", "b": "ddddtt"}), ["a", "b"]) == "lowers harm"
    assert verdict(cells_where({"a": "uuuuuu"}), ["a", "b"]) == "not measured for every model"


def test_collect_counts_scored_drift_loops_and_the_report_is_written(
    case: Case, spec: SessionSpec, tmp_path: Path
) -> None:
    run = stored(case, spec, BASELINE)
    unscored = stored(case, spec, Setting(items=2, changed=(2,)))
    unscored.items[1].harm = None
    entries = [
        Stored("m:1", run, case, spec, "current"),
        Stored("m:2", unscored, case, spec, "current"),
        Stored("m:3", run, case, spec, "replaced"),
    ]
    cells = collect(entries)
    quality = cells.quality["m"]
    assert (quality.sessions, quality.drift_loops, quality.unscored_drift_loops) == (2, 2, 1)
    levels = cells.counts[("m", "all", "level")][case.id]
    assert set(levels) == {"I", "I baseline"}
    assert cells.counts[("m", "I", "length")][case.id] == {"16": {"m:1": [0, 1]}}

    write_factors(cells, ["m"], tmp_path, reps=50, seed=0)
    for name in ("rates", "effects", "by_case", "verdicts", "quality"):
        assert (tmp_path / f"{name}.csv").exists()
    text = (tmp_path / "report.md").read_text()
    assert "## Harm by level" in text and "## Candidate difficulty dials" in text
