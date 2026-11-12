"""File tools of the recorded harnesses, applied to an in-memory set of files."""

import asyncio

from steward.environment.tools import apply_file_tool, changes_files


class Memory:
    def __init__(self, **files: str) -> None:
        self.files = dict(files)

    async def read(self, path: str) -> str | None:
        return self.files.get(path)

    async def write(self, path: str, content: str) -> None:
        self.files[path] = content

    async def delete(self, path: str) -> None:
        self.files.pop(path, None)


def call(files: Memory, tool: str, **arguments: object) -> tuple[str, bool]:
    outcome = asyncio.run(apply_file_tool(files, tool, dict(arguments)))
    assert outcome is not None
    return outcome


def test_each_harness_spells_an_edit_its_own_way() -> None:
    files = Memory(a="one two two\n")
    assert call(files, "Edit", file_path="a", old_string="one", new_string="1")[1]
    assert call(files, "replace_string_in_file", filePath="a", oldString="1", newString="uno")[1]
    assert call(
        files, "str_replace_editor", command="str_replace", path="a", old_str="uno", new_str="x"
    )[1]
    assert call(files, "replace_file", file_path="a", old_content="x", new_content="y")[1]
    assert files.files["a"] == "y two two\n"
    text, applied = call(files, "Edit", file_path="a", old_string="two", new_string="2")
    assert not applied and "2 matches" in text
    assert call(files, "Edit", file_path="a", old_string="two", new_string="2", replace_all=True)[1]
    several = [
        {"filePath": "a", "oldString": "y", "newString": "z"},
        {"filePath": "b", "oldString": "", "newString": ""},
    ]
    text, applied = call(files, "multi_replace_string_in_file", replacements=several)
    assert not applied and files.files["a"] == "y 2 2\n"
    assert call(files, "multi_replace_string_in_file", replacements=several[:1])[1]
    assert files.files["a"] == "z 2 2\n"


def test_missing_text_and_missing_files_are_reported_not_raised() -> None:
    files = Memory(a="text\n")
    assert call(files, "Edit", file_path="a", old_string="absent", new_string="x") == (
        "String to replace not found in file: a",
        False,
    )
    assert call(files, "Read", file_path="b") == ("File does not exist: b", False)
    assert asyncio.run(apply_file_tool(files, "Bash", {"command": "ls"})) is None
    assert changes_files("apply_patch") and not changes_files("Read")


def test_reads_follow_the_range_arguments_of_the_harness() -> None:
    files = Memory(a="l1\nl2\nl3\nl4\n")
    assert call(files, "Read", file_path="a", offset=2, limit=2)[0] == "     2\tl2\n     3\tl3"
    assert call(files, "read_file", filePath="a", startLine=2, endLine=3)[0] == "l2\nl3"
    assert call(files, "mcp__filesystem__read_text_file", path="a", head=1)[0] == "l1"
    assert call(files, "Read", input='{"file_path": "a", "limit": 1}')[0] == "     1\tl1"


def test_patch_adds_updates_moves_and_deletes() -> None:
    files = Memory(**{"src/a.py": "import os\n\n\ndef f():\n    return 1\n", "old.txt": "x\n"})
    patch = """*** Begin Patch
*** Add File: notes.md
+first
+second
*** Update File: src/a.py
*** Move to: src/b.py
@@ def f():
-    return 1
+    return 2
*** Delete File: old.txt
*** End Patch
"""
    text, applied = call(files, "apply_patch", input=patch)
    assert applied and text.endswith("A notes.md\nM src/b.py\nD old.txt")
    assert files.files == {
        "notes.md": "first\nsecond\n",
        "src/b.py": "import os\n\n\ndef f():\n    return 2\n",
    }
    missing = "*** Begin Patch\n*** Update File: src/b.py\n@@\n-    return 9\n+    return 3\n*** End Patch"
    text, applied = call(files, "apply_patch", input=missing)
    assert not applied and "Failed to find expected lines" in text
    assert files.files["src/b.py"].endswith("return 2\n")
