"""How closely re-running a session's recorded prefix reproduces what the session saw.

Each re-run command's output is compared with the result the recording holds for it. A
harness that sends several commands and gets one result back is compared as one block.
"""

from __future__ import annotations

import difflib
import re
from collections import Counter

from pydantic import BaseModel

MATCH_THRESHOLD = 0.9
COMPARE_LIMIT = 2000
SHOWN_CHARS = 400

# What harnesses put around a command's output: exit status, timing, a chunk identifier.
WRAPPER = re.compile(
    r"\A(?:Command: [^\n]*\n)?(?:Chunk ID: \w+\n)?(?:Exit code:? \d+\n)?"
    r"(?:Wall time: [\d.]+ seconds\n)?(?:Process exited with code \d+\n)?"
    r"(?:Original token count: \d+\n)?(?:Total output lines: \d+\n)?(?:Output:\n)?"
    r"(?:New Terminal Output:\n)?"
)
# Values that differ between any two runs of the same command and say nothing about state.
VOLATILE = re.compile(
    r"<command_execution_time>[^<]*</command_execution_time>"
    r"|\x1b\[[0-9;?]*[a-zA-Z]"
    # A terminal's note that output follows, and a prompt on a line of its own.
    r"|^New Terminal Output:[ \t]*$"
    r"|^\w+@[\w.-]+:\S* ?[#$][ \t]*$"
    r"|root@[0-9a-f-]{8,}"
    r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) [ \d]\d (?:[ \d]\d:\d\d| \d{4})\b"
    r"|\r",
    re.MULTILINE,
)


class Comparison(BaseModel):
    step: int
    command: str
    status: int | None
    score: float
    replayed: str
    recorded: str


class ReplayFidelity(BaseModel):
    """Counts over the re-run prefix, and each comparison behind them."""

    replayed: int = 0
    failed: int = 0
    compared: int = 0
    matched: int = 0
    similarity_total: float = 0.0
    # Share of the lines the recording showed that the re-run also showed, over the whole
    # prefix. A terminal prints a command's output whenever it arrives, so the two runs can
    # show the same lines under different commands; this ignores where a line appeared.
    transcript: float | None = None
    comparisons: list[Comparison] = []

    @property
    def rate(self) -> float | None:
        """Share of compared commands whose output matched; None when nothing was compared."""
        return self.matched / self.compared if self.compared else None


def output_lines(text: str) -> Counter[str]:
    return Counter(
        " ".join(line.split()).removeprefix("./") for line in text.splitlines() if line.strip()
    )


def transcript_lines(text: str) -> Counter[str]:
    """The distinct lines of an output, without what differs between any two runs."""
    return Counter(set(output_lines(VOLATILE.sub("", WRAPPER.sub("", text, count=1)))))


def similarity(replayed: str, recorded: str) -> float:
    """How alike two command outputs are, from 0 to 1. Whitespace layout, what the harness
    put around the output, and the order of lines are ignored: a search that lists the same
    files in another order shows the same state."""
    replayed = VOLATILE.sub("", WRAPPER.sub("", replayed, count=1))
    body = VOLATILE.sub("", WRAPPER.sub("", recorded, count=1))
    left = " ".join(replayed.split())[:COMPARE_LIMIT]
    right = " ".join(body.split())[:COMPARE_LIMIT]
    if left == right:
        return 1.0
    ordered = difflib.SequenceMatcher(None, left, right, autojunk=False).ratio()
    mine, theirs = output_lines(replayed), output_lines(body)
    union = sum((mine | theirs).values())
    shared = sum((mine & theirs).values()) / union if union else 1.0
    return max(ordered, shared)


class ReplayLog:
    """Pairs re-run actions with recorded results as the prefix is walked in order."""

    def __init__(self) -> None:
        self.fidelity = ReplayFidelity()
        self.actions: list[tuple[int, str, int | None, str] | None] = []
        self.results: list[str] = []
        self.shown: Counter[str] = Counter()
        self.recorded: Counter[str] = Counter()

    def ran(self, step: int, command: str, status: int | None, output: str) -> None:
        self._close_group()
        self.actions.append((step, command, status, output))
        self.fidelity.replayed += 1
        self.fidelity.failed += status not in (None, 0)

    def skipped(self) -> None:
        """An action of the recording that was not re-run; its result is not compared."""
        self._close_group()
        self.actions.append(None)

    def result(self, content: str) -> None:
        self.results.append(content)

    def _close_group(self) -> None:
        if self.results:
            self.flush()

    def _compare(self, step: int, command: str, status: int | None, out: str, rec: str) -> None:
        score = similarity(out, rec)
        self.fidelity.compared += 1
        self.fidelity.matched += score >= MATCH_THRESHOLD
        self.fidelity.similarity_total += score
        self.fidelity.comparisons.append(
            Comparison(
                step=step,
                command=command[:300],
                status=status,
                score=round(score, 3),
                replayed=out[-SHOWN_CHARS:],
                recorded=rec[-SHOWN_CHARS:],
            )
        )

    def flush(self) -> ReplayFidelity:
        """Compare the actions gathered so far with the results that followed them."""
        actions, results = self.actions, self.results
        self.actions, self.results = [], []
        ran = [a for a in actions if a is not None]
        if not ran or not results:
            return self.fidelity
        for _, _, _, output in ran:
            self.shown |= transcript_lines(output)
        for recorded in results:
            self.recorded |= transcript_lines(recorded)
        if self.recorded:
            found = sum((self.shown & self.recorded).values())
            self.fidelity.transcript = round(found / sum(self.recorded.values()), 3)
        if len(actions) == len(results):
            for action, recorded in zip(actions, results, strict=True):
                if action is not None:
                    self._compare(*action, recorded)
        elif len(ran) == len(actions):
            step, command, status, _ = ran[0]
            joined = "\n".join(a[3] for a in ran)
            self._compare(step, command, status, joined, "\n".join(results))
        return self.fidelity
