"""C2 branch plumbing and the session launcher, against a local bare remote."""

import subprocess

from steward.filter import c2_launch
from steward.filter.c2_branches import fetch_results, git, push_handoff


def make_repo(tmp_path):
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    for key, value in (("user.name", "t"), ("user.email", "t@t")):
        git("config", key, value, cwd=repo)
    (repo / "README.md").write_text("x\n")
    git("add", "README.md", cwd=repo)
    git("commit", "-q", "-m", "init", cwd=repo)
    git("remote", "add", "origin", str(remote), cwd=repo)
    git("push", "-q", "origin", "main", cwd=repo)
    return repo


def test_push_handoff_twice_without_force(tmp_path) -> None:
    repo = make_repo(tmp_path)
    shards = repo / "data" / "handoff" / "c2"
    shards.mkdir(parents=True)
    (shards / "shard_000.jsonl").write_text("{}\n")
    first = push_handoff(repo, [shards], "one")
    (shards / "shard_000.jsonl").unlink()
    (shards / "shard_001.jsonl").write_text("{}\n")
    second = push_handoff(repo, [shards], "two")
    assert first != second
    files = git("ls-tree", "-r", "--name-only", second, cwd=repo).split()
    assert "data/handoff/c2/shard_001.jsonl" in files
    assert "data/handoff/c2/shard_000.jsonl" not in files
    assert git("merge-base", "--is-ancestor", first, second, cwd=repo) == ""


def test_fetch_results(tmp_path) -> None:
    repo = make_repo(tmp_path)
    git("checkout", "-q", "-b", "results/c2-shard-000", cwd=repo)
    results = repo / "data" / "results" / "c2"
    results.mkdir(parents=True)
    (results / "shard_000.jsonl").write_text('{"a": 1}\n')
    git("add", "-f", "data/results/c2/shard_000.jsonl", cwd=repo)
    git("commit", "-q", "-m", "r", cwd=repo)
    git("push", "-q", "origin", "results/c2-shard-000", cwd=repo)
    git("checkout", "-q", "main", cwd=repo)
    assert not (results / "shard_000.jsonl").exists()
    written = fetch_results(repo, "results/c2-shard-", results)
    assert [p.name for p in written] == ["shard_000.jsonl"]
    assert (results / "shard_000.jsonl").read_text() == '{"a": 1}\n'


def test_launch_respects_limit_and_never_relaunches(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    handoff = repo / "data" / "handoff" / "c2"
    handoff.mkdir(parents=True)
    for n in range(4):
        (handoff / f"shard_{n:03d}.jsonl").write_text("{}\n")
    prompts = []
    monkeypatch.setattr(
        c2_launch,
        "start_session",
        lambda r, p, d: prompts.append(p) or "https://claude.ai/code/session_x",
    )
    finished: set[str] = set()
    monkeypatch.setattr(c2_launch, "finished_branches", lambda r, p: finished)
    args = (
        repo,
        handoff,
        repo / "data" / "results" / "c2",
        "results/c2-shard-",
        repo / "launches.jsonl",
        2,
        tmp_path / "logs",
    )
    assert len(c2_launch.launch(*args)) == 2
    assert c2_launch.launch(*args) == []
    finished.add("results/c2-shard-000")
    started = c2_launch.launch(*args)
    assert [s.shard for s in started] == ["data/handoff/c2/shard_002.jsonl"]
    assert "Write results to data/results/c2/shard_000.jsonl" in prompts[0]
    assert "Push results to branch results/c2-shard-000" in prompts[0]
