"""Rebuilding a working tree by replaying recorded writes and edits."""

import subprocess
from pathlib import Path

from steward.environment import repository
from steward.environment.repository import PathResolver, rebuild
from steward.schema import Step


def action(index: int, tool: str, **arguments: object) -> Step:
    return Step(index=index, kind="action", content="", tool=tool, tool_input=dict(arguments))


def make_checkout(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("x = 1\ny = 2\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def test_edits_replay_and_mismatches_are_counted(tmp_path, monkeypatch) -> None:
    root = make_checkout(tmp_path)
    monkeypatch.setattr(repository, "checkout", lambda *args, **kwargs: None)
    steps = [
        action(
            0,
            "Edit",
            file_path="/Users/dev/proj/src/app.py",
            old_string="x = 1",
            new_string="x = 10",
        ),
        action(1, "Write", file_path="/Users/dev/proj/src/new.py", content="z = 3\n"),
        action(2, "Edit", file_path="src/app.py", old_string="y = 2", new_string="y = 20"),
        action(
            3, "Edit", file_path="/Users/dev/proj/src/app.py", old_string="missing", new_string="q"
        ),
        action(4, "Edit", file_path="/Users/dev/.config/tool.json", old_string="a", new_string="b"),
        action(5, "Edit", file_path="src/app.py", old_string="x = 10", new_string="never applied"),
    ]
    report = rebuild("s:1", "owner/name", "abc", steps, takeover_step=5, destination=root)
    assert (root / "src" / "app.py").read_text() == "x = 10\ny = 20\n"
    assert (root / "src" / "new.py").read_text() == "z = 3\n"
    assert (report.edits, report.edits_applied, report.writes) == (3, 2, 1)
    assert report.outside_repository == 1 and report.first_mismatch_step == 3
    assert report.fidelity == 2 / 3


def test_workspace_prefix_is_learned_from_an_existing_file(tmp_path) -> None:
    root = make_checkout(tmp_path)
    resolver = PathResolver(root)
    assert resolver.resolve("/home/u/work/repo/README.md") is None
    assert resolver.resolve("/home/u/work/repo/src/app.py") == root / "src" / "app.py"
    assert resolver.resolve("/home/u/work/repo/docs/new.md") == root / "docs" / "new.md"
    assert resolver.resolve("/tmp/scratch.txt") is None


def test_failed_checkout_is_reported(tmp_path, monkeypatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise subprocess.CalledProcessError(128, "git", stderr=b"fatal: reference is not a tree")

    monkeypatch.setattr(repository, "checkout", fail)
    report = rebuild("s:1", "owner/name", "abc", [], 0, tmp_path / "x")
    assert not report.checked_out and "not a tree" in (report.error or "")
