"""The harness version: the loop's protocol and a hash of the texts it sends."""

import pytest

from steward.cases import changes, simulator
from steward.cases.harness import harness_version


def test_rewording_any_text_sent_makes_a_new_harness(monkeypatch: pytest.MonkeyPatch) -> None:
    before = harness_version(5, ["closing"])
    assert before.startswith("5-") and before == harness_version(5, ["closing"])
    assert harness_version(6, ["closing"]) != before
    assert harness_version(5, ["closing, reworded"]) != before
    monkeypatch.setattr(simulator, "BRIEFED", simulator.BRIEFED + " ")
    assert harness_version(5, ["closing"]) != before
    monkeypatch.undo()
    monkeypatch.setattr(changes, "NOTES_OPENING", "Day {day}: {body}")
    assert harness_version(5, ["closing"]) != before
