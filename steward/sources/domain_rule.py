"""Deterministic domain rule for mixed real-user sources (brief 6.0)."""

from __future__ import annotations

import re

from steward.filter.tags import action_text
from steward.schema import Domain, Step

DEVOPS = re.compile(
    r"\bdocker(-compose)?\b|Dockerfile|\bkubectl\b|\bhelm\b|\bterraform\b|\.tf\b|"
    r"\.github/workflows|\.gitlab-ci|Jenkinsfile|\bansible\b|k8s/|\bsystemctl\b|"
    r"\bnginx\b|docker-compose\.ya?ml|\bgcloud\b|\baws\s|\baz\s",
    re.IGNORECASE,
)
AI_RND = re.compile(
    r"\bnvidia-smi\b|\bcuda\b|\btorchrun\b|\baccelerate launch\b|\bdeepspeed\b|"
    r"\bwandb\b|\bmlflow\b|\btensorboard\b|\bsbatch\b|train(ing)?[_-]?\w*\.py|"
    r"\bfinetune\b|\bfine-tune\b|\bvllm\b|\.ckpt\b|\bcheckpoints?/",
    re.IGNORECASE,
)

MIN_SHARE = 0.15
MIN_HITS = 3


def assign_domain(steps: list[Step]) -> Domain | None:
    """Return terminal_devops, ai_rnd or swe, or None when the rule cannot decide.

    A domain is chosen when at least MIN_HITS actions and MIN_SHARE of all actions match it.
    Sessions with matches below that bar are left undecided for Stage B.
    """
    actions = [action_text(s) for s in steps if s.kind == "action"]
    if not actions:
        return None
    devops = sum(1 for text in actions if DEVOPS.search(text))
    ai_rnd = sum(1 for text in actions if AI_RND.search(text))

    def strong(hits: int) -> bool:
        return hits >= MIN_HITS and hits / len(actions) >= MIN_SHARE

    if strong(devops) and devops >= ai_rnd:
        return "terminal_devops"
    if strong(ai_rnd):
        return "ai_rnd"
    if devops == 0 and ai_rnd == 0:
        return "swe"
    if devops < MIN_HITS and ai_rnd < MIN_HITS:
        return "swe"
    return None
