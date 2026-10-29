"""HAL (Holistic Agent Leaderboard) run logs: one decrypted file per agent run, many tasks each.

A run file holds Weave call records, one per LLM call. A task's trajectory is the agent-model
call with the longest message history plus that call's reply. User-simulator calls (gpt-4o in
tau-bench) and child records of wrapped calls are left out.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from steward.config import Settings
from steward.schema import Domain, Trajectory
from steward.sources.base import Source, SourceInfo, Unavailable
from steward.sources.hal_messages import parse_hal_messages
from steward.sources.json_files import read_json

log = logging.getLogger(__name__)

NAME = "hal_logs"
SECRET_WORDS = ("key", "token", "secret", "password")
# Benchmark name prefix to domain. Web and customer-service agents are tool use; coding
# benchmarks are swe; CORE-Bench and ScienceAgentBench run scientific data-analysis code.
DOMAIN_BY_BENCHMARK: tuple[tuple[str, Domain], ...] = (
    ("taubench", "tool_use"),
    ("assistantbench", "tool_use"),
    ("browser", "tool_use"),
    ("gaia", "tool_use"),
    ("online_mind2web", "tool_use"),
    ("seeact", "tool_use"),
    ("swebench", "swe"),
    ("usaco", "swe"),
    ("scicode", "swe"),
    ("colbench", "swe"),
    ("corebench", "data"),
    ("scienceagentbench", "data"),
)
FALLBACK_DOMAIN: Domain = "tool_use"
TASK_ENV_PREFIXES = {"taubench_": "tau-bench:"}


def domain_for(benchmark: str) -> tuple[Domain, bool]:
    """The benchmark's domain, and False when the benchmark is unknown and a fallback was used."""
    for prefix, domain in DOMAIN_BY_BENCHMARK:
        if benchmark.startswith(prefix):
            return domain, True
    return FALLBACK_DOMAIN, False


def task_env_for(benchmark: str, task_id: str) -> str:
    for prefix, replacement in TASK_ENV_PREFIXES.items():
        if benchmark.startswith(prefix):
            return f"{replacement}{benchmark.removeprefix(prefix)}:{task_id}"
    return f"{benchmark}:{task_id}"


def without_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: without_secrets(item)
            for key, item in value.items()
            if not any(word in str(key).lower() for word in SECRET_WORDS)
        }
    if isinstance(value, list):
        return [without_secrets(item) for item in value]
    return value


def harness_name(agent_name: str) -> str:
    """The scaffold part of `agent_name`, which ends with the model in parentheses."""
    return " ".join(agent_name.split("(", 1)[0].split())


def same_model(configured: str, called: Any) -> bool:
    """Provider prefixes differ between config and calls (together_ai/..., openai/...)."""
    return isinstance(called, str) and called.split("/")[-1] == configured.split("/")[-1]


def task_order(task_id: str) -> tuple[int, int, str]:
    return (0, int(task_id), task_id) if task_id.isdigit() else (1, 0, task_id)


def final_messages(call: dict[str, Any]) -> list[dict[str, Any]]:
    inputs = call.get("inputs") or {}
    messages = [m for m in inputs.get("messages") or [] if isinstance(m, dict)]
    output = call.get("output")
    choices = output.get("choices") if isinstance(output, dict) else None
    if choices and isinstance(choices[0], dict) and isinstance(choices[0].get("message"), dict):
        messages.append(choices[0]["message"])
    return messages


def agent_calls_by_task(run: dict[str, Any], model: str) -> dict[str, list[dict[str, Any]]]:
    calls: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in run.get("raw_logging_results") or []:
        if not isinstance(record, dict) or record.get("parent_id") is not None:
            continue
        task_id = record.get("weave_task_id")
        inputs = record.get("inputs")
        if task_id is None or not isinstance(inputs, dict):
            continue
        if same_model(model, inputs.get("model")):
            calls[str(task_id)].append(record)
    return calls


def task_outcome(run: dict[str, Any], task_id: str) -> dict[str, Any]:
    outcome: dict[str, Any] = {}
    evals = run.get("raw_eval_results")
    entry = evals.get(task_id) if isinstance(evals, dict) else None
    if isinstance(entry, dict):
        for key in ("reward", "score", "resolved", "success"):
            if isinstance(entry.get(key), (bool, int, float)):
                outcome[key] = entry[key]
    results = run.get("results")
    if isinstance(results, dict):
        successful = {str(t) for t in results.get("successful_tasks") or []}
        failed = {str(t) for t in results.get("failed_tasks") or []}
        if task_id in successful | failed:
            outcome["successful"] = task_id in successful
    return outcome


class HalLogs(Source):
    info = SourceInfo(
        name=NAME,
        domain="tool_use",
        real_user=False,
        location="hf:agent-evals/hal_traces",
        license=None,
        notes=(
            "No license declared; research use with attribution to HAL (arXiv 2510.11977). "
            "Subset under a 5 GB cap: tau-bench airline first. Domain is set per benchmark."
        ),
    )

    def download(self, settings: Settings) -> None:
        raise Unavailable(
            "HAL zips hold Fernet-encrypted JSON (password hal1234, PBKDF2 salt per file), and "
            "the project does not depend on cryptography. Run data/logs/hal_scripts/pipeline.py "
            "in a separate venv with cryptography and huggingface_hub to fill "
            "data/raw/hal_logs/decrypted/<run_id>_UPLOAD.json.gz."
        )

    def run_files(self, settings: Settings) -> list[Path]:
        return sorted((self.raw_dir(settings) / "decrypted").glob("*.json.gz"))

    def load(self, settings: Settings) -> Iterator[Trajectory]:
        skipped = 0
        for path in self.run_files(settings):
            try:
                run = read_json(path)
            except (OSError, ValueError) as error:
                log.warning("skipping %s: %s", path, error)
                skipped += 1
                continue
            if not isinstance(run, dict) or not isinstance(run.get("config"), dict):
                skipped += 1
                continue
            yield from self.run_trajectories(run, path.name.removesuffix(".json.gz"))
        if skipped:
            log.warning("%s: skipped %d unreadable run files", NAME, skipped)

    def run_trajectories(self, run: dict[str, Any], file_stem: str) -> Iterator[Trajectory]:
        config = run["config"]
        agent_args = config.get("agent_args") if isinstance(config.get("agent_args"), dict) else {}
        model = str(agent_args.get("model_name") or "")
        benchmark = str(config.get("benchmark_name") or agent_args.get("benchmark_name") or "")
        run_id = str(config.get("run_id") or file_stem.removesuffix("_UPLOAD"))
        agent_name = str(config.get("agent_name") or "")
        domain, known = domain_for(benchmark)
        calls_by_task = agent_calls_by_task(run, model) if model else {}
        code_agent = not any(
            (call.get("inputs") or {}).get("tools")
            for calls in calls_by_task.values()
            for call in calls
        )
        for task_id in sorted(calls_by_task, key=task_order):
            final = max(
                calls_by_task[task_id],
                key=lambda call: (
                    len((call.get("inputs") or {}).get("messages") or []),
                    str(call.get("started_at") or ""),
                ),
            )
            steps = parse_hal_messages(final_messages(final), code_agent)
            if not steps:
                continue
            yield Trajectory(
                trajectory_id=f"{NAME}:{run_id}/{task_id}",
                source=NAME,
                domain=domain,
                real_user=False,
                history_author=model or None,
                harness=harness_name(agent_name) or None,
                task_env=task_env_for(benchmark, task_id),
                license=None,
                steps=steps,
                domain_decided_by_rule=known,
                meta={
                    "run_id": run_id,
                    "task_id": task_id,
                    "benchmark": benchmark,
                    "agent_name": " ".join(agent_name.split()),
                    "date": config.get("date"),
                    "agent_args": without_secrets(agent_args),
                    "agent_calls": len(calls_by_task[task_id]),
                    **task_outcome(run, task_id),
                },
            )
