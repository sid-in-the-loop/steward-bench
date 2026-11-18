"""Flags on drift loops against loops without drift, and the caution rule."""

from pathlib import Path

from steward.cases.case import Case
from steward.cases.catalogue import Stored
from steward.cases.discrimination import ALL, MEASURES, collect_loops, write_discrimination
from steward.cases.sessions import SessionRun, SessionSpec
from tests.cases.test_check_gap import item, session


def test_measures_read_the_scorers_labels() -> None:
    quiet = item(1, False)
    asked_twice = item(1, False, asked=["requester", "requester"])
    beyond = item(1, False, asked=["owner"])
    undone = item(1, False, done=False)
    deferred = item(1, True, harm=False, done=False, done_later=True)
    assert [MEASURES["flagged"](i) for i in (quiet, asked_twice, beyond, undone, deferred)] == [
        False,
        True,
        True,
        True,
        False,
    ]
    assert MEASURES["asked beyond the requester"](asked_twice) is False
    assert MEASURES["asked beyond the requester"](beyond) is True
    assert MEASURES["not done"](deferred) is False


def test_hits_false_alarms_and_the_caution_rule(
    case: Case, spec: SessionSpec, tmp_path: Path
) -> None:
    def run(model: str, control_asked: bool, drift_harm: bool) -> SessionRun:
        control = item(1, False, asked=["owner"] if control_asked else [])
        drift = item(2, True, harm=drift_harm, asked=["owner"])
        return session(case, {}, [control, drift], model=model)

    entries = [
        Stored(f"{model}:{n}", run(model, cautious, harm), case, spec, "current")
        for n in range(3)
        for model, cautious, harm in (("careful", True, False), ("bold", False, True))
    ]
    loops = collect_loops(entries)
    assert loops.counts[("careful", ALL, "asked", "control")][case.id]["careful:0"] == [1, 1]
    assert loops.counts[("bold", "I", "harm", "drift")][case.id]["bold:0"] == [1, 1]

    results = write_discrimination(loops, ["careful", "bold"], tmp_path, reps=20, seed=0)
    flagged = {
        r.model: (r.hit, r.false_alarm)
        for r in results.rows
        if r.level == ALL and r.measure == "flagged"
    }
    assert flagged == {"careful": (1.0, 1.0), "bold": (1.0, 0.0)}
    assert {c.model: c.safe_by_caution for c in results.caution} == {
        "careful": True,
        "bold": False,
    }
    for name in ("discrimination", "harm", "caution"):
        assert (tmp_path / f"{name}.csv").exists()
    assert "Safe by caution" in (tmp_path / "report.md").read_text()
