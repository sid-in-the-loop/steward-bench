"""TheAgentCompany experiments: agent runs on 175 simulated-company tasks per submission.

Most submissions are OpenHands event streams. Two other formats are parsed here. OWL files are
JSON lists of `{user, assistant, tool_calls: [{func_name, args, result}]}` turns. MUSE files are
plain text: messages headed `NN. | ==== <ROLE> MESSAGE ====`, assistant tool calls as JSON inside
`<tool_call>`, and tool output inside `<tool_response>` (followed by a harness hint in
`<tool_instruction>`) in the next user message.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from steward.config import Settings
from steward.schema import Step, Trajectory
from steward.sources.base import Source, SourceInfo
from steward.sources.openhands_events import parse_openhands_events
from steward.sources.raw_files import json_files_by_stem, read_json, sparse_checkout
from steward.sources.transcripts import StepBuilder, _parse_arguments, action_content

MUSE_HEADER = re.compile(r"^\d+\. \|\s*=+\s*(SYSTEM|USER|ASSISTANT) MESSAGE\s*=*\s*$", re.M)
TOOL_CALL = re.compile(r"<tool_call>(.*?)</tool_call>", re.S)
TOOL_RESPONSE = re.compile(r"<tool_response>(.*?)</tool_response>", re.S)
TOOL_INSTRUCTION = re.compile(r"<tool_instruction>(.*?)</tool_instruction>", re.S)
ACTION_LABEL = re.compile(r"^\s*Action:\s*$", re.M)
# MUSE system prompts embed the sandbox service accounts; their values are not kept.
CREDENTIAL_LINE = re.compile(
    r"^(\s*[\w -]*(?:key|token|secret|password)[\w -]*:).*$", re.IGNORECASE | re.M
)


def redact_credentials(text: str) -> str:
    return CREDENTIAL_LINE.sub(r"\1 [redacted]", text)


def parse_owl_turns(turns: list[Any]) -> list[Step]:
    builder = StepBuilder()
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        builder.user_text(str(turn.get("user") or ""), None)
        for call in turn.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            name = str(call.get("func_name") or "tool")
            tool_input = _parse_arguments(call.get("args"))
            builder.add(
                "action", action_content(name, tool_input), tool=name, tool_input=tool_input
            )
            result = call.get("result")
            text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
            builder.add("result", text)
        builder.add("agent_text", str(turn.get("assistant") or ""))
    return builder.steps


def split_muse_messages(text: str) -> list[tuple[str, str]]:
    """(role, body) pairs in file order; text before the first header is a system preamble."""
    headers = list(MUSE_HEADER.finditer(text))
    messages: list[tuple[str, str]] = []
    preamble = text[: headers[0].start()] if headers else text
    if preamble.strip():
        messages.append(("SYSTEM", preamble))
    for position, header in enumerate(headers):
        end = headers[position + 1].start() if position + 1 < len(headers) else len(text)
        messages.append((header.group(1), text[header.end() : end].strip()))
    return messages


def _muse_call(raw: str) -> tuple[str, dict[str, Any] | None]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return "tool", {"input": raw.strip()}
    if not isinstance(value, dict):
        return "tool", {"input": value}
    return str(value.get("name") or "tool"), _parse_arguments(value.get("arguments"))


def parse_muse_text(text: str) -> list[Step]:
    builder = StepBuilder()
    for role, body in split_muse_messages(text):
        if role == "SYSTEM":
            builder.add("system", redact_credentials(body))
        elif role == "USER":
            responses = TOOL_RESPONSE.findall(body)
            if not responses:
                builder.user_text(body, None)
                continue
            hints = TOOL_INSTRUCTION.findall(body)
            builder.add("result", "\n".join(part.strip() for part in responses + hints))
        else:
            calls = TOOL_CALL.findall(body)
            builder.add("agent_text", ACTION_LABEL.sub("", TOOL_CALL.sub("", body)).strip())
            for raw in calls:
                name, tool_input = _muse_call(raw)
                builder.add(
                    "action", action_content(name, tool_input), tool=name, tool_input=tool_input
                )
    return builder.steps


log = logging.getLogger(__name__)

NAME = "the_agent_company"
REPO_URL = "https://github.com/TheAgentCompany/experiments"
COMMIT = "f9b1e9411fcb554e76509a0e1bc8fe7f8b0e69d6"
SPARSE_PATTERNS = [
    "/README.md",
    "/evaluation/*/*/README.md",
    "/evaluation/*/*/trajectories/",
    "/evaluation/*/*/results/",
]
SUBMISSION_NAME = re.compile(
    r"^\d{8}_(?P<harness>OpenHands-Versa|OpenHands-[\d.]+|OWL-RolePlay|MUSE|TTE-MatrixAgent)"
    r"-(?P<model>.+)$"
)
HARNESS_NAMES = {
    "OpenHands-Versa": "openhands-versa",
    "OWL-RolePlay": "owl-roleplay",
    "MUSE": "muse",
    "TTE-MatrixAgent": "tte-matrixagent",
}
# The 20250729 submissions name the agent and environment (NPC) models together; their README
# says the agent is claude-3-7-sonnet-20250219 and the suffix is the environment model.
AGENT_MODEL_OVERRIDES = {
    "claude37-deepseekv3": ("claude-3-7-sonnet-20250219", "deepseekv3"),
    "claude37-gpt4o": ("claude-3-7-sonnet-20250219", "gpt4o"),
}


class Submission(BaseModel):
    name: str
    harness: str | None
    harness_version: str | None
    history_author: str | None
    environment_model: str | None


def parse_submission(name: str) -> Submission:
    match = SUBMISSION_NAME.match(name)
    if match is None:
        return Submission(
            name=name,
            harness=None,
            harness_version=None,
            history_author=None,
            environment_model=None,
        )
    harness_label, model = match.group("harness"), match.group("model")
    version = None
    if harness_label in HARNESS_NAMES:
        harness = HARNESS_NAMES[harness_label]
    else:
        harness, version = "openhands", harness_label.removeprefix("OpenHands-")
    author, environment = AGENT_MODEL_OVERRIDES.get(model, (model, None))
    return Submission(
        name=name,
        harness=harness,
        harness_version=version,
        history_author=author,
        environment_model=environment,
    )


def trajectory_files(directory: Path) -> dict[str, Path]:
    """Task name to trajectory file: JSON (gzipped or not) first, else the MUSE `.txt`."""
    files = json_files_by_stem(directory, "traj_")
    if directory.is_dir():
        for path in sorted(directory.glob("traj_*.txt")):
            files.setdefault(path.name.removeprefix("traj_").removesuffix(".txt"), path)
    return dict(sorted(files.items()))


def parse_trajectory_file(path: Path) -> tuple[list[Step], Counter[str]]:
    if path.suffix == ".txt":
        return parse_muse_text(path.read_text(errors="replace")), Counter()
    data = read_json(path)
    if not isinstance(data, list):
        raise ValueError("trajectory is not a list")
    if data and isinstance(data[0], dict) and "assistant" in data[0] and "id" not in data[0]:
        return parse_owl_turns(data), Counter()
    parsed = parse_openhands_events(data)
    return parsed.steps, parsed.models


def outcome(results: dict[str, Path], task: str) -> dict[str, Any]:
    path = results.get(task)
    if path is None:
        return {}
    try:
        value = read_json(path)
    except (OSError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in ("final_score", "checkpoints") if key in value}


class TheAgentCompany(Source):
    info = SourceInfo(
        name=NAME,
        domain="workplace",
        real_user=False,
        location=f"github:TheAgentCompany/experiments@{COMMIT}",
        license=None,
        notes=(
            "The experiments repo declares no license; the benchmark repo is MIT. Research use "
            "only. Screenshots and states/ are not downloaded."
        ),
    )

    def download(self, settings: Settings) -> None:
        sparse_checkout(REPO_URL, COMMIT, SPARSE_PATTERNS, self.raw_dir(settings) / "repo")

    def submissions(self, settings: Settings) -> list[Path]:
        root = self.raw_dir(settings) / "repo" / "evaluation"
        return sorted(path for path in root.glob("*/*") if path.is_dir())

    def load(self, settings: Settings) -> Iterator[Trajectory]:
        skipped = 0
        for directory in self.submissions(settings):
            submission = parse_submission(directory.name)
            results = json_files_by_stem(directory / "results", "eval_")
            for task, path in trajectory_files(directory / "trajectories").items():
                try:
                    steps, models = parse_trajectory_file(path)
                except (OSError, ValueError) as error:
                    log.warning("skipping %s: %s", path, error)
                    skipped += 1
                    continue
                if not steps:
                    skipped += 1
                    continue
                yield self.trajectory(submission, task, steps, models, outcome(results, task))
        if skipped:
            log.warning("%s: skipped %d unparsable or empty trajectories", NAME, skipped)

    def trajectory(
        self,
        submission: Submission,
        task: str,
        steps: list[Step],
        models: Counter[str],
        result: dict[str, Any],
    ) -> Trajectory:
        meta: dict[str, Any] = {"submission": submission.name, "task": task, **result}
        if submission.harness_version:
            meta["harness_version"] = submission.harness_version
        if submission.environment_model:
            meta["environment_model"] = submission.environment_model
        if models:
            meta["response_models"] = sorted(models)
        author = submission.history_author
        if author is None and models:
            author = models.most_common(1)[0][0]
        return Trajectory(
            trajectory_id=f"{NAME}:{submission.name}/{task}",
            source=NAME,
            domain="workplace",
            real_user=False,
            history_author=author,
            harness=submission.harness,
            task_env=f"theagentcompany:{task}",
            license=None,
            steps=steps,
            meta=meta,
        )
