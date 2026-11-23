"""Keeping a task's answer key out of the agent's reach.

A task's item script and checks keep what only the checks read (checksums of the files that
must survive, the names of protected items, which shift is changed) in the info folder of the
repository's remote, /srv/git/origin.git/info in the container. That folder is part of the
remote the agent works with, so while the agent acts the files are moved to a stash outside
the container, and they are put back only while an item script or a check runs.
"""

from __future__ import annotations

import shutil
from pathlib import Path

# What git itself keeps in a bare repository's info folder; these stay in place.
GIT_FILES = frozenset({"exclude", "attributes", "refs", "grafts", "sparse-checkout", "alternates"})


class AnswerKey:
    """Moves the task's files between the remote's info folder and a stash. Hiding and
    revealing are idempotent, so a caller may reveal before every script and hide after it."""

    def __init__(self, info: Path, stash: Path) -> None:
        self.info = info
        self.stash = stash
        self.hidden = False

    def hide(self) -> None:
        if self.hidden or not self.info.is_dir():
            self.hidden = True
            return
        self.stash.mkdir(parents=True, exist_ok=True)
        for path in sorted(self.info.iterdir()):
            if path.name not in GIT_FILES:
                shutil.move(str(path), str(self.stash / path.name))
        self.hidden = True

    def reveal(self) -> None:
        if not self.hidden:
            return
        if self.stash.is_dir():
            self.info.mkdir(parents=True, exist_ok=True)
            for path in sorted(self.stash.iterdir()):
                target = self.info / path.name
                if target.exists():
                    # Something the agent wrote under the same name: the key wins.
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                shutil.move(str(path), str(target))
        self.hidden = False

    def contents(self) -> list[str]:
        """The names the agent could see in the info folder now, for tests and audits."""
        if not self.info.is_dir():
            return []
        return sorted(p.name for p in self.info.iterdir() if p.name not in GIT_FILES)
