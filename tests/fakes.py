"""A fake OpenRouter batch client that answers every request with a canned reply."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from steward.filter.batch import BatchRequest, wire_id
from steward.schema import CRITERIA

Reply = Callable[[BatchRequest], str]


def b_reply(request: BatchRequest) -> str:
    return json.dumps(
        {
            "decision": "YES",
            "reason": "clear task",
            "domain": "swe",
            "best_steps": [20],
            "moment_kinds": ["irreversible_action"],
            "offline_rebuild": "likely",
        }
    )


def c1_reply(request: BatchRequest) -> str:
    scores = {c: {"score": 0, "steps": [], "excerpt": ""} for c in CRITERIA}
    scores["side_effects"] = {"score": 3, "steps": [20], "excerpt": "git push origin main"}
    scores["reward_gaming"] = {"score": 2, "steps": [5], "excerpt": "sed -i"}
    return json.dumps(
        {
            "trajectory_id": "ignored",
            "scores": scores,
            "candidates": [
                {
                    "step": 20,
                    "moment_kind": "irreversible_action",
                    "unsafe_precedent": False,
                    "conflicting_instruction": False,
                }
            ],
            "honest_reporting_moment": True,
            "authorization_boundary": False,
        }
    )


class FakeBatches:
    replies: dict[str, Reply] = {}
    batches: dict[str, dict[str, Any]] = {}

    def __init__(self, model: object) -> None:
        self.model = model

    def create(self, requests: list[BatchRequest]) -> str:
        batch_id = f"batch_{len(FakeBatches.batches)}"
        reply = FakeBatches.replies["default"]
        FakeBatches.batches[batch_id] = {
            "id": batch_id,
            "status": "completed",
            "usage": {
                "prompt_tokens": 1000 * len(requests),
                "completion_tokens": 100 * len(requests),
                "cost": 0.001 * len(requests),
            },
            "results": [
                {
                    "custom_id": wire_id(r.custom_id),
                    "response": {"body": {"choices": [{"message": {"content": reply(r)}}]}},
                    "error": None,
                }
                for r in requests
            ],
        }
        return batch_id

    def get(self, batch_id: str) -> dict[str, Any]:
        return FakeBatches.batches[batch_id]
