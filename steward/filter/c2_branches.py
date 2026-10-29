"""Git plumbing for the C2 handoff: push shards to handoff/c2, fetch results branches.

Both operations use a separate git worktree, so the main checkout is never switched.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

HANDOFF_BRANCH = "handoff/c2"
MAX_FILE_BYTES = 20_000_000


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()[:500]}")
    return result.stdout


def remote_branch_exists(repo: Path, branch: str) -> bool:
    return bool(git("ls-remote", "--heads", "origin", branch, cwd=repo).strip())


def push_handoff(repo: Path, shard_dirs: list[Path], message: str) -> str:
    """Commit the current shard files on handoff/c2 and push without rewriting history.

    The branch continues from its remote tip when it exists, with main merged in so the cloud
    session gets current code; otherwise it starts from main. Shard files in the given
    directories are replaced by the current ones.
    """
    files = [p for d in shard_dirs for p in sorted(d.glob("shard_*.jsonl"))]
    for path in files:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise RuntimeError(f"{path} is over 20 MB")
    with tempfile.TemporaryDirectory() as tmp:
        worktree = Path(tmp) / "handoff"
        if remote_branch_exists(repo, HANDOFF_BRANCH):
            git("fetch", "-q", "origin", HANDOFF_BRANCH, cwd=repo)
            git("worktree", "add", "--detach", str(worktree), "FETCH_HEAD", cwd=repo)
            git(
                "merge", "-q", "--no-edit", "-m", "Merge main into handoff/c2", "main", cwd=worktree
            )
        else:
            git("worktree", "add", "--detach", str(worktree), "main", cwd=repo)
        try:
            for directory in shard_dirs:
                stale = worktree / directory.relative_to(repo)
                for old in stale.glob("shard_*.jsonl"):
                    git("rm", "-q", "--cached", str(old.relative_to(worktree)), cwd=worktree)
                    old.unlink()
            for path in files:
                target = worktree / path.relative_to(repo)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                git("add", "-f", str(target.relative_to(worktree)), cwd=worktree)
            git("commit", "-q", "-m", message, cwd=worktree)
            git("push", "-q", "origin", f"HEAD:refs/heads/{HANDOFF_BRANCH}", cwd=worktree)
            sha = git("rev-parse", "HEAD", cwd=worktree).strip()
        finally:
            git("worktree", "remove", "--force", str(worktree), cwd=repo)
    log.info("pushed %d shard files to %s at %s", len(files), HANDOFF_BRANCH, sha)
    return sha


def fetch_results(repo: Path, branch_prefix: str, results_dir: Path) -> list[Path]:
    """Copy data/results/... from every origin branch with the prefix into results_dir."""
    git(
        "fetch",
        "-q",
        "origin",
        f"+refs/heads/{branch_prefix}*:refs/remotes/origin/{branch_prefix}*",
        cwd=repo,
    )
    refs = git(
        "for-each-ref",
        "--format=%(refname:short)",
        f"refs/remotes/origin/{branch_prefix}*",
        cwd=repo,
    )
    results_dir.mkdir(parents=True, exist_ok=True)
    written = []
    relative = results_dir.relative_to(repo)
    for ref in refs.split():
        listing = git("ls-tree", "-r", "--name-only", ref, str(relative), cwd=repo)
        for name in listing.split():
            content = git("show", f"{ref}:{name}", cwd=repo)
            target = repo / name
            target.write_text(content)
            written.append(target)
    log.info("fetched %d result files from %s*", len(written), branch_prefix)
    return written
