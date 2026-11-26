"""Every program of the agent's that a repository task's harness scripts run goes through
`as_agent`."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.cases.agent_code import Scanner, runs_agent_code, unwrapped, wrapped

CASES = Path(__file__).parents[2] / "cases"
SCRIPTS = ("item.sh", "levels.sh", "during.sh")
CHECKS = {"harm", "authority", "done", "final", "capture"}


def repository_tasks() -> list[Path]:
    return sorted(
        d
        for d in CASES.iterdir()
        if d.is_dir() and not d.name.startswith(("_", "workplace")) and (d / "case.yaml").exists()
    )


def checks(node: object, key: str = "", inside_reference: bool = False) -> list[str]:
    """The check scripts of a task file, outside its references."""
    if isinstance(node, dict):
        found = []
        for name, value in node.items():
            reference = inside_reference or name.endswith("reference")
            found += checks(value, name, reference)
        return found
    if isinstance(node, list):
        return [c for item in node for c in checks(item, key, inside_reference)]
    if isinstance(node, str) and key in CHECKS and not inside_reference:
        return [node]
    return []


def harness_texts(task: Path) -> list[tuple[str, str]]:
    texts = [(name, (task / name).read_text()) for name in SCRIPTS if (task / name).exists()]
    for name in ("session.yaml", "case.yaml"):
        if (task / name).exists():
            loaded = yaml.safe_load((task / name).read_text())
            texts += [(f"{name} check", text) for text in checks(loaded)]
    return texts


@pytest.mark.parametrize("task", repository_tasks(), ids=lambda d: d.name)
def test_a_tasks_harness_scripts_run_the_agents_code_only_through_as_agent(task: Path) -> None:
    found = [
        f"{name}:{f.line}: {text.splitlines()[f.line - 1].strip()[:100]}"
        for name, text in harness_texts(task)
        for f in unwrapped(text)
    ]
    assert not found, "\n".join(found)


def test_the_scanner_finds_programs_of_the_tree_and_leaves_the_harness_s_own() -> None:
    script = (
        "info=/srv/git/origin.git/info\n"
        'OPS_DATA="$night" tools/jobs run-due > /dev/null\n'
        "x=$(timeout 5 jobs/backup.sh)\n"
        "case $b in main | release/*) ;; esac\n"
        "cat tools/svc; [ -x tools/svc ] && make test\n"
        "python3 - \"$CHANGED\" <<'END'\nimport subprocess\nEND\n"
        "cat > \"$info/lib.sh\" <<'END'\nhelper() { bin/closeout purge; }\nEND\n"
        "cat > jobs/nightly.sh <<'END'\ntools/svc stop\nEND\n"
        '"$info"/rl-check harm; python3 "$info/check.py"; bash -c "$command"\n'
        "as_agent tools/svc list\n"
    )
    assert [f.word for f in unwrapped(script)] == [
        "tools/jobs",
        "jobs/backup.sh",
        "make",
        "bin/closeout",
        "bash",
    ]
    assert unwrapped(wrapped(script)) == []
    assert not runs_agent_code(["python3", "-c", "print(1)"], set())
    assert runs_agent_code(["python3", "-m", "pytest"], set())
    assert Scanner("a 'b|c' | d").scan() == [(0, ["a", "'b|c'"]), (10, ["d"])]
