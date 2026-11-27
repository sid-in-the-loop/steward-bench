"""Reading the scaled benchmark's tasks for what a validation run cannot see: people who share
a name across tasks, sentences copied from task to task, and disclosure patterns that the
harmful reference's own report already matches."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import yaml

from steward.session import load_session_spec
from steward.task import load_task, task_directories

# A sentence shared by this many tasks or more reads as a template.
TEMPLATE_TASKS = 3
SENTENCE_WORDS = 6


def scaled_directories(root: Path) -> list[Path]:
    """The tasks of the scaled benchmark: those whose session form has a confused deputy."""
    return [
        d
        for d in task_directories(root)
        if (d / "session.yaml").exists()
        and "\nconfused_deputy:" in (d / "session.yaml").read_text()
    ]


def _names(directory: Path) -> list[str]:
    names = [p.name for p in load_task(directory).people]
    manager = yaml.safe_load((directory / "session.yaml").read_text()).get("manager", {})
    if isinstance(manager, dict) and manager.get("name"):
        names.append(str(manager["name"]))
    return names


def _sentences(directory: Path) -> set[str]:
    raw = yaml.safe_load((directory / "session.yaml").read_text())
    lines: list[str] = []
    for key in ("owner_status", "bystander_opinion", "manager_push"):
        lines += [str(x) for x in raw.get(key, [])]
    for key in ("requester_reply", "owner_unchanged"):
        if raw.get(key):
            lines.append(str(raw[key]))
    found: set[str] = set()
    for line in lines:
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            words = re.findall(r"[a-z']+", sentence.lower())
            if len(words) >= SENTENCE_WORDS:
                found.add(" ".join(words))
    return found


def lint(root: Path) -> list[str]:
    """Every finding, one line each."""
    findings: list[str] = []
    directories = scaled_directories(root)
    full: dict[str, set[str]] = defaultdict(set)
    first: dict[str, set[str]] = defaultdict(set)
    last: dict[str, set[str]] = defaultdict(set)
    shared: dict[str, set[str]] = defaultdict(set)
    for directory in directories:
        for name in _names(directory):
            parts = name.split()
            full[name].add(directory.name)
            first[parts[0]].add(directory.name)
            if len(parts) > 1:
                last[parts[-1]].add(directory.name)
        for sentence in _sentences(directory):
            shared[sentence].add(directory.name)
        task = load_task(directory)
        spec = load_session_spec(directory, task)
        if spec is not None and re.search(
            task.measures.disclose, spec.harmful_reference.report, re.IGNORECASE
        ):
            findings.append(f"{directory.name}: the disclose pattern matches the harmful report")
    for label, table in (("full name", full), ("first name", first), ("surname", last)):
        for name, where in sorted(table.items()):
            if len(where) > 1:
                findings.append(
                    f"{label} {name!r} in {len(where)} tasks: {', '.join(sorted(where))}"
                )
    for sentence, where in sorted(shared.items(), key=lambda kv: -len(kv[1])):
        if len(where) >= TEMPLATE_TASKS:
            findings.append(f"template sentence in {len(where)} tasks: {sentence!r}")
    return findings
