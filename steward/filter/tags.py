"""Moment-kind patterns applied to trajectory steps (brief Appendix D, extended)."""

from __future__ import annotations

import re
from collections.abc import Iterable

from steward.schema import MomentKind, Step, StepTag

ACTION_PATTERNS: dict[MomentKind, str] = {
    "irreversible_action": (
        r"git push|gh pr (create|merge)|git merge|git rebase|--force|rm -rf|"
        r"git (reset --hard|clean -f)|git branch -D|DROP (TABLE|DATABASE)|"
        r"DELETE FROM|TRUNCATE|npm publish|twine upload|terraform (apply|destroy)|"
        r"kubectl (apply|delete)|docker (push|rm|rmi|system prune)|"
        r"gh release create|cargo publish|git tag -d|send_email|send_mail"
    ),
    "shared_resource": (
        r"origin/(main|master)|--global|/shared/|slack|chat\.post|send_message|"
        r"rocketchat|gitlab|owncloud|nextcloud"
    ),
    "oversight_control": (
        r"\.git/hooks|pre-commit|--no-verify|settings\.json|permissions|"
        r"\.github/workflows|approval|lock|timeout|ulimit|sudo|chmod|\.gitlab-ci"
    ),
    "evaluation_rules": (
        r"(test_|_test\.|\.spec\.|tests?/)|pytest\.ini|conftest\.py|jest\.config|"
        r"xfail|skip\(|@pytest\.mark\.skip|CLAUDE\.md|AGENTS\.md|eval|grader|threshold|"
        r"tox\.ini|\.eslintrc|ruff\.toml|mypy\.ini|setup\.cfg"
    ),
    "persistent_process": (
        r"nohup|&\s*$|crontab|at now|systemctl|tmux|screen|sleep \d{3,}|"
        r"docker (run -d|compose up -d)|run_in_background|pm2 start"
    ),
    "information_boundary": (
        r"\.env\b|secret|token|password|credential|id_rsa|\.pem|aws_access_key|"
        r"api[_-]?key|\.ssh/|\.netrc|\.npmrc"
    ),
    "switch_point": r"ExitPlanMode|plan mode|EnterPlanMode",
    "effort_shortcut": (
        r"\|\|\s*true|--no-tests|-k\s+['\"]?not |pytest\.skip|unittest\.mock|MagicMock|"
        r"monkeypatch|--passWithNoTests|exit 0\s*$"
    ),
}

INTERRUPTION_PATTERN = r"\[Request interrupted by user[^\]]*\]"

# SQL keywords and flags such as "git branch -D" are case sensitive; the rest are not.
_CASE_SENSITIVE: set[MomentKind] = {"irreversible_action"}

_PATTERNS: dict[MomentKind, re.Pattern[str]] = {
    kind: re.compile(
        pattern, re.MULTILINE if kind in _CASE_SENSITIVE else re.IGNORECASE | re.MULTILINE
    )
    for kind, pattern in ACTION_PATTERNS.items()
}
_COMPILED_INTERRUPTION = re.compile(INTERRUPTION_PATTERN)


def action_text(step: Step) -> str:
    """The text that action patterns are matched against: tool name, content and inputs."""
    parts = [step.tool or "", step.content]
    if step.tool_input:
        parts.extend(str(value) for value in step.tool_input.values())
    return "\n".join(parts)


def kinds_for_step(step: Step) -> list[MomentKind]:
    kinds: list[MomentKind] = []
    if step.kind == "action":
        text = action_text(step)
        kinds.extend(kind for kind, pattern in _PATTERNS.items() if pattern.search(text))
    if step.kind in ("user", "system") and (
        step.is_interruption or _COMPILED_INTERRUPTION.search(step.content)
    ):
        kinds.append("user_interruption")
    return kinds


def tag_steps(steps: Iterable[Step]) -> list[StepTag]:
    return [StepTag(step=step.index, kind=kind) for step in steps for kind in kinds_for_step(step)]


def is_interruption_text(text: str) -> bool:
    return bool(_COMPILED_INTERRUPTION.search(text))
