"""The task linter finds shared names, copied sentences and loose disclosure patterns."""

import shutil
from pathlib import Path

from steward.task import BUILDING_MARKER
from steward.task_lint import lint, scaled_directories

ROOT = Path(__file__).parents[2] / "tasks"
FIXTURES = Path(__file__).parent / "fixtures"


def copy_task(source: str, root: Path, name: str) -> Path:
    # Copied without a building marker, so a task another builder is editing can still serve.
    target = root / name
    found = ROOT / source if (ROOT / source).exists() else FIXTURES / source
    shutil.copytree(found, target, ignore=shutil.ignore_patterns(BUILDING_MARKER))
    return target


def test_only_tasks_with_a_confused_deputy_are_scaled_tasks(tmp_path: Path) -> None:
    copy_task("terminal-589aa59f3f", tmp_path, "terminal-589aa59f3f")
    copy_task("swe-082dc18eaa", tmp_path, "swe-082dc18eaa")
    names = {d.name for d in scaled_directories(tmp_path)}
    assert names == {"terminal-589aa59f3f"}


def test_a_task_copied_under_another_name_shares_every_name_and_sentence(tmp_path: Path) -> None:
    source = "terminal-589aa59f3f"
    shutil.copytree(ROOT / "_bases", tmp_path / "_bases")
    copy_task(source, tmp_path, source)
    copy = copy_task(source, tmp_path, "terminal-0000000000")
    task = (copy / "task.yaml").read_text().replace(f"id: {source}", "id: terminal-0000000000")
    (copy / "task.yaml").write_text(task)
    findings = lint(tmp_path)
    assert any(f.startswith("full name") and "2 tasks" in f for f in findings)
    assert not any(f.startswith("template sentence") for f in findings)


def test_a_task_being_built_is_skipped_until_its_marker_is_removed(tmp_path: Path) -> None:
    from steward.task import task_directories

    for name in ("swe-done", "swe-half"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "task.yaml").write_text("id: x\n")
    (tmp_path / "swe-half" / BUILDING_MARKER).write_text("")
    assert [d.name for d in task_directories(tmp_path)] == ["swe-done"]
    assert [d.name for d in task_directories(tmp_path, ["swe-half"])] == ["swe-done", "swe-half"]
    (tmp_path / "swe-half" / BUILDING_MARKER).unlink()
    assert [d.name for d in task_directories(tmp_path)] == ["swe-done", "swe-half"]
