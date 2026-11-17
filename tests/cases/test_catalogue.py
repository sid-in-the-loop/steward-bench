"""Which stored sessions count in the benchmark, which were replaced or run on an earlier
version, and how renamed cases are found."""

import json
import shutil
from pathlib import Path

from steward.cases.case import Case
from steward.cases.catalogue import catalogue, stored_models
from steward.cases.session_commands import sessions_path
from steward.cases.sessions import SessionRun, SessionSpec, Setting
from steward.config import Settings
from tests.cases.conftest import ROOT
from tests.cases.test_bench import stored


def copy_case(settings: Settings, case: Case) -> Path:
    cases = settings.paths.root / "cases"
    for shared in ("_bases", "_exchanges"):
        if (ROOT / shared).exists():
            shutil.copytree(ROOT / shared, cases / shared)
    shutil.copytree(ROOT / case.id, cases / case.id)
    return cases / case.id


def store(settings: Settings, model: str, runs: list[SessionRun]) -> None:
    path = sessions_path(settings, model)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(run.model_dump_json() + "\n" for run in runs))


def test_each_stored_session_is_given_its_case_and_status(
    settings: Settings, case: Case, spec: SessionSpec
) -> None:
    copy_case(settings, case)
    (settings.paths.data / "cases").mkdir(parents=True)
    (settings.paths.data / "cases" / "former_identifiers.json").write_text(
        json.dumps({"s9": case.id})
    )
    baseline = stored(case, spec, Setting())
    renamed = baseline.model_copy(update={"case": "s9"})
    older = baseline.model_copy(update={"session_version": "0000000000"})
    gone = baseline.model_copy(update={"case": "no-such-case"})
    store(settings, "gpt-6-luna", [baseline, renamed, older, gone, baseline])
    store(settings, "another-model", [baseline])

    assert stored_models(settings) == ["gpt-6-luna", "another-model"]
    entries = list(catalogue(settings))
    assert [(e.id, e.status) for e in entries] == [
        ("gpt-6-luna:1", "replaced"),
        ("gpt-6-luna:2", "earlier"),
        ("gpt-6-luna:3", "earlier"),
        ("gpt-6-luna:5", "current"),
        ("another-model:1", "current"),
    ]
    assert {e.case.id for e in entries} == {case.id}

    aside = sessions_path(settings, "gpt-6-luna").parent / "set_aside" / "gpt-6-luna.jsonl"
    aside.parent.mkdir()
    aside.write_text(baseline.model_dump_json() + "\n")
    last = list(catalogue(settings, ["gpt-6-luna"]))[-1]
    assert (last.id, last.status) == ("gpt-6-luna:set_aside:1", "set_aside")


def test_a_session_of_a_case_that_changes_during_a_loop_is_outside_the_benchmark(
    settings: Settings, case: Case, spec: SessionSpec
) -> None:
    folder = copy_case(settings, case)
    (folder / "during.sh").write_text("echo changed\n")
    session = folder / "session.yaml"
    session.write_text(session.read_text() + "\nduring: during.sh\n")
    store(settings, "gpt-6-luna", [stored(case, spec, Setting())])
    assert [e.status for e in catalogue(settings)] == ["outside"]
