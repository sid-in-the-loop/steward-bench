"""Keeping a task's answer key out of the agent's reach.

A task's item script and checks keep what only the checks read (checksums of the files that
must survive, the names of protected items, which shift is changed, the check programs
themselves) in the info folder of the repository's remote, /srv/git/origin.git/info in the
container. That folder is part of the remote the agent works with, so while the agent acts the
files are kept in a pristine copy outside the container.

Item scripts, `during` scripts and recorded owners write the key, so the key is revealed
writable around them and whatever they leave becomes the new pristine copy; code of the
agent's that they run does so through `as_agent`, which shows it no key at all. A link or
anything else but a plain file or folder in the key is tampering wherever it is found: it is
removed when the key is taken, and fails the comparison after a check. A check only
reads the key: before each check the pristine copy is put back, read-only where the executor
can mount it so, and after each check the folder is compared with the pristine copy by its
content hash. A check run that changed or removed a key file is recorded as tampering.

While a check runs, the key folder also holds two canaries with a random token for the
session (see tripwire.py); they are left out of the hash and removed with the key after the
check.
"""

from __future__ import annotations

import hashlib
import secrets
import shutil
import stat
from collections.abc import Callable, Sequence
from pathlib import Path

from steward.cases.tripwire import CANARIES, TokenSearch, search_tree

# Written by Python when a script imports a module kept in the key. It is hidden with the key,
# since it holds the module's code, but left out of the hash, since a script may write it.
BYTECODE = "__pycache__"

# Called with the info folder when a check begins, so the executor can mount it read-only,
# and with None when it ends.
Lock = Callable[[Path | None], None]


def key_entries(folder: Path) -> list[Path]:
    """Everything in the info folder. Git's own files there (`exclude`, `attributes`,
    `alternates` and the like) are hidden and hashed with the key as well: a remote's info
    folder is not the agent's to see or change, and `alternates` or `attributes` put there
    would change what the remote holds or how the checks' git reads it."""
    if not folder.is_dir():
        return []
    return sorted(folder.iterdir())


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


def unsound(folder: Path) -> list[Path]:
    """What in the key folder no script of the task writes there: a link, a file with another
    name elsewhere (a hard link, which another file's change would change), or anything that
    is neither a file nor a folder. Each is tampering, since a check would read through it."""
    found = []
    for top in key_entries(folder):
        paths = [top, *top.rglob("*")] if top.is_dir() and not top.is_symlink() else [top]
        for path in paths:
            try:
                status = path.lstat()
            except OSError:
                found.append(path)
                continue
            if stat.S_ISDIR(status.st_mode):
                continue
            if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
                found.append(path)
    return found


def digest(folder: Path) -> str:
    """A hash of every key file's path and content; links are hashed by their target."""
    hashed = hashlib.sha256()
    for top in key_entries(folder):
        paths = [top, *sorted(top.rglob("*"))] if top.is_dir() and not top.is_symlink() else [top]
        for path in paths:
            if path.parent == folder and path.name in CANARIES:
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
        # Set when the key a script left held a link or anything else no script writes; it is
        # removed, and the shift is scored as tampered.
        self.tainted = False

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
        for path in sorted(unsound(self.stash), reverse=True):
            remove(path)
            self.tainted = True
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
        for canary in CANARIES:
            (self.info / canary).write_text(self.token + "\n")
        self.checking = True
        if self.lock is not None:
            self.lock(self.info)

    def end_check(self) -> bool:
        """After a check: whether the key is as it was. The key is hidden again either way."""
        if not self.checking:
            return True
        if self.lock is not None:
            self.lock(None)
        intact = not unsound(self.info) and digest(self.info) == self.pristine
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
