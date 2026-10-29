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
        r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{20,}",
        r"SG\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}",
        r"npm_[A-Za-z0-9]{30,}",
        r"gl[a-z]{2,5}-[A-Za-z0-9_\-]{20,}",
        r"hooks\.slack\.com/services/[A-Za-z0-9/]+",
        r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}",
        r"ya29\.[A-Za-z0-9_\-]{20,}",
        r"GOCSPX-[A-Za-z0-9_\-]{20,}",
        r"pypi-AgEIcHlwaS5vcmc[A-Za-z0-9_\-]{50,}",
        r"shp(?:at|ss|ca|pa)_[a-fA-F0-9]{32}",
        r"\d{8,10}:AA[A-Za-z0-9_\-]{33}",
        r"lin_api_[A-Za-z0-9]{40}",
        r"sbp_[a-f0-9]{40}",
    )
)
# Credentials embedded in connection URLs keep the scheme, user and host.
URL_PASSWORD = re.compile(r"(\b[a-z][a-z0-9+.\-]*://[^:\s/@]+:)[^@\s/]{3,}(@)", re.IGNORECASE)
ASSIGNED_SECRET = re.compile(
    r"(?i)((?:api[_-]?key|secret|token|password)[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9._\-/+]{16,}"
)

REPLACEMENT = "[REDACTED]"


def redact(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(REPLACEMENT, text)
    text = URL_PASSWORD.sub(lambda m: m.group(1) + REPLACEMENT + m.group(2), text)
    return ASSIGNED_SECRET.sub(lambda m: m.group(1) + REPLACEMENT, text)
