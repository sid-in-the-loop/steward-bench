"""The live filesystem tools of a workplace session, and rebuilding its task directory."""

import json
from pathlib import Path

from steward.environment import workplace
from steward.environment.workplace import MOUNT, TaskFiles, build_task_workspace
from steward.schema import Step


def text(result: str) -> str:
    return str(json.loads(result)["text"])


def action(index: int, tool: str, **arguments: object) -> Step:
    return Step(index=index, kind="action", content="", tool=tool, tool_input=dict(arguments))


def result(index: int, content: str) -> Step:
    return Step(index=index, kind="result", content=content)


def test_filesystem_tools_act_on_the_task_directory(tmp_path) -> None:
    files = TaskFiles(tmp_path)
    wrote = files.call("write_file", {"path": f"{MOUNT}/notes/a.md", "content": "one\ntwo\n"})
    assert text(wrote) == f"Successfully wrote to {MOUNT}/notes/a.md"
    assert text(files.call("read_file", {"path": "notes/a.md"})) == "one\ntwo\n"
    assert text(files.call("list_directory", {"path": MOUNT})) == "[DIR] notes"
    assert text(files.call("search_files", {"path": MOUNT, "pattern": "*.md"})) == (
        f"{MOUNT}/notes/a.md"
    )
    moved = files.call("move_file", {"source": "notes/a.md", "destination": "b.md"})
    assert text(moved) == "Successfully moved notes/a.md to b.md"
    edited = files.call(
        "edit_file", {"path": "b.md", "edits": [{"oldText": "two", "newText": "2"}]}
    )
    assert "-two" in text(edited) and "+2" in text(edited)
    assert (tmp_path / "b.md").read_text() == "one\n2\n"
    tree = json.loads(text(files.call("directory_tree", {"path": MOUNT})))
    assert tree == [
        {"name": "b.md", "type": "file"},
        {"name": "notes", "type": "directory", "children": []},
    ]


def test_filesystem_tools_refuse_paths_outside_and_report_missing_files(tmp_path) -> None:
    files = TaskFiles(tmp_path / "task")
    (tmp_path / "task").mkdir()
    (tmp_path / "secret.txt").write_text("no")
    assert "Access denied" in text(files.call("read_file", {"path": "/etc/passwd"}))
    assert "Access denied" in text(files.call("read_file", {"path": "../secret.txt"}))
    missing = text(files.call("read_file", {"path": "gone.md"}))
    assert missing == f"Error: ENOENT: no such file or directory, open '{MOUNT}/gone.md'"


def test_task_directory_is_rebuilt_from_starting_files_reads_and_writes(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(workplace, "run_in_container", lambda *a, **k: ("", 0, MOUNT))
    starting = tmp_path / "initial_workspace"
    starting.mkdir()
    (starting / "scores.csv").write_text("id,score\n1,90\n")
    steps = [
        action(0, "filesystem-read_file", path=f"{MOUNT}/generated.csv"),
        result(1, workplace.block("id,name\n1,Ann\n")),
        action(2, "filesystem-write_file", path="report.md", content="draft"),
        result(3, workplace.block("Successfully wrote to report.md")),
        action(4, "terminal-run_command", command="ls"),
        result(5, "[]"),
        action(6, "filesystem-write_file", path="late.md", content="after the takeover"),
        result(7, workplace.block("Successfully wrote to late.md")),
    ]
    built = build_task_workspace("toolathlon:r/t", starting, steps, 6, tmp_path / "built", "image")
    names = sorted(p.name for p in built.tree.iterdir())
    assert names == ["generated.csv", "report.md", "scores.csv"]
    assert (built.tree / "generated.csv").read_text() == "id,name\n1,Ann\n"
    assert built.mount == MOUNT and built.commands_replayed == 2
    assert (Path(built.directory) / "workspace.json").exists()
