"""The viewer's records and page, from a session run on a fake environment."""

import json
from pathlib import Path

from steward.cases.bench_view import (
    INLINE_LIMIT,
    session_record,
    setting_in_words,
    write_view,
)
from steward.cases.case import Case
from steward.cases.level_settings import several_drifts
from steward.cases.levels import Voice
from steward.cases.sessions import SessionSpec, Setting
from tests.cases.test_bench import stored

NAMES = {"owner": "Mara Ruiz", "bystander": "Tomas Lind", "manager": "Ingrid Solberg"}


def test_a_setting_reads_as_one_sentence() -> None:
    baseline = setting_in_words(Setting(), {"requester": "Dragan Petrovic", **NAMES})
    assert baseline == (
        "16 loops; drift on loop 9; users: Dragan Petrovic (requester), Mara Ruiz (owner); "
        "users speak unprompted at the regular rate; handover notes out of date."
    )
    voices = (
        Voice(role="owner", rate=0.5, noise=0.8, warns=True),
        Voice(role="bystander", rate=0),
        Voice(role="manager", pushes=False),
    )
    drawn = Setting(level="III", seed=1, changed=(4, 11), users=4, voices=voices, notes="missing")
    words = setting_in_words(drawn, NAMES)
    assert "drift on loops 4 and 11" in words
    assert (
        "Mara Ruiz (owner) speaks at 50% of handovers, 80% of it unrelated, says what changed"
        in words
    )
    assert "Tomas Lind (bystander) answers when asked" in words
    assert "Ingrid Solberg (manager) speaks at 100% of handovers, does not push" in words
    assert words.endswith("no handover notes.")
    assert "second person changes the same object" in setting_in_words(several_drifts(0), NAMES)
    assert "no drift loop" in setting_in_words(Setting(changed=()), NAMES)


def test_a_session_record_carries_the_people_the_counts_and_every_loop(
    case: Case, spec: SessionSpec
) -> None:
    record = session_record(stored(case, spec, Setting(items=2, changed=(2,), users=2)), case, spec)
    assert record["title"] == case.title and record["domain"] == "swe"
    assert [p["role"] for p in record["people"]] == ["requester", "owner", "bystander", "manager"]
    assert (record["loops"], record["drift_loops"], record["harmed"]) == (2, 1, 0)
    assert [loop["drift"] for loop in record["transcript"]] == [False, True]
    kinds = [event["kind"] for event in record["transcript"][0]["events"]]
    assert kinds[0] == "message" and kinds[-1] == "report"
    assert record["problems"] == []


def test_the_page_carries_small_data_and_writes_large_data_beside_it(
    case: Case, spec: SessionSpec, tmp_path: Path
) -> None:
    record = session_record(stored(case, spec, Setting(items=2, changed=(2,), users=2)), case, spec)
    page, files = write_view([record], tmp_path / "small", "Sample")
    text = page.read_text()
    assert files == 0 and "<title>Sample</title>" in text and "__DATA__" not in text
    embedded = text.split('<script type="application/json" id="data">')[1].split("</script>")[0]
    data = json.loads(embedded)
    assert data["sessions"][0]["file"] == "swe-I-m" and "transcript" not in data["sessions"][0]
    assert data["inline"]["swe-I-m"][0]["transcript"][1]["drift"] is True

    record["transcript"][0]["events"][0]["text"] = "x" * (INLINE_LIMIT + 1)
    page, files = write_view([record], tmp_path / "large", "Sample")
    assert files == 1
    beside = json.loads((tmp_path / "large" / "data" / "swe-I-m.json").read_text())
    assert beside[0]["id"] == record["id"]
    assert '"inline":null' in page.read_text()
