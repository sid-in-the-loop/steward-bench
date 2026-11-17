"""The viewer's records and page, from a session run on a fake environment."""

import gzip
import json
from pathlib import Path

from steward.cases.bench_view import (
    FULL_ONLY,
    INLINE_LIMIT,
    collect,
    config_of,
    session_record,
    write_view,
)
from steward.cases.case import Case
from steward.cases.level_settings import several_drifts
from steward.cases.levels import Voice
from steward.cases.sessions import SessionSpec, Setting
from steward.config import Settings
from tests.cases.test_bench import stored
from tests.cases.test_catalogue import copy_case, store

NAMES = {"owner": "Mara Ruiz", "bystander": "Tomas Lind", "manager": "Ingrid Solberg"}


def test_a_setting_is_laid_out_as_tags_rows_and_users() -> None:
    baseline = config_of(Setting(), {"requester": "Dragan Petrovic", **NAMES})
    assert baseline["tags"] == ["16 loops", "1 drift", "2 users"]
    assert ["Drift loops", "9"] in baseline["rows"]
    assert ["Handover notes", "out of date"] in baseline["rows"]
    assert [(u["name"], u["speaks"]) for u in baseline["users"]] == [
        ("Dragan Petrovic", "gives each loop's task"),
        ("Mara Ruiz", "unprompted, regular rate"),
    ]
    voices = (
        Voice(role="owner", rate=0.5, noise=0.8, warns=True),
        Voice(role="bystander", rate=0),
        Voice(role="manager", pushes=False),
    )
    drawn = Setting(level="III", seed=1, changed=(4, 11), users=4, voices=voices, notes="missing")
    config = config_of(drawn, NAMES)
    assert ["Drift loops", "4 and 11"] in config["rows"] and ["Seed", "1"] in config["rows"]
    owner, bystander, manager = config["users"][1:]
    assert (owner["speaks"], owner["unrelated"], owner["stance"]) == (
        "50% of handovers",
        "80%",
        "says what changed",
    )
    assert bystander["speaks"] == "only when asked"
    assert manager["stance"] == "does not push"
    assert "second: same" in config_of(several_drifts(0), NAMES)["tags"]
    assert config_of(Setting(changed=()), NAMES)["tags"][1] == "no drift"


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
    page, files, _ = write_view([record], tmp_path / "small", "Sample")
    text = page.read_text()
    assert files == 0 and "<title>Sample</title>" in text and "__DATA__" not in text
    embedded = text.split('<script type="application/json" id="data">')[1].split("</script>")[0]
    data = json.loads(embedded)
    entry = data["sessions"][0]
    assert entry["file"] == "swe-I-m" and not set(FULL_ONLY) & set(entry)
    assert entry["status"] == "current" and data["statuses"]["current"]
    assert data["inline"]["swe-I-m"][0]["transcript"][1]["drift"] is True

    large = dict(record, id="second")
    large["transcript"] = json.loads(json.dumps(record["transcript"]))
    large["transcript"][0]["events"][0]["text"] = "x" * (INLINE_LIMIT + 1)
    page, files, _ = write_view([record, large], tmp_path / "large", "Sample")
    assert files == 1
    with gzip.open(tmp_path / "large" / "data" / "swe-I-m.json.gz", "rt") as beside:
        assert [r["id"] for r in json.load(beside)] == [record["id"], "second"]
    assert '"inline":null' in page.read_text()


def test_the_viewer_collects_every_stored_session_with_its_status(
    settings: Settings, case: Case, spec: SessionSpec
) -> None:
    copy_case(settings, case)
    baseline = stored(case, spec, Setting())
    store(settings, "gpt-6-luna", [baseline, baseline])
    records = list(collect(settings))
    assert [(r["id"], r["status"]) for r in records] == [
        ("gpt-6-luna:1", "replaced"),
        ("gpt-6-luna:2", "current"),
    ]
    assert list(collect(settings, levels=["II"])) == []
