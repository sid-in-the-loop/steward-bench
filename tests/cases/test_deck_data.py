"""The presentation's numbers, computed on a few hand-made loops."""

from pathlib import Path

import numpy as np
import pytest

from steward.cases.deck_data import (
    HIDDEN,
    alarm,
    build_deck,
    done_of,
    grouped_difference,
    outcomes,
    read_stale,
    split,
    tradeoff_by_case,
    visibility_of,
    write_deck,
)
from steward.cases.digest import LoopMeasures

VISIBLE_CASE = "swe-082dc18eaa"
HIDDEN_CASE = sorted(HIDDEN)[0]


def loop(case: str = VISIBLE_CASE, session: str = "m:1", **over: object) -> LoopMeasures:
    fields: dict[str, object] = {
        "session": session,
        "status": "current",
        "model": "m",
        "case": case,
        "domain": case.split("-")[0],
        "level": "III",
        "setting": "s",
        "trial": 0,
        "loops": 16,
        "drift_loops": "9",
        "users": 3,
        "notes": "out_of_date",
        "loop": 9,
        "drift": True,
        "carried": False,
        "second": "",
        "ending": "reported",
        "commands": 3,
        "looked_first": "no",
        "asked": "",
        "asked_first": "",
        "harm": "no",
        "second_harm": "",
        "beyond_authority": "no",
        "disclosed": "",
        "done": "yes",
        "done_later": "",
        "heard": "",
        "spoke_before": "",
        "cost_usd": 0.0,
    }
    fields.update(over)
    return LoopMeasures.model_validate(fields)


def test_visibility_reads_notes_signal_and_case() -> None:
    assert visibility_of(loop(notes="accurate")) == "Announced"
    assert visibility_of(loop(heard="noise signal")) == "Announced"
    assert visibility_of(loop(case=HIDDEN_CASE)) == "Hidden"
    assert visibility_of(loop()) == "Visible"


def test_done_uses_the_deferred_check_where_there_is_one() -> None:
    assert done_of(loop(done="no", done_later="yes"))
    assert not done_of(loop(done="yes", done_later="no"))
    assert alarm(loop(drift=False, asked="requester"))
    assert alarm(loop(drift=False, done="no"))
    assert not alarm(loop(drift=False))


def test_split_shares_add_up_to_one_within_each_case() -> None:
    rows = [loop(harm="yes"), loop(asked_first="owner"), loop(), loop(asked_first="bystander")]
    s = split(rows)
    assert (s.harm, s.asked, s.quiet) == (0.25, 0.25, 0.5)
    assert s.shifts == 4


def test_outcomes_count_harm_with_the_task_done() -> None:
    rows = [loop(harm="yes"), loop(harm="yes", done="no"), loop(), loop(done="no")]
    o = outcomes(rows)
    assert (o.safe_done, o.safe_not, o.harm_not, o.harm_done) == (0.25, 0.25, 0.25, 0.25)
    assert o.done_given_harm == 0.5


def test_tradeoff_reads_drift_and_routine_loops_separately() -> None:
    rows = [
        loop(harm="yes"),
        loop(harm="no"),
        loop(drift=False, harm="", asked="owner"),
        loop(drift=False, harm=""),
        loop(drift=False, harm="", carried=True, asked="owner"),
    ]
    t = tradeoff_by_case(rows)[VISIBLE_CASE]
    assert (t.harm, t.alarm, t.done) == (0.5, 0.5, 1.0)


def test_grouped_difference_averages_kinds_within_cases() -> None:
    rows = [
        loop(heard="noise", harm="yes"),
        loop(heard="", harm="no"),
        loop(heard="side_talk", harm="no"),
    ]
    d = grouped_difference(rows, ("noise", "side_talk"), np.random.default_rng(0), 50)
    # noise: 1.0 against 0.0 (the side_talk loop counts as not hearing noise) gives +1.0;
    # side_talk: 0.0 against 0.5 gives -0.5; the mean is +0.25.
    assert d.diff == pytest.approx(0.25)
    assert d.cases == 1


def test_build_and_write_the_deck(tmp_path: Path) -> None:
    rates = tmp_path / "rates.csv"
    rates.write_text(
        "model,level,factor,value,cases,sessions,loops,harmed_loops,rate,low,high,session_rate\n"
        "m,loops with a change,stale,this loop,1,1,3,1,0.33,0.1,0.5,0.3\n"
        "m,loops with a change,stale,earlier loop,1,1,1,1,1.0,1.0,1.0,1.0\n"
        "m,loops with a change,stale,never,1,1,0,0,0.0,0.0,0.0,0.0\n"
    )
    stale = read_stale(rates, ["m"])
    assert stale["m"]["this"].share == 0.75
    assert stale["m"]["earlier"].harm_share == 0.5
    rows = []
    for case in (VISIBLE_CASE, HIDDEN_CASE):
        for n, heard in enumerate(("", "pressure", "hearsay", "noise", "pile_up", "disagreement")):
            rows.append(
                loop(case=case, session=f"m:{n}", heard=heard, harm="yes" if n % 2 else "no")
            )
            rows.append(loop(case=case, session=f"m:{n}", heard=heard, notes="accurate"))
            rows.append(loop(case=case, session=f"m:{n}", heard="signal", notes="missing"))
            rows.append(
                loop(
                    case=case,
                    session=f"m:{n}",
                    notes="missing",
                    heard="approval side_talk shop_talk retraction",
                )
            )
            rows.append(loop(case=case, session=f"m:{n}", drift=False, harm=""))
    deck = build_deck(rows, stale, ["m"], reps=20, seed=0)
    model = deck.by_model["m"]
    assert set(model.visibility) == {"Announced", "Visible", "Hidden"}
    assert model.visibility["Announced"].rate == 0.0
    assert set(model.social) == {"pile_up", "disagreement", "approval", "hearsay", "unrelated"}
    assert model.pressure["pressure"].harm == 1.0
    assert set(deck.by_case) == {VISIBLE_CASE, HIDDEN_CASE}
    out = tmp_path / "deck.js"
    write_deck(deck, out)
    assert out.read_text().startswith("window.DECK = {")
