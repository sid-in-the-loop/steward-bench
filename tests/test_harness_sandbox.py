"""What code of the agent's that a harness script runs can see and change, in the real
container: `as_agent` and the harness's git."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from steward.cases.sandbox import workspace_image
from steward.config import load_settings
from steward.environment import workspace as ws
from steward.environment.repository import ReplayReport

IMAGE = workspace_image(load_settings())
pytestmark = pytest.mark.skipif(
    shutil.which("apptainer") is None or not IMAGE.exists(),
    reason="the workspace image is not installed",
)
SECRET = "key-secret-5b1e"


def make_workspace(directory: Path) -> ws.Workspace:
    report = ReplayReport(
        trajectory_id="case", repository="", commit="", takeover_step=0, checked_out=True
    )
    workspace = ws.Workspace(
        directory=directory,
        mount="/home/sam/ops",
        home="/home/sam",
        replay=report,
        environment={"PGUSER": "sam"},
        harness_environment={"PGUSER": "postgres", "PGPASSFILE": "/opt/steward/pgpass"},
    )
    workspace.tree.mkdir(parents=True)
    workspace.temporary.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", str(workspace.remote)], check=True)
    (workspace.remote / "info" / "b-search-3").write_text(f"{SECRET}\n")
    return workspace


def harness(workspace: ws.Workspace, script: str) -> tuple[str, int]:
    output, status, _ = ws.run_in_container(
        workspace, IMAGE, workspace.mount, script, timeout_sec=60, harness=True
    )
    return output, status


SPY = f"""#!/bin/sh
env
cat {ws.REMOTE_MOUNT}/info/* 2>&1
ls -A {ws.REMOTE_MOUNT}/info /opt/steward 2>&1
for p in /proc/[0-9]*; do tr '\\0' ' ' < $p/cmdline; echo; cat $p/environ 2>/dev/null | tr '\\0' ' '; done
echo planted > {ws.REMOTE_MOUNT}/info/planted 2>&1
cat /proc/self/fd/0
echo spy-ran
"""


def test_code_of_the_agents_run_by_a_check_sees_neither_the_key_nor_the_flags(
    tmp_path: Path,
) -> None:
    workspace = make_workspace(tmp_path)
    spy = workspace.tree / "tools" / "spy"
    spy.parent.mkdir()
    spy.write_text(SPY)
    spy.chmod(0o755)
    script = (
        "export ITEM=3\nCHANGED=1 GOAL=1 CONFLICT=1 VISIBLE=1\n"
        f'[ "$(cat {ws.REMOTE_MOUNT}/info/b-search-3)" = {SECRET} ] && echo harness-reads-key\n'
        f"exec 3< {ws.REMOTE_MOUNT}/info/b-search-3\n"
        f"OPS_DATA=/tmp as_agent tools/spy < {ws.REMOTE_MOUNT}/info/b-search-3\n"
    )
    output, status = harness(workspace, script)
    assert status == 0, output
    assert "harness-reads-key" in output and "spy-ran" in output
    shown = output.replace("harness-reads-key", "")
    assert SECRET not in shown
    for flag in ("CHANGED=", "GOAL=", "CONFLICT=", "VISIBLE=", "PGPASSFILE", "PYTHONSAFEPATH"):
        assert flag not in shown, flag
    assert "PGUSER=sam" in shown and "OPS_DATA=/tmp" in shown and "ITEM=3" in shown
    assert not (workspace.remote / "info" / "planted").exists()
    assert (workspace.remote / "info" / "b-search-3").read_text() == f"{SECRET}\n"


def test_the_agents_own_commands_never_have_the_flags_or_the_harness_folder(
    tmp_path: Path,
) -> None:
    workspace = make_workspace(tmp_path)
    harness(workspace, "CHANGED=1 GOAL=1\nas_agent true\n")
    output, _, _ = ws.run_in_container(
        workspace, IMAGE, workspace.mount, "env; ls -A /opt/steward 2>&1; ps -ef", timeout_sec=60
    )
    assert "CHANGED" not in output and "GOAL" not in output
    assert "No such file or directory" in output


def git_repository(tree: Path, marks: Path) -> None:
    """A tree whose configuration names a command at every place git would run one."""

    def run(*arguments: str) -> None:
        subprocess.run(["git", "-C", str(tree), *arguments], check=True, capture_output=True)

    mark = "/home/sam/marks"
    run("init", "-q", "-b", "main")
    (tree / ".gitattributes").write_text("* filter=planted diff=planted merge=planted\n")
    (tree / "a.txt").write_text("one\n")
    run("add", "-A")
    run("-c", "user.name=a", "-c", "user.email=b", "commit", "-qm", "one")
    (tree / "a.txt").write_text("two\n")
    run("remote", "add", "origin", ws.REMOTE_MOUNT)
    planted = {
        "core.hooksPath": f"{mark}/hooks",
        "core.fsmonitor": f"touch {mark}/fsmonitor; false",
        "core.pager": f"touch {mark}/pager; cat",
        "core.sshCommand": f"touch {mark}/ssh",
        "diff.external": f"{mark}/run diff-external",
        "remote.origin.uploadpack": f"touch {mark}/uploadpack; git-upload-pack",
        "remote.origin.receivepack": f"touch {mark}/receivepack; git-receive-pack",
        "gpg.ssh.program": f"{mark}/run gpg-ssh",
        "gpg.format": "ssh",
        "log.showSignature": "true",
        "credential.helper": f"!touch {mark}/credential",
        "core.repositoryFormatVersion": "1",
        "extensions.worktreeConfig": "true",
        "include.path": "included",
        "includeIf.gitdir:/home/sam/**.path": "included-if",
    }
    for key, value in planted.items():
        run("config", key, value)
    (tree / ".git" / "config.worktree").write_text(
        f"[core]\n\tpager = touch {mark}/worktree-pager; cat\n"
        f'[filter "planted"]\n\tclean = {mark}/run worktree-filter\n\trequired = true\n'
    )
    (tree / ".git" / "included").write_text(
        f'[filter "planted"]\n\tsmudge = {mark}/run include-filter\n'
        f'[diff "planted"]\n\ttextconv = {mark}/run textconv\n\tcommand = {mark}/run diff-driver\n'
        f'[merge "planted"]\n\tdriver = {mark}/run merge-driver\n'
    )
    (tree / ".git" / "included-if").write_text(f"[core]\n\tfsmonitor = {mark}/run include-if\n")
    marks.mkdir()
    runner = marks / "run"
    runner.write_text("#!/bin/sh\ntouch /home/sam/marks/$1\ncat\n")
    runner.chmod(0o755)
    hooks = marks / "hooks"
    hooks.mkdir()
    for hook in ("pre-commit", "post-checkout", "pre-push", "reference-transaction"):
        (hooks / hook).write_text(f"#!/bin/sh\ntouch /home/sam/marks/hook-{hook}\n")
        (hooks / hook).chmod(0o755)


GIT_SCRIPT = (
    "git status --short\ngit diff | grep -c ^+two\ngit diff --cached\ngit log -1 --format=%s\n"
    "git add -A && git commit -qm two\ngit push -q origin HEAD:main\n"
    "git fetch -q origin\ngit log --show-signature -1 > /dev/null\n"
    "git checkout -q -b other\ngit -C /home/sam/ops show HEAD > /dev/null\n"
    "git --git-dir=/srv/git/origin.git log -1 --format=%an main\n"
    "git config --global user.name Sam\n"
)


def planted_workspace(tmp_path: Path) -> tuple[ws.Workspace, Path]:
    workspace = make_workspace(tmp_path)
    workspace.home_directory.mkdir(exist_ok=True)
    (workspace.home_directory / ".gitconfig").write_text(
        "[user]\n\tname = Sam\n\temail = sam@example.org\n"
        "[core]\n\tpager = touch /home/sam/marks/global-pager; cat\n"
    )
    marks = workspace.home_directory / "marks"
    git_repository(workspace.tree, marks)
    return workspace, marks


def test_the_harness_git_runs_nothing_the_agents_configuration_names(tmp_path: Path) -> None:
    workspace, marks = planted_workspace(tmp_path)
    output, status = harness(workspace, GIT_SCRIPT)
    assert status == 0, output
    assert sorted(p.name for p in marks.iterdir()) == ["hooks", "run"], output
    assert "Sam" in output and "\n1\n" in output


def test_the_same_git_commands_run_the_planted_commands_for_the_agent(tmp_path: Path) -> None:
    workspace, marks = planted_workspace(tmp_path)
    output, _, _ = ws.run_in_container(
        workspace, IMAGE, workspace.mount, GIT_SCRIPT, timeout_sec=60
    )
    ran = {p.name for p in marks.iterdir()}
    planted = {"uploadpack", "receivepack", "hook-pre-commit", "include-if", "worktree-filter"}
    planted |= {"include-filter", "diff-driver", "textconv"}
    assert planted <= ran, (" ".join(sorted(ran)), output)


def test_a_harness_command_reads_its_script_from_a_file_and_not_its_arguments(
    tmp_path: Path,
) -> None:
    workspace = make_workspace(tmp_path)
    command = (
        ws.container_command(
            workspace, IMAGE, workspace.mount, "CHANGED=1\ntrue", 60, harness=tmp_path / "run"
        )
        if (tmp_path / "run").mkdir() is None
        else []
    )
    assert not any("CHANGED" in part for part in command)
    assert "CHANGED=1" in (tmp_path / "run" / "script").read_text()


def test_the_agent_reaches_the_github_stand_in_only_through_its_own_process(
    tmp_path: Path,
) -> None:
    import json

    workspace = make_workspace(tmp_path)
    state = workspace.home_directory / ws.GH_STATE
    state.parent.mkdir(parents=True)
    initial = {"repository": "o/r", "next_number": 1, "pulls": {}, "issues": {}, "recorded": {}}
    state.write_text(json.dumps(initial))
    broker = ws.GhBroker(workspace, IMAGE)
    reaching = broker.start()
    try:
        commands = (
            "gh issue create --title First --body 'from the agent' && gh issue list; "
            "echo forged > ~/.config/gh/state.json; echo status=$?"
        )
        output, _, _ = ws.run_in_container(reaching, IMAGE, reaching.mount, commands, 60)
        assert "First" in output and "Read-only file system" in output, output
        recorded = json.loads(state.read_text())
        assert recorded["issues"]["1"]["title"] == "First"
        calls = (state.parent / "calls.jsonl").read_text()
        assert '"issue", "create"' in calls
        # The harness's own scripts still write the state directly.
        harness_output, status = harness(reaching, "echo '{}' > ~/.config/gh/scratch.json")
        assert status == 0 and (state.parent / "scratch.json").exists(), harness_output
    finally:
        broker.stop()
    assert broker.process is None


def test_a_script_without_a_shebang_runs_under_bash_as_the_harness_would_run_it(
    tmp_path: Path,
) -> None:
    workspace = make_workspace(tmp_path)
    tool = workspace.tree / "deploy"
    tool.write_text("set -euo pipefail\necho ran-under-bash\n")
    tool.chmod(0o755)
    output, status = harness(workspace, "as_agent ./deploy")
    assert status == 0 and "ran-under-bash" in output, output
