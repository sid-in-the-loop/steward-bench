"""Digests of stored sessions and their measures table."""

import csv
from pathlib import Path

from steward.cases.case import Case
from steward.cases.catalogue import Stored
from steward.cases.digest import OUTPUT_LIMIT, digest, digest_path, write_digests
from steward.cases.sessions import SessionSpec, Setting
from tests.cases.test_bench import stored


def entry(case: Case, spec: SessionSpec) -> Stored:
    run = stored(case, spec, Setting(items=2, changed=(2,), users=2))
    return Stored("gpt-6-luna:7", run, case, spec, "current")


def test_a_digest_names_the_session_and_puts_each_loop_under_its_measures(
    case: Case, spec: SessionSpec
) -> None:
    text = digest(entry(case, spec))
    lines = text.splitlines()
    assert lines[0] == "session gpt-6-luna:7: counts in the benchmark"
    assert f"harmful action: {case.harmful_action}" in lines
    loops = [line for line in lines if line.startswith("== loop")]
    assert loops[0].startswith("== loop 1 (control): ending")
    assert loops[1].startswith("== loop 2 (drift): ") and "harm no" in loops[1]
    assert any(line.startswith("[agent runs") for line in lines)
    assert any(line.startswith("[agent reports]") for line in lines)


def test_a_long_output_is_shortened_and_says_by_how_much(case: Case, spec: SessionSpec) -> None:
    stored_entry = entry(case, spec)
    result = next(e for e in stored_entry.run.events if e.kind == "result")
    result.text = "y" * (OUTPUT_LIMIT + 50)
    assert "[... 50 more characters]" in digest(stored_entry)


def test_digests_and_the_measures_table_are_written_per_session_and_loop(
    case: Case, spec: SessionSpec, tmp_path: Path
) -> None:
    one = entry(case, spec)
    sessions, loops = write_digests([one], tmp_path)
    assert (sessions, loops) == (1, 2)
    path = digest_path(tmp_path, one)
    assert path == tmp_path / case.id / "gpt-6-luna" / "I" / f"{one.run.setting.id}-t0-7.txt"
    assert path.read_text() == digest(one)
    rows = list(csv.DictReader((tmp_path / "measures.csv").open()))
    assert [(r["loop"], r["drift"], r["harm"]) for r in rows] == [
        ("1", "False", ""),
        ("2", "True", "no"),
    ]
