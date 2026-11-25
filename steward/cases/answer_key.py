"""Keeping a task's answer key out of the agent's reach.

A task's item script and checks keep what only the checks read (checksums of the files that
must survive, the names of protected items, which shift is changed, the check programs
themselves) in the info folder of the repository's remote, /srv/git/origin.git/info in the
container. That folder is part of the remote the agent works with, so while the agent acts the
files are kept in a pristine copy outside the container.

Item scripts, `during` scripts and recorded owners write the key, so the key is revealed
writable around them and whatever they leave becomes the new pristine copy. A check only
reads the key: before each check the pristine copy is put back, read-only where the executor
can mount it so, and after each check the folder is compared with the pristine copy by its
content hash. A check run that changed or removed a key file is recorded as tampering.

While a check runs, the key folder also holds a canary with a random token for the session
(see tripwire.py); it is left out of the hash and removed with the key after the check.
"""

from __future__ import annotations

import hashlib
import secrets
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path

from steward.cases.tripwire import CANARY, TokenSearch, search_tree

# What git itself keeps in a bare repository's info folder; these stay in place.
GIT_FILES = frozenset({"exclude", "attributes", "refs", "grafts", "sparse-checkout", "alternates"})
# Written by Python when a check imports a module kept in the key; not part of the key.
BYTECODE = "__pycache__"

# Called with the info folder when a check begins, so the executor can mount it read-only,
# and with None when it ends.
Lock = Callable[[Path | None], None]


def key_entries(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.name not in GIT_FILES and p.name != BYTECODE)


def remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def copy(source: Path, target: Path) -> None:
    if source.is_dir() and not source.is_symlink():
        shutil.copytree(source, target, symlinks=True)
    else:
        shutil.copy2(source, target, follow_symlinks=False)


def digest(folder: Path) -> str:
    """A hash of every key file's path and content; links are hashed by their target."""
    hashed = hashlib.sha256()
    for top in key_entries(folder):
        paths = [top, *sorted(top.rglob("*"))] if top.is_dir() and not top.is_symlink() else [top]
        for path in paths:
            if path == folder / CANARY:
                continue
            if BYTECODE in path.parts or path.suffix == ".pyc":
                continue
            hashed.update(str(path.relative_to(folder)).encode() + b"\0")
            if path.is_symlink():
                hashed.update(b"link:" + str(path.readlink()).encode())
            elif path.is_file():
                hashed.update(b"file:" + path.read_bytes())
            else:
                hashed.update(b"dir")
            hashed.update(b"\0")
    return hashed.hexdigest()


class AnswerKey:
    """Moves the task's files between the remote's info folder and a pristine copy. Hiding and
    revealing are idempotent, so a caller may reveal before every script and hide after it."""

    def __init__(
        self,
        info: Path,
        stash: Path,
        lock: Lock | None = None,
        writable: Sequence[Path] = (),
    ) -> None:
        self.info = info
        self.stash = stash
        self.lock = lock
        # The directories the agent can write, searched for the canary's token after checks.
        self.writable = tuple(writable)
        self.token = secrets.token_hex(16)
        self.hidden = False
        self.checking = False
        self.pristine = ""
        # Set when something wrote into the info folder while the key was hidden.
        self.planted = False

    def hide(self) -> None:
        """After a script that may write the key: what it left is the key from now on."""
        if self.checking:
            raise RuntimeError("a check is still reading the answer key")
        if self.hidden:
            return
        if self.stash.is_dir():
            shutil.rmtree(self.stash)
        self.stash.mkdir(parents=True)
        for path in key_entries(self.info):
            shutil.move(str(path), str(self.stash / path.name))
        self.pristine = digest(self.stash)
        self.hidden = True

    def reveal(self) -> None:
        """Before a script that may write the key: the pristine key, writable."""
        if not self.hidden:
            return
        self._restore()
        self.hidden = False

    def begin_check(self) -> None:
        """Before a check: the pristine key, read-only where the executor can mount it so."""
        if not self.hidden:
            self.hide()
        self._restore()
        self.info.mkdir(parents=True, exist_ok=True)
        (self.info / CANARY).write_text(self.token + "\n")
        self.checking = True
        if self.lock is not None:
            self.lock(self.info)

    def end_check(self) -> bool:
        """After a check: whether the key is as it was. The key is hidden again either way."""
        if not self.checking:
            return True
        if self.lock is not None:
            self.lock(None)
        intact = digest(self.info) == self.pristine
        for path in key_entries(self.info):
            remove(path)
        self.checking = False
        return intact

    def _restore(self) -> None:
        # Anything the agent put in the folder while the key was hidden is removed first, so it
        # can neither become part of the key nor stand in for a file a script expects.
        entries = key_entries(self.info)
        if entries:
            self.planted = True
        for path in entries:
            remove(path)
        if not self.stash.is_dir():
            return
        self.info.mkdir(parents=True, exist_ok=True)
        for path in key_entries(self.stash):
            copy(path, self.info / path.name)

    def search_copies(self) -> TokenSearch | None:
        """Whether the canary's token is anywhere the agent can write, outside the key folder
        and the pristine copy. None where there is nowhere to search."""
        if not self.writable:
            return None
        return search_tree(self.writable, self.token, skip=(self.info, self.stash))

    def contents(self) -> list[str]:
        """The names the agent could see in the info folder now, for tests and audits."""
        return [p.name for p in key_entries(self.info)]
