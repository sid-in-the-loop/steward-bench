"""The stratified sample of stored sessions for checking the scorer's labels."""

import csv
from pathlib import Path

from steward.cases.case import Case
from steward.cases.catalogue import Stored
from steward.cases.digest import digest_path
from steward.cases.sessions import SessionSpec, Setting
from steward.cases.validation_sample import (
    CHECKLIST,
    SAMPLE_TABLE,
    Key,
    Light,
    Outcome,
    allocate,
    draw,
    key_of,
    outcome_of,
    write_sample,
)
from tests.cases.test_bench import stored


def lights(model: str, level: str, outcome: Outcome, count: int) -> list[Light]:
    return [
        Light(
            key=Key(model=model, case=f"c{n % 3}", setting=f"{level}-{outcome}-{n}", trial=0),
            level=level,
            outcome=outcome,
        )
        for n in range(count)
    ]


def test_every_group_gets_its_minimum_and_the_rest_goes_by_size() -> None:
    sizes = {("m", "I", "harm"): 90, ("m", "I", "no harm"): 9, ("m", "II", "no drift"): 1}
    counts = allocate(sizes, total=20, minimum=2)
    assert counts[("m", "II", "no drift")] == 1
    assert counts[("m", "I", "no harm")] >= 2
    assert sum(counts.values()) == 20
    assert counts[("m", "I", "harm")] > counts[("m", "I", "no harm")]


def test_a_sample_never_draws_more_than_a_group_has() -> None:
    counts = allocate({("m", "I", "harm"): 3, ("m", "I", "no harm"): 2}, total=50, minimum=1)
    assert counts == {("m", "I", "harm"): 3, ("m", "I", "no harm"): 2}


def test_the_draw_is_the_same_whatever_order_the_sessions_come_in() -> None:
    pool = lights("m", "I", "harm", 40) + lights("m", "III", "no harm", 25)
    first = draw(pool, total=10, minimum=2, seed=0)
    assert first == draw(list(reversed(pool)), total=10, minimum=2, seed=0)
    assert len(first) == 10 and len(set(first)) == 10
    assert sum(k.setting.startswith("III") for k in first) >= 2


def entry(case: Case, spec: SessionSpec, line: int, setting: Setting) -> Stored:
    return Stored(f"m:{line}", stored(case, spec, setting), case, spec, "current")


def test_the_sample_table_keeps_verdicts_across_reruns(
    case: Case, spec: SessionSpec, tmp_path: Path
) -> None:
    drifting = entry(case, spec, 1, Setting(items=2, changed=(2,)))
    calm = entry(case, spec, 2, Setting(items=2, changed=()))
    assert outcome_of(drifting.run) == "no harm"
    assert outcome_of(calm.run) == "no drift"
    out, digests = tmp_path / "validation", tmp_path / "digests"
    chosen = [key_of(drifting.run), key_of(calm.run)]

    assert write_sample([drifting, calm], chosen, out, digests) == (2, 0)
    assert digest_path(digests, drifting).exists()
    rows = list(csv.DictReader((out / SAMPLE_TABLE).open()))
    assert rows[0]["drift_labels"].startswith("loop 2: harm no")
    assert rows[1]["drift_loops"] == ""
    assert "Digest:" in (out / CHECKLIST).read_text()

    rows[0]["verdict"], rows[0]["note"] = "wrong", "asked first"
    with (out / SAMPLE_TABLE).open("w", newline="") as table:
        writer = csv.DictWriter(table, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    # A later draw without the checked session keeps it and its verdict.
    assert write_sample([calm], [key_of(calm.run)], out, digests) == (2, 1)
    again = list(csv.DictReader((out / SAMPLE_TABLE).open()))
    assert [(r["session"], r["verdict"]) for r in again] == [("m:2", ""), ("m:1", "wrong")]
