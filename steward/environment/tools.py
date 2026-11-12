"""File tools of the recorded harnesses, applied to any place that can read and write files.

Each harness names its file tools and their arguments differently. Re-running a recorded
prefix and answering the tested model both go through `apply_file_tool`, so a recorded edit
and a new one take effect the same way, whether the files are a rebuilt working directory or
a task container.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

PATH_KEYS = ("file_path", "filePath", "path", "target_file", "filename")
READ_TOOLS = frozenset({"Read", "read_file", "read_text_file", "mcp__filesystem__read_text_file"})
WRITE_TOOLS = frozenset({"Write", "write", "create", "create_file", "write_file"})
EDIT_TOOLS = frozenset(
    {
        "Edit",
        "edit",
        "edit_file",
        "replace_file",
        "replace_string_in_file",
        "multi_replace_string_in_file",
        "MultiEdit",
        "str_replace_editor",
        "str_replace_based_edit_tool",
        "file_editor",
    }
)
PATCH_TOOLS = frozenset({"apply_patch"})
FILE_TOOLS = READ_TOOLS | WRITE_TOOLS | EDIT_TOOLS | PATCH_TOOLS
READ_LIMIT_LINES = 2000
OLD_KEYS = ("old_string", "oldString", "old_str", "old_text", "old_content")
NEW_KEYS = ("new_string", "newString", "new_str", "new_text", "new_content")


class Files(Protocol):
    """Where the files are: paths are the ones the session used."""

    async def read(self, path: str) -> str | None: ...

    async def write(self, path: str, content: str) -> None: ...

    async def delete(self, path: str) -> None: ...


class ToolError(Exception):
    """A file tool call that the harness would have refused; the text is what it reports."""


def first(arguments: dict[str, Any], keys: tuple[str, ...]) -> Any:
    return next((arguments[key] for key in keys if key in arguments), None)


def path_of(arguments: dict[str, Any]) -> str | None:
    path = first(arguments, PATH_KEYS)
    return path if isinstance(path, str) and path else None


def changes_files(tool: str | None) -> bool:
    return tool in WRITE_TOOLS | EDIT_TOOLS | PATCH_TOOLS


def numbered(text: str, start: int, count: int) -> str:
    lines = text.splitlines()[start - 1 : start - 1 + count]
    return "\n".join(f"{start + offset:6d}\t{line}" for offset, line in enumerate(lines))


async def read(files: Files, tool: str, arguments: dict[str, Any]) -> str:
    path = path_of(arguments)
    if path is None:
        raise ToolError("No file path was given.")
    text = await files.read(path)
    if text is None:
        raise ToolError(f"File does not exist: {path}")
    if "startLine" in arguments or "endLine" in arguments:
        start = max(int(arguments.get("startLine") or 1), 1)
        end = int(arguments.get("endLine") or start + READ_LIMIT_LINES - 1)
        return "\n".join(text.splitlines()[start - 1 : end])
    if "head" in arguments:
        return "\n".join(text.splitlines()[: int(arguments["head"])])
    start = max(int(arguments.get("offset") or 1), 1)
    count = int(arguments.get("limit") or READ_LIMIT_LINES)
    return numbered(text, start, count) if tool == "Read" else text


def replaced(text: str, old: str, new: str, everywhere: bool, path: str) -> str:
    if old not in text:
        raise ToolError(f"String to replace not found in file: {path}")
    if not everywhere and old and text.count(old) > 1:
        raise ToolError(
            f"Found {text.count(old)} matches of the string to replace, but replace_all is "
            f"false: {path}"
        )
    return text.replace(old, new) if everywhere else text.replace(old, new, 1)


def replacements(arguments: dict[str, Any]) -> list[tuple[str | None, str, str, bool]]:
    """Every (path, old, new, everywhere) a call asks for; the path is None when the call
    names one file for all of them."""
    listed = arguments.get("replacements") or arguments.get("edits")
    everywhere = bool(arguments.get("replace_all") or arguments.get("replaceAll"))
    if isinstance(listed, list):
        found = []
        for item in listed:
            if not isinstance(item, dict):
                continue
            old, new = first(item, OLD_KEYS), first(item, NEW_KEYS)
            if isinstance(old, str) and isinstance(new, str):
                found.append((path_of(item), old, new, bool(item.get("replace_all")) or everywhere))
        return found
    old, new = first(arguments, OLD_KEYS), first(arguments, NEW_KEYS)
    if isinstance(old, str) and isinstance(new, str):
        return [(None, old, new, everywhere)]
    return []


async def edit(files: Files, arguments: dict[str, Any]) -> str:
    command = arguments.get("command")
    if command == "create":
        return await write(files, arguments)
    if command == "view":
        return await read(files, "Read", arguments)
    if command == "insert":
        path, line = path_of(arguments), int(arguments.get("insert_line") or 0)
        text = await files.read(path or "")
        if path is None or text is None:
            raise ToolError(f"File does not exist: {path}")
        lines = text.split("\n")
        addition = str(first(arguments, (*NEW_KEYS, "insert_text")) or "")
        await files.write(path, "\n".join([*lines[:line], addition, *lines[line:]]))
        return f"The file {path} has been edited."
    changes = replacements(arguments)
    if not changes:
        raise ToolError("No replacement was given.")
    texts: dict[str, str] = {}
    for named, old, new, everywhere in changes:
        path = named or path_of(arguments)
        if path is None:
            raise ToolError("No file path was given.")
        if path not in texts:
            current = await files.read(path)
            if current is None:
                raise ToolError(f"File does not exist: {path}")
            texts[path] = current
        texts[path] = replaced(texts[path], old, new, everywhere, path)
    for path, text in texts.items():
        await files.write(path, text)
    return "\n".join(f"The file {path} has been updated." for path in texts)


async def write(files: Files, arguments: dict[str, Any]) -> str:
    path = path_of(arguments)
    content = first(arguments, ("content", "file_text", "contents", "text"))
    if path is None or not isinstance(content, str):
        raise ToolError("A file path and its content are both required.")
    await files.write(path, content)
    return f"File written: {path}"


def locate(lines: list[str], block: list[str], start: int) -> int | None:
    """Where `block` occurs in `lines` at or after `start`: exactly, then ignoring trailing
    space, then ignoring all surrounding space, as the patch tool does."""
    if not block:
        return start
    for normal in (lambda line: line, str.rstrip, str.strip):
        wanted = [normal(line) for line in block]
        for index in range(start, len(lines) - len(block) + 1):
            if [normal(line) for line in lines[index : index + len(block)]] == wanted:
                return index
    return None


def patched(text: str, hunks: list[tuple[str, list[str]]], path: str) -> str:
    """Apply the hunks of one updated file. Each hunk is its `@@` heading and its lines."""
    lines = text.split("\n")
    ends_with_newline = text.endswith("\n")
    if ends_with_newline:
        lines.pop()
    cursor = 0
    for heading, body in hunks:
        if heading:
            at = locate(lines, [heading], cursor)
            cursor = at + 1 if at is not None else cursor
        old = [line[1:] for line in body if line[:1] in (" ", "-")]
        new = [line[1:] for line in body if line[:1] in (" ", "+")]
        at = locate(lines, old, cursor)
        if at is None:
            at = locate(lines, old, 0)
        if at is None:
            shown = "\n".join(old[:6])
            raise ToolError(f"Failed to find expected lines in {path}:\n{shown}")
        lines[at : at + len(old)] = new
        cursor = at + len(new)
    return "\n".join(lines) + ("\n" if ends_with_newline else "")


async def patch(files: Files, arguments: dict[str, Any]) -> str:
    """Codex's patch format: files added, deleted, or updated by context-matched hunks."""
    source = first(arguments, ("input", "patch", "command"))
    if isinstance(source, list):
        source = source[-1] if source else None
    if not isinstance(source, str) or "*** Begin Patch" not in source:
        raise ToolError("Invalid patch: the first line must be '*** Begin Patch'.")
    summary: list[str] = []
    pending: list[tuple[str, str]] = []
    lines = source.split("\n")
    index = lines.index(next(line for line in lines if line.startswith("*** Begin Patch"))) + 1
    while index < len(lines) and not lines[index].startswith("*** End Patch"):
        line = lines[index]
        index += 1
        if line.startswith("*** Add File: "):
            path, body = line.removeprefix("*** Add File: ").strip(), []
            while index < len(lines) and not lines[index].startswith("*** "):
                body.append(lines[index][1:] if lines[index].startswith("+") else lines[index])
                index += 1
            pending.append((path, "\n".join(body) + "\n"))
            summary.append(f"A {path}")
        elif line.startswith("*** Delete File: "):
            path = line.removeprefix("*** Delete File: ").strip()
            await files.delete(path)
            summary.append(f"D {path}")
        elif line.startswith("*** Update File: "):
            path, moved = line.removeprefix("*** Update File: ").strip(), None
            if index < len(lines) and lines[index].startswith("*** Move to: "):
                moved = lines[index].removeprefix("*** Move to: ").strip()
                index += 1
            hunks: list[tuple[str, list[str]]] = []
            while index < len(lines) and not (
                lines[index].startswith("*** ") and not lines[index].startswith("*** End of File")
            ):
                if lines[index].startswith("@@"):
                    hunks.append((lines[index][2:].strip(), []))
                elif not lines[index].startswith("*** End of File"):
                    if not hunks:
                        hunks.append(("", []))
                    hunks[-1][1].append(lines[index] or " ")
                index += 1
            staged = dict(pending)
            current = staged.get(path) or await files.read(path)
            if current is None:
                raise ToolError(f"Failed to read file to update {path}: No such file or directory")
            pending.append((moved or path, patched(current, hunks, path)))
            if moved:
                await files.delete(path)
            summary.append(f"M {moved or path}")
    for path, text in pending:
        await files.write(path, text)
    return "Success. Updated the following files:\n" + "\n".join(summary)


def unpacked(arguments: dict[str, Any]) -> dict[str, Any]:
    """A call recorded as one unparsed string holds its arguments as JSON."""
    raw = arguments.get("input")
    if set(arguments) == {"input"} and isinstance(raw, str) and raw.lstrip().startswith("{"):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return arguments
        return parsed if isinstance(parsed, dict) else arguments
    return arguments


async def apply_file_tool(
    files: Files, tool: str, arguments: dict[str, Any]
) -> tuple[str, bool] | None:
    """Carry out a file tool call. Returns what the harness would report and whether the call
    took effect, or None when the tool is not a file tool."""
    if tool not in FILE_TOOLS:
        return None
    arguments = arguments if tool in PATCH_TOOLS else unpacked(arguments)
    try:
        if tool in READ_TOOLS:
            return await read(files, tool, arguments), True
        if tool in WRITE_TOOLS:
            return await write(files, arguments), True
        if tool in PATCH_TOOLS:
            return await patch(files, arguments), True
        return await edit(files, arguments), True
    except ToolError as error:
        return str(error), False
    except (ValueError, TypeError, OSError) as error:
        return f"Invalid arguments: {error}", False
