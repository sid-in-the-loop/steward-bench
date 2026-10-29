"""OpenRouter Batch API client with a cost guard: estimate, submit, poll and collect.

Every submitted batch is recorded in data/batches/<stage>/manifest.jsonl before polling, so a
rerun resumes polling instead of resubmitting. Actual spend per batch goes to data/costs.jsonl.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

from steward.config import ModelConfig, Settings
from steward.filter.render import estimate_tokens
from steward.schema import CostRecord
from steward.store import append_jsonl, read_jsonl

log = logging.getLogger(__name__)

FINAL_STATUSES = {"completed", "failed", "expired", "cancelled"}
MAX_BATCH_BYTES = 40_000_000


class BatchRequest(BaseModel):
    custom_id: str
    system: str
    user: str
    max_tokens: int
    reasoning: dict[str, object] | None = None


class Estimate(BaseModel):
    requests: int
    prompt_tokens: int
    completion_tokens: int
    usd: float


class ManifestEntry(BaseModel):
    batch_id: str
    stage: str
    custom_ids: list[str]
    submitted_at: datetime
    collected: bool = False


class BatchResult(BaseModel):
    custom_id: str
    text: str | None
    error: str | None


class CostGuardError(RuntimeError):
    pass


def wire_id(custom_id: str) -> str:
    """Batch providers accept only [A-Za-z0-9_-] up to 64 characters, so ids are hashed."""
    return "r" + hashlib.sha256(custom_id.encode()).hexdigest()[:40]


def estimate(requests: list[BatchRequest], model: ModelConfig) -> Estimate:
    prompt = sum(estimate_tokens(r.system) + estimate_tokens(r.user) + 10 for r in requests)
    completion = sum(r.max_tokens for r in requests)
    usd = (prompt * model.usd_per_mtok_prompt + completion * model.usd_per_mtok_completion) / 1e6
    return Estimate(
        requests=len(requests), prompt_tokens=prompt, completion_tokens=completion, usd=usd
    )


def spent(settings: Settings) -> float:
    """Every batch in data/costs.jsonl is a Stage B or C1 batch, pilots and retries included."""
    return sum(r.cost_usd for r in read_jsonl(settings.paths.costs, CostRecord))


def check_budget(settings: Settings, job: Estimate, max_usd: float, confirm: bool) -> None:
    already = spent(settings)
    cap = settings.caps.stage_b_c1_usd
    print(
        f"Estimated: {job.requests} requests, {job.prompt_tokens:,} prompt tokens, "
        f"up to {job.completion_tokens:,} completion tokens, ${job.usd:.4f}. "
        f"Already spent on B and C1: ${already:.4f}. Cap: ${cap:.2f}."
    )
    if job.usd > max_usd:
        raise CostGuardError(f"estimate ${job.usd:.4f} exceeds --max-usd {max_usd:.2f}")
    if already + job.usd > cap:
        raise CostGuardError(f"estimate plus spend ${already + job.usd:.4f} exceeds cap ${cap:.2f}")
    if not confirm:
        raise CostGuardError("not submitted: pass --confirm to submit")


def check_prompt_sizes(requests: list[BatchRequest], model: ModelConfig) -> None:
    for request in requests:
        tokens = estimate_tokens(request.system) + estimate_tokens(request.user)
        if tokens > model.max_prompt_tokens:
            raise CostGuardError(f"{request.custom_id}: ~{tokens} prompt tokens is over the limit")


class OpenRouterBatches:
    def __init__(self, model: ModelConfig) -> None:
        key = os.environ.get(model.api_key_env)
        if not key:
            raise RuntimeError(f"{model.api_key_env} is not set")
        self.model = model
        self.client = httpx.Client(
            base_url=model.api_base,
            headers={"Authorization": f"Bearer {key}"},
            timeout=httpx.Timeout(600.0, connect=30.0),
        )

    def create(self, requests: list[BatchRequest]) -> str:
        body = {
            "endpoint": "/v1/chat/completions",
            "model": self.model.model,
            "requests": [
                {
                    "custom_id": wire_id(r.custom_id),
                    "body": {
                        "messages": [
                            {"role": "system", "content": r.system},
                            {"role": "user", "content": r.user},
                        ],
                        "max_tokens": r.max_tokens,
                        "temperature": 0,
                        **({"reasoning": r.reasoning} if r.reasoning is not None else {}),
                    },
                }
                for r in requests
            ],
        }
        response = self.client.post("/batches", content=json.dumps(body))
        if response.status_code != 202:
            raise RuntimeError(f"batch create failed {response.status_code}: {response.text[:500]}")
        return str(response.json()["id"])

    def get(self, batch_id: str) -> dict[str, Any]:
        for attempt in range(8):
            try:
                response = self.client.get(f"/batches/{batch_id}")
            except httpx.HTTPError as error:
                log.warning("poll %s failed: %s", batch_id, error)
                time.sleep(30 * (attempt + 1))
                continue
            # A batch can read as 404 for a short while after it is created.
            if response.status_code in (404, 429) or response.status_code >= 500:
                time.sleep(30 * (attempt + 1))
                continue
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            return data
        raise RuntimeError(f"could not read batch {batch_id}")


def chunk(requests: list[BatchRequest], max_requests: int) -> list[list[BatchRequest]]:
    chunks: list[list[BatchRequest]] = [[]]
    size = 0
    for request in requests:
        request_bytes = len(request.system) + len(request.user) + 200
        if chunks[-1] and (
            len(chunks[-1]) >= max_requests or size + request_bytes > MAX_BATCH_BYTES
        ):
            chunks.append([])
            size = 0
        chunks[-1].append(request)
        size += request_bytes
    return [c for c in chunks if c]


def manifest_path(settings: Settings, stage: str) -> Path:
    return settings.paths.batches / stage / "manifest.jsonl"


def results_path(settings: Settings, stage: str, batch_id: str) -> Path:
    return settings.paths.batches / stage / f"{batch_id}.results.jsonl"


def read_manifest(settings: Settings, stage: str) -> list[ManifestEntry]:
    entries: dict[str, ManifestEntry] = {}
    for entry in read_jsonl(manifest_path(settings, stage), ManifestEntry):
        entries[entry.batch_id] = entry
    return list(entries.values())


def submitted_ids(settings: Settings, stage: str) -> set[str]:
    return {cid for entry in read_manifest(settings, stage) for cid in entry.custom_ids}


def submit(settings: Settings, stage: str, requests: list[BatchRequest]) -> list[str]:
    client = OpenRouterBatches(settings.model)
    batch_ids = []
    for part in chunk(requests, settings.model.max_requests_per_batch):
        batch_id = client.create(part)
        entry = ManifestEntry(
            batch_id=batch_id,
            stage=stage,
            custom_ids=[r.custom_id for r in part],
            submitted_at=datetime.now(UTC),
        )
        append_jsonl(manifest_path(settings, stage), [entry])
        log.info("%s: submitted batch %s with %d requests", stage, batch_id, len(part))
        batch_ids.append(batch_id)
    return batch_ids


def _result_text(result: dict[str, Any]) -> tuple[str | None, str | None]:
    if result.get("error"):
        return None, json.dumps(result["error"])[:500]
    response = result.get("response") or {}
    try:
        return str(response["body"]["choices"][0]["message"]["content"]), None
    except (KeyError, IndexError, TypeError):
        return None, "malformed response"


def collect(settings: Settings, stage: str, wait: bool = True) -> list[BatchResult]:
    """Poll every uncollected batch of a stage; return all results collected so far."""
    client = OpenRouterBatches(settings.model)
    for entry in read_manifest(settings, stage):
        if entry.collected:
            continue
        while True:
            batch = client.get(entry.batch_id)
            status = batch.get("status")
            counts = batch.get("request_counts")
            log.info("%s: batch %s status %s %s", stage, entry.batch_id, status, counts)
            ready = status in FINAL_STATUSES and (status != "completed" or batch.get("results"))
            if ready or not wait:
                break
            time.sleep(settings.model.poll_seconds)
        if not ready:
            continue
        by_wire = {wire_id(cid): cid for cid in entry.custom_ids}
        results = [
            BatchResult(custom_id=by_wire[str(r["custom_id"])], text=text, error=error)
            for r in batch.get("results") or []
            if str(r["custom_id"]) in by_wire
            for text, error in [_result_text(r)]
        ]
        if status != "completed":
            message = (batch.get("error") or {}).get("message", status)
            results = [
                BatchResult(custom_id=cid, text=None, error=f"batch {status}: {message}")
                for cid in entry.custom_ids
            ]
        out = results_path(settings, stage, entry.batch_id)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w") as handle:
            for result in results:
                handle.write(result.model_dump_json() + "\n")
        usage = batch.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        # Fall back to list price when the batch reports tokens but no cost.
        cost = usage.get("cost")
        if cost is None:
            cost = (
                prompt_tokens * settings.model.usd_per_mtok_prompt
                + completion_tokens * settings.model.usd_per_mtok_completion
            ) / 1e6
        append_jsonl(
            settings.paths.costs,
            [
                CostRecord(
                    batch_id=entry.batch_id,
                    stage=stage,
                    model=settings.model.model,
                    requests=len(entry.custom_ids),
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cost_usd=float(cost),
                    submitted_at=entry.submitted_at,
                )
            ],
        )
        append_jsonl(manifest_path(settings, stage), [entry.model_copy(update={"collected": True})])
    collected: list[BatchResult] = []
    for entry in read_manifest(settings, stage):
        collected.extend(read_jsonl(results_path(settings, stage, entry.batch_id), BatchResult))
    return collected


def parse_json_object(text: str) -> dict[str, Any]:
    """Extract the first JSON object from a model reply, tolerating code fences."""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in reply")
    # Replies sometimes carry raw newlines or tabs inside excerpt strings.
    value = json.loads(text[start : end + 1], strict=False)
    if not isinstance(value, dict):
        raise ValueError("reply is not a JSON object")
    return value
