"""Shared fixtures: settings rooted in a temp dir and small synthetic trajectories."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from steward.config import Paths, Settings
from steward.schema import Step, Trajectory

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    root = tmp_path / "repo"
    (root / "prompts").mkdir(parents=True)
    for prompt in (REPO_ROOT / "prompts").glob("*.md"):
        shutil.copy(prompt, root / "prompts" / prompt.name)
    return Settings(paths=Paths(root=root, data=tmp_path / "data"))


def make_steps(n_actions: int, interrupt_at: int | None = None, users: int = 2) -> list[Step]:
    steps: list[Step] = []

    def add(kind: str, content: str, **extra: object) -> None:
        steps.append(Step(index=len(steps), kind=kind, content=content, **extra))  # type: ignore[arg-type]

    add("user", "Please fix the failing test in parser.py")
    for i in range(n_actions):
        if i == n_actions // 2 and users > 1:
            for _ in range(users - 1):
                add("user", "Also update the docs")
        if interrupt_at is not None and i == interrupt_at:
            add("user", "[Request interrupted by user]", is_interruption=True)
        command = "git push origin main" if i == n_actions - 1 else f"sed -i 's/a/b/' file{i}.py"
        add("action", command, tool="Bash", tool_input={"command": command})
        add("result", "ok " * 10)
    add("agent_text", "Done. All tests pass.")
    return steps


def make_trajectory(
    native_id: str = "t1",
    source: str = "fixture",
    n_actions: int = 50,
    domain: str = "swe",
    real_user: bool = True,
    interrupt_at: int | None = None,
) -> Trajectory:
    return Trajectory(
        trajectory_id=f"{source}:{native_id}",
        source=source,
        domain=domain,  # type: ignore[arg-type]
        real_user=real_user,
        history_author="claude-opus-4-6",
        harness="claude-code",
        repo="owner/name",
        base_commit="a" * 40,
        task_env=None,
        license="mit",
        steps=make_steps(n_actions, interrupt_at),
        meta={"agent_percentage": 80.0},
    )
