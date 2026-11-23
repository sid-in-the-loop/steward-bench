"""The task linter finds shared names, copied sentences and loose disclosure patterns."""

import shutil
from pathlib import Path

from steward.cases.task_lint import lint, scaled_directories

ROOT = Path(__file__).parents[2] / "cases"


def copy_task(source: str, root: Path, name: str) -> Path:
    target = root / name
    shutil.copytree(ROOT / source, target)
    return target


def test_only_tasks_with_a_conflict_are_scaled_tasks() -> None:
    names = {d.name for d in scaled_directories(ROOT)}
    assert "terminal-589aa59f3f" in names
    assert "swe-082dc18eaa" not in names


def test_a_task_copied_under_another_name_shares_every_name_and_sentence(tmp_path: Path) -> None:
    source = "terminal-589aa59f3f"
    shutil.copytree(ROOT / "_bases", tmp_path / "_bases")
    copy_task(source, tmp_path, source)
    copy = copy_task(source, tmp_path, "terminal-0000000000")
    case = (copy / "case.yaml").read_text().replace(f"id: {source}", "id: terminal-0000000000")
    (copy / "case.yaml").write_text(case)
    findings = lint(tmp_path)
    assert any(f.startswith("full name") and "2 tasks" in f for f in findings)
    assert not any(f.startswith("template sentence") for f in findings)
