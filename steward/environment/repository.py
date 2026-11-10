"""Rebuild a repository's working tree at a takeover step, and measure how faithful it is.

The tree starts from the session's commit; the recorded file writes and edits are then applied
in order. Every recorded edit names the text it replaced, so an edit that applies cleanly is
direct evidence that the file was in the recorded state. Edits that do not apply show where the
rebuilt tree has drifted: from a wrong commit, from shell commands that changed files, or from
sub-agents whose edits are not in the transcript.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path, PurePosixPath

from pydantic import BaseModel

from steward.schema import Step

log = logging.getLogger(__name__)

EDIT_TOOLS = frozenset({"Edit", "edit", "str_replace_editor", "str_replace_based_edit_tool"})
WRITE_TOOLS = frozenset({"Write", "write", "create"})


class ReplayReport(BaseModel):
    trajectory_id: str
    repository: str
    commit: str
    takeover_step: int
    checked_out: bool
    edits: int = 0
    edits_applied: int = 0
    writes: int = 0
    outside_repository: int = 0
    first_mismatch_step: int | None = None
    error: str | None = None

    @property
    def fidelity(self) -> float | None:
        """Share of recorded edits that applied cleanly; None when there were none."""
        return self.edits_applied / self.edits if self.edits else None


def checkout(repository: str, commit: str, destination: Path, timeout_sec: int = 600) -> None:
    """A blobless clone of a GitHub repository with the commit checked out."""

    def git(*args: str, cwd: Path | None = None) -> None:
        # A private or deleted repository must fail, not ask the terminal for credentials.
        subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            timeout=timeout_sec,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )

    if not (destination / ".git").exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://github.com/{repository}.git"
        git("clone", "--quiet", "--filter=blob:none", "--no-checkout", url, str(destination))
    git("checkout", "--quiet", "--force", commit, cwd=destination)
    git("clean", "-fdq", cwd=destination)


class PathResolver:
    """Maps recorded file paths, often absolute on the user's machine, into the checkout.

    The user's workspace prefix is learned from the first absolute path whose tail exists in
    the checkout; later absolute paths outside that prefix are outside the repository.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.prefix: PurePosixPath | None = None

    def resolve(self, recorded: str) -> Path | None:
        path = PurePosixPath(recorded)
        if not path.is_absolute():
            return self.root / path
        if self.prefix is not None:
            return self.root / path.relative_to(self.prefix) if self._inside(path) else None
        parts = path.parts[1:]
        for start in range(len(parts)):
            tail = PurePosixPath(*parts[start:])
            if (self.root / tail).exists():
                self.prefix = PurePosixPath("/", *parts[:start])
                return self.root / tail
        return None

    def _inside(self, path: PurePosixPath) -> bool:
        assert self.prefix is not None
        return path.is_relative_to(self.prefix)


def apply_step(step: Step, resolver: PathResolver, report: ReplayReport) -> None:
    arguments = step.tool_input or {}
    recorded = arguments.get("file_path") or arguments.get("path")
    if not isinstance(recorded, str):
        return
    target = resolver.resolve(recorded)
    if step.tool in WRITE_TOOLS:
        content = arguments.get("content", arguments.get("file_text"))
        if not isinstance(content, str):
            return
        if target is None:
            # A new file under a workspace prefix that is not known yet cannot be placed.
            report.outside_repository += 1
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        report.writes += 1
        return
    old, new = (
        arguments.get("old_string", arguments.get("old_str")),
        arguments.get("new_string", arguments.get("new_str")),
    )
    if not isinstance(old, str) or not isinstance(new, str):
        return
    if target is None:
        report.outside_repository += 1
        return
    report.edits += 1
    text = target.read_text(errors="surrogateescape") if target.is_file() else None
    if text is None or old not in text:
        if report.first_mismatch_step is None:
            report.first_mismatch_step = step.index
        return
    count = -1 if arguments.get("replace_all") else 1
    target.write_text(text.replace(old, new, count), errors="surrogateescape")
    report.edits_applied += 1


def rebuild(
    trajectory_id: str,
    repository: str,
    commit: str,
    steps: list[Step],
    takeover_step: int,
    destination: Path,
) -> ReplayReport:
    """Check out the commit and apply the recorded writes and edits before the takeover step."""
    report = ReplayReport(
        trajectory_id=trajectory_id,
        repository=repository,
        commit=commit,
        takeover_step=takeover_step,
        checked_out=False,
    )
    try:
        checkout(repository, commit, destination)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        detail = getattr(error, "stderr", b"") or b""
        report.error = f"checkout failed: {detail.decode(errors='replace').strip()[-200:]}"
        return report
    report.checked_out = True
    resolver = PathResolver(destination)
    for step in steps:
        before_takeover = step.index < takeover_step
        if before_takeover and step.kind == "action" and step.tool in EDIT_TOOLS | WRITE_TOOLS:
            apply_step(step, resolver, report)
    return report
