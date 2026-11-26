"""The version of the session harness: its loop and everything the loop runs.

A stored session stands in for a new one only when it was run by the same harness. The version
is the loop's protocol number with a hash of the source text of every module on the session
path, which is every module inside steward/ that `run_session` imports, directly or through
another, together with the schedule. Any change to them, a tool description, a result or
error string, a turn cap, a token limit or a text sent to the agent or the simulator, makes
the sessions run before it out of date without anyone having to remember to raise the number.
A test checks that the list covers every module `run_session` reaches.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

SESSION_MODULES = (
    "steward.cases",
    "steward.cases.answer_key",
    "steward.cases.case",
    "steward.cases.casting",
    "steward.cases.changes",
    "steward.cases.clock",
    "steward.cases.conditions",
    "steward.cases.harness",
    "steward.cases.levels",
    "steward.cases.measures",
    "steward.cases.people",
    "steward.cases.postgres",
    "steward.cases.run",
    "steward.cases.sandbox",
    "steward.cases.schedule",
    "steward.cases.sessions",
    "steward.cases.simulator",
    "steward.cases.tripwire",
    "steward.config",
    "steward.environment.audit",
    "steward.environment.fidelity",
    "steward.environment.gym",
    "steward.environment.gym_sessions",
    "steward.environment.recorded",
    "steward.environment.repository",
    "steward.environment.shell",
    "steward.environment.tools",
    "steward.environment.workplace",
    "steward.environment.workspace",
    "steward.execution",
    "steward.execution.anthropic_policy",
    "steward.execution.evaluate",
    "steward.execution.harbor_agent",
    "steward.execution.history",
    "steward.execution.policy",
    "steward.execution.record",
    "steward.execution.session",
    "steward.filter.batch",
    "steward.filter.rank",
    "steward.filter.render",
    "steward.filter.selection",
    "steward.filter.stage_a",
    "steward.filter.tags",
    "steward.intervention.factors",
    "steward.principal.principal",
    "steward.principal.simulator",
    "steward.scenario.author",
    "steward.scenario.compile",
    "steward.scenario.designs",
    "steward.scenario.spec",
    "steward.scenario.subset",
    "steward.schema",
    "steward.scoring.score",
    "steward.store",
)
# The directory that holds the steward package.
SOURCE_ROOT = Path(__file__).resolve().parents[2]


def module_path(module: str, root: Path = SOURCE_ROOT) -> Path:
    """The source file of a module or package under `root`."""
    base = root.joinpath(*module.split("."))
    single = base.with_suffix(".py")
    return single if single.exists() else base / "__init__.py"


def harness_version(protocol: int, root: Path = SOURCE_ROOT) -> str:
    """`protocol` and a short hash of it with the source of every session module."""
    digest = hashlib.sha1(str(protocol).encode())
    for module in SESSION_MODULES:
        digest.update(b"\n\0" + module.encode() + b"\n\0")
        digest.update(module_path(module, root).read_bytes())
    return f"{protocol}-{digest.hexdigest()[:8]}"
