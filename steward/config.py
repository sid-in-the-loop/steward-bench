"""Paths, caps and thresholds in one place."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel

from steward.schema import Domain


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


class Paths(BaseModel):
    root: Path
    data: Path

    @property
    def raw(self) -> Path:
        return self.data / "raw"

    @property
    def normalized(self) -> Path:
        return self.data / "normalized"

    @property
    def stage_a(self) -> Path:
        return self.data / "stage_a"

    @property
    def stage_b(self) -> Path:
        return self.data / "stage_b"

    @property
    def stage_c1(self) -> Path:
        return self.data / "stage_c1"

    @property
    def stage_c2(self) -> Path:
        return self.data / "stage_c2"

    @property
    def handoff_c2(self) -> Path:
        return self.data / "handoff" / "c2"

    @property
    def results_c2(self) -> Path:
        return self.data / "results" / "c2"

    @property
    def pilot(self) -> Path:
        return self.data / "pilot"

    @property
    def handoff_c2_pilot(self) -> Path:
        return self.data / "handoff" / "c2_pilot"

    @property
    def results_c2_pilot(self) -> Path:
        return self.data / "results" / "c2_pilot"

    @property
    def ranked(self) -> Path:
        return self.data / "ranked"

    @property
    def batches(self) -> Path:
        return self.data / "batches"

    @property
    def logs(self) -> Path:
        return self.data / "logs"

    @property
    def costs(self) -> Path:
        return self.data / "costs.jsonl"

    @property
    def prompts(self) -> Path:
        return self.root / "prompts"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def figures(self) -> Path:
        return self.reports / "figures"


def load_paths() -> Paths:
    root = Path(os.environ.get("STEWARD_ROOT", _repo_root()))
    data = Path(os.environ.get("STEWARD_DATA", root / "data"))
    return Paths(root=root, data=data)


class StageAThresholds(BaseModel):
    min_agent_actions: int = 40
    min_state_changes: int = 1
    min_user_messages_real_user: int = 2
    min_agent_share: float = 0.30


class StageBConfig(BaseModel):
    top_n: int = 8000
    per_domain_floor: int = 600
    # Every Stage A survivor in these domains is triaged, beyond top_n (DECISIONS.md).
    full_domains: tuple[str, ...] = ("terminal_devops", "workplace")
    summary_token_budget: int = 2500
    max_output_tokens: int = 600
    # Haiku 5.5 reasons by default and reasoning tokens count toward max_tokens.
    reasoning: dict[str, object] = {"enabled": False}


class StageC1Config(BaseModel):
    top_n: int = 1500
    per_domain_floor: int = 150
    # Every Stage B pass in these domains is scored, beyond top_n (DECISIONS.md).
    full_domains: tuple[str, ...] = ("terminal_devops", "workplace")
    rendering_token_cap: int = 40_000
    max_output_tokens: int = 6000
    reasoning: dict[str, object] = {"effort": "low"}


class RenderConfig(BaseModel):
    window_before: int = 25
    window_after: int = 15
    result_head_chars: int = 400
    result_tail_chars: int = 400
    max_windows: int = 3


class StageC2Config(BaseModel):
    # C2 reads a pool of distinct tasks per domain; ranking keeps the best final_per_domain.
    per_domain: int = 180
    domains: tuple[str, ...] = ("swe", "terminal_devops", "workplace")
    one_per_task: bool = True
    shard_max_trajectories: int = 25
    shard_max_bytes: int = 20_000_000
    max_parallel_sessions: int = 6


class ModelConfig(BaseModel):
    """Stage B and C1 run through the OpenRouter Batch API (see docs/pipeline.md)."""

    model: str = "anthropic/claude-haiku-5.5"
    api_base: str = "https://openrouter.ai/api/v1"
    api_key_env: str = "OPENROUTER_API_KEY"
    usd_per_mtok_prompt: float = 0.05
    usd_per_mtok_completion: float = 0.25
    long_prompt_threshold: int = 100_000
    max_prompt_tokens: int = 99_000
    poll_seconds: int = 60
    max_requests_per_batch: int = 2000


class Caps(BaseModel):
    stage_b_c1_usd: float = 15.0


class RankConfig(BaseModel):
    top_k: int = 40
    final_per_domain: int = 120


class PilotConfig(BaseModel):
    size: int = 40


class SourceSampling(BaseModel):
    nebius_sample: int = 3000
    swe_bench_submissions: int = 12
    swe_bench_instances: int = 150
    seed: int = 20261028


class Settings(BaseModel):
    paths: Paths
    stage_a: StageAThresholds = StageAThresholds()
    stage_b: StageBConfig = StageBConfig()
    stage_c1: StageC1Config = StageC1Config()
    render: RenderConfig = RenderConfig()
    stage_c2: StageC2Config = StageC2Config()
    model: ModelConfig = ModelConfig()
    caps: Caps = Caps()
    rank: RankConfig = RankConfig()
    pilot: PilotConfig = PilotConfig()
    sampling: SourceSampling = SourceSampling()


def load_settings() -> Settings:
    return Settings(paths=load_paths())


REAL_USER_DOMAINS_BY_RULE: tuple[Domain, ...] = ("swe", "terminal_devops", "ai_rnd")
