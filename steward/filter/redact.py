"""Redact secret-shaped strings from text that leaves the cluster (C2 handoff shards)."""

from __future__ import annotations

import re

SECRET_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        r"sk-(?:or-v1-|ant-|proj-)?[A-Za-z0-9_\-]{20,}",
        r"hf_[A-Za-z0-9]{30,}",
        r"gh[pousr]_[A-Za-z0-9]{30,}",
        r"github_pat_[A-Za-z0-9_]{30,}",
        r"xox[abprs]-[A-Za-z0-9\-]{10,}",
        r"AKIA[0-9A-Z]{16}",
        r"AIza[0-9A-Za-z_\-]{35}",
        r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    )
)
ASSIGNED_SECRET = re.compile(
    r"(?i)((?:api[_-]?key|secret|token|password)[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9._\-/+]{16,}"
)

REPLACEMENT = "[REDACTED]"


def redact(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(REPLACEMENT, text)
    return ASSIGNED_SECRET.sub(lambda m: m.group(1) + REPLACEMENT, text)
