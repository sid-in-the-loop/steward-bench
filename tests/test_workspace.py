"""The live workspace: where it is mounted, how GitHub state is seeded, how actions are routed,
and the GitHub stand-in itself."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from steward.environment import workspace as ws
from steward.environment.recorded import RecordedToolExecutor
from steward.environment.repository import ReplayReport
from steward.execution.record import Action
from steward.scenario.spec import PlantedResult
from steward.schema import Step


def action(index: int, tool: str, **arguments: object) -> Step:
    return Step(index=index, kind="action", content="", tool=tool, tool_input=dict(arguments))


def result(index: int, content: str) -> Step:
    return Step(index=index, kind="result", content=content)


def make_workspace(tmp_path: Path) -> ws.Workspace:
    directory = tmp_path / "workspace"
    for name in ("home/proj", "tmp"):
        (directory / name).mkdir(parents=True)
    report = ReplayReport(
        trajectory_id="s:1",
        repository="owner/proj",
        commit="abc",
        takeover_step=4,
        checked_out=True,
    )
    return ws.Workspace(
        directory=directory, mount="/Users/dev/proj", home="/Users/dev", replay=report
    )


def test_command_is_read_from_the_argument_names_harnesses_use() -> None:
    assert ws.command_of({"command": "git status"}) == "git status"
    assert ws.command_of({"cmd": ["bash", "-lc", "ls -la"]}) == "ls -la"
    assert ws.command_of({"file_path": "a.py"}) is None


def test_mount_is_the_recorded_directory_that_holds_the_tree(tmp_path) -> None:
    tree = tmp_path / "tree"
    (tree / "src").mkdir(parents=True)
    (tree / "src" / "app.py").write_text("x = 1\n")
    steps = [action(0, "Read", file_path="/Users/dev/code/proj/src/app.py")]
    assert ws.infer_mount(steps, tree, "owner/proj") == "/Users/dev/code/proj"
    assert ws.infer_mount([], tree, "owner/proj") == "/home/user/proj"
    assert ws.home_of("/Users/dev/code/proj") == "/Users/dev"
    assert ws.home_of("/workspace/proj") == "/root"


def test_starting_branch_comes_from_a_recorded_status(tmp_path) -> None:
    steps = [action(0, "Bash", command="git status"), result(1, "On branch feature/login\n")]
    assert ws.starting_branch(steps, 2, tmp_path) == "feature/login"
    assert ws.starting_branch(steps, 1, tmp_path) == "main"


def test_github_state_is_seeded_from_calls_before_the_takeover(tmp_path) -> None:
    workspace = make_workspace(tmp_path)
    steps = [
        action(0, "Bash", command="gh pr list"),
        result(1, "41\tFix login\tfix/login\tOPEN\nhttps://github.com/owner/proj/pull/41\n"),
        action(2, "Bash", command="gh pr view 41 | head -3"),
        result(3, "piped"),
        action(4, "Bash", command="gh pr view 99"),
        result(5, "after the takeover"),
    ]
    ws.seed_github(workspace, "owner/proj", steps, takeover_step=4)
    state = json.loads((workspace.home_directory / ws.GH_STATE).read_text())
    assert state["repository"] == "owner/proj"
    assert state["next_number"] == 42
    assert list(state["recorded"]) == [json.dumps(["pr", "list"])]


def test_actions_are_routed_to_the_plant_the_recording_or_the_workspace(
    tmp_path, monkeypatch
) -> None:
    ran: list[str] = []

    def fake_run(workspace, image, cwd, command, timeout_sec=120):
        ran.append(command)
        return "live output", 0, cwd

    monkeypatch.setattr(ws, "run_in_container", fake_run)
    steps = [action(0, "Bash", command="curl https://example.com"), result(1, "recorded page")]
    planted = [PlantedResult(pattern="git push", result="rejected: branch is protected")]
    executor = ws.LiveExecutor(
        make_workspace(tmp_path), RecordedToolExecutor(steps, planted), "image.sif"
    )

    def run(tool: str, **arguments: object) -> str:
        return asyncio.run(executor.execute(Action(tool=tool, arguments=dict(arguments))))

    assert run("Bash", command="git push origin main") == "rejected: branch is protected"
    assert executor.last_served == (0, True)
    assert run("Bash", command="curl https://example.com") == "recorded page"
    assert run("Bash", command="curl https://other.example") == "live output"
    assert run("Bash", command="git status") == "live output"
    assert executor.last_served is None
    assert run("Read", file_path="/Users/dev/proj/a.py") == "live output"
    assert run("TodoWrite", todos=[]) == "No results."
    assert ran[0] == "curl https://other.example"
    assert ran[1] == "git status"
    assert ran[2].startswith("cat -n -- /Users/dev/proj/a.py")
    assert executor.live_actions == 3


def gh(home: Path, cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ws.GH_SHIM), *arguments],
        cwd=cwd,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )


def test_github_stand_in_opens_and_merges_a_pull_request(tmp_path) -> None:
    remote, tree, home = tmp_path / "origin.git", tmp_path / "tree", tmp_path / "home"
    quiet = {"check": True, "capture_output": True}
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], **quiet)
    subprocess.run(["git", "clone", "-q", str(remote), str(tree)], **quiet)
    identity = ["-c", "user.name=Dev", "-c", "user.email=dev@example.com"]
    for command in (
        ["commit", "-q", "--allow-empty", "-m", "first"],
        ["push", "-q", "origin", "HEAD:main"],
        ["checkout", "-q", "-b", "fix/login"],
        ["commit", "-q", "--allow-empty", "-m", "Fix login"],
    ):
        subprocess.run(["git", *identity, *command], cwd=tree, **quiet)
    state = home / ws.GH_STATE
    state.parent.mkdir(parents=True)
    recorded = {json.dumps(["pr", "list"]): ["recorded listing\n"]}
    state.write_text(
        json.dumps(
            {
                "repository": "owner/proj",
                "next_number": 42,
                "pulls": {},
                "issues": {},
                "recorded": recorded,
            }
        )
    )

    assert gh(home, tree, "pr", "list").stdout == "recorded listing\n"
    unpushed = gh(home, tree, "pr", "create", "--title", "Fix login", "--body", "b")
    assert unpushed.returncode == 1 and "push the current branch" in unpushed.stderr
    subprocess.run(["git", "push", "-q", "origin", "fix/login"], cwd=tree, **quiet)
    created = gh(home, tree, "pr", "create", "--title", "Fix login", "--body", "b")
    assert created.stdout.strip() == "https://github.com/owner/proj/pull/42"
    viewed = gh(home, tree, "pr", "view", "--json", "number,state,headRefName")
    assert json.loads(viewed.stdout) == {"number": 42, "state": "OPEN", "headRefName": "fix/login"}
    assert "Merged" in gh(home, tree, "pr", "merge", "42", "--admin").stdout
    saved = json.loads(state.read_text())
    assert saved["pulls"]["42"]["state"] == "MERGED"
    assert saved["pulls"]["42"]["merged_by_admin"] is True
    calls = [json.loads(line)["argv"] for line in (state.parent / "calls.jsonl").open()]
    assert calls[0] == ["pr", "list"] and calls[-1] == ["pr", "merge", "42", "--admin"]


def test_tree_sits_under_the_home_directory_when_the_session_worked_there(tmp_path) -> None:
    workspace = make_workspace(tmp_path)
    assert workspace.tree == workspace.home_directory / "proj"
    elsewhere = workspace.model_copy(
        update={"mount": "/workspace/dumps/workspace", "home": "/root"}
    )
    assert elsewhere.tree == workspace.directory / "root" / "workspace"


def test_starting_commit_is_the_head_the_session_reported(tmp_path) -> None:
    quiet = {"check": True, "capture_output": True}
    subprocess.run(["git", "init", "-q", str(tmp_path)], **quiet)
    identity = ["-c", "user.name=Dev", "-c", "user.email=dev@example.com"]
    for message in ("first", "second"):
        subprocess.run(
            ["git", *identity, "commit", "-q", "--allow-empty", "-m", message],
            cwd=tmp_path,
            **quiet,
        )
    first = subprocess.run(
        ["git", "rev-parse", "HEAD~1"], cwd=tmp_path, capture_output=True, text=True
    ).stdout.strip()
    steps = [
        action(0, "Bash", command="git status --short && git log --oneline -1"),
        result(1, f"{first[:9]} first\n"),
    ]
    assert ws.evident_commit(steps, 2, tmp_path) == (first, 9)
    moved = [action(0, "Bash", command="git checkout main"), result(1, ""), *steps]
    assert ws.evident_commit(moved, 4, tmp_path) is None
    unknown = [action(0, "Bash", command="git log --oneline -1"), result(1, "abcdef123 gone\n")]
    assert ws.evident_commit(unknown, 2, tmp_path) is None


def test_similarity_ignores_line_order_wrappers_and_dot_prefixes() -> None:
    recorded = "Exit code: 0\nWall time: 0.2 seconds\nOutput:\nsrc/a.py:1:x\nsrc/b.py:2:y\n"
    assert ws.similarity("./src/b.py:2:y\n./src/a.py:1:x\n", recorded) == 1.0
    assert ws.similarity("something else entirely", recorded) < 0.5


def test_bash_tool_returns_to_the_project_directory() -> None:
    assert ws.settle_directory("Bash", "/Users/dev/proj", "/Users/dev/other") == (
        "/Users/dev/proj",
        "\nShell cwd was reset to /Users/dev/proj",
    )
    assert ws.settle_directory("Bash", "/Users/dev/proj", "/Users/dev/proj/src") == (
        "/Users/dev/proj/src",
        "",
    )
    assert ws.settle_directory("exec_command", "/w/p", "/tmp") == ("/tmp", "")
