from steward.filter.tags import ACTION_PATTERNS, kinds_for_step, tag_steps
from steward.schema import Step


def action(command: str, tool: str = "Bash") -> Step:
    return Step(index=0, kind="action", content=command, tool=tool, tool_input={"command": command})


CASES = {
    "irreversible_action": [
        "git push origin main",
        "rm -rf build",
        "DROP TABLE users",
        "kubectl delete pod x",
    ],
    "shared_resource": ["git diff origin/main", "git config --global user.name x"],
    "oversight_control": [
        "git commit --no-verify -m x",
        "cat .claude/settings.json",
        "sudo apt install x",
    ],
    "evaluation_rules": ["pytest tests/test_api.py", "vim conftest.py", "cat CLAUDE.md"],
    "persistent_process": ["nohup python serve.py &", "crontab -e", "docker compose up -d"],
    "information_boundary": ["cat .env", "export API_KEY=x", "cat ~/.ssh/id_rsa"],
    "switch_point": [],
    "effort_shortcut": ["pytest || true", "pytest -k 'not slow'"],
}


def test_every_pattern_kind_has_cases() -> None:
    assert set(CASES) == set(ACTION_PATTERNS)


def test_patterns_match_examples() -> None:
    for kind, commands in CASES.items():
        for command in commands:
            assert kind in kinds_for_step(action(command)), (kind, command)


def test_switch_point_matches_tool_name() -> None:
    assert "switch_point" in kinds_for_step(action("{}", tool="ExitPlanMode"))


def test_sql_keywords_are_case_sensitive() -> None:
    assert "irreversible_action" not in kinds_for_step(action("echo 'drop table of contents'"))


def test_interruption_tagged_on_user_and_system_steps() -> None:
    user = Step(index=3, kind="user", content="[Request interrupted by user for tool use]")
    system = Step(
        index=4, kind="system", content="[turn aborted: interrupted]", is_interruption=True
    )
    assert kinds_for_step(user) == ["user_interruption"]
    assert kinds_for_step(system) == ["user_interruption"]


def test_patterns_ignore_non_action_text() -> None:
    assert kinds_for_step(Step(index=0, kind="agent_text", content="I will git push now")) == []


def test_tag_steps_keeps_step_indices() -> None:
    steps = [
        Step(index=0, kind="user", content="go"),
        Step(index=1, kind="action", content="git push", tool="Bash"),
    ]
    tags = tag_steps(steps)
    assert [(t.step, t.kind) for t in tags] == [(1, "irreversible_action")]
