"""The version of the session harness: its loop and the texts it sends.

A stored session stands in for a new one only when it was run by the same harness. The version
is the loop's protocol number, raised by hand when the loop changes, and a hash of every fixed
text the agent or the simulator is sent, so that rewording any of them makes the sessions run
before it out of date without anyone having to remember to raise the number.
"""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Sequence

from steward.cases import changes, levels, run, simulator
from steward.execution import anthropic_policy


def harness_version(protocol: int, session_texts: Sequence[str]) -> str:
    """`protocol` and a short hash of the session loop's own texts with every other fixed
    text sent to the agent or the simulator."""
    texts = [
        *session_texts,
        simulator.INSTRUCTIONS,
        simulator.BRIEFED,
        simulator.NO_REPLY,
        changes.NOTES_OPENING,
        str(changes.NOTES_LIMIT),
        levels.UNINFORMED,
        levels.NEUTRAL,
        anthropic_policy.NO_REPLY,
        anthropic_policy.REPORT_RECEIVED,
        # The system text of a Workplace world is written inside the function that builds it.
        inspect.getsource(run.prepare_world),
    ]
    digest = hashlib.sha1("\n\0".join(texts).encode()).hexdigest()[:8]
    return f"{protocol}-{digest}"
