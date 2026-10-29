# Decisions

Decisions made during the unattended Phase 1 run, one line each with the reason. Thresholds and
criteria from the brief are unchanged unless an entry says otherwise.

## Setup

- project_desc/traj_collection.md is treated as PROJECT_BRIEF.md: it is the Phase 1 brief that CLAUDE.md refers to, and no file named PROJECT_BRIEF.md exists.
- project_desc/ and the user's uncommitted CLAUDE.md edit are left out of commits: the repository is public, and publishing the unreleased project plan is the owner's call.
- Stage B and C1 call anthropic/claude-haiku-5.5 through the OpenRouter Batch API: the run instructions say every B and C1 call goes through OpenRouter, and its batch tier is half the sync price.
- The combined B and C1 cap is $15, not the $10 in CLAUDE.md and the brief: the run instructions set $15 for this run.
- Spend is measured from the batch usage OpenRouter reports, logged per batch in data/costs.jsonl. The key already showed $18.15 of earlier usage before this run, so key-level usage is not used.
- Pricing in config is the OpenRouter batch list price ($0.05 per million prompt tokens, $0.25 per million completion tokens) and every request is kept under 99k prompt tokens: the price is five times higher above 100k.
- Tokens are estimated as characters / 3.2, a conservative ratio for Claude tokenizers, so cost estimates sit above actual spend.
- Raw data stays in data/raw under the home quota, with downloads capped at 5 GB per source and 25 GB total: 77 GB was free and /projects allocations were not used without the owner's say.

- C2 shards and results go on public branches of this repository: the brief requires a private repository, the owner waived that at 00:33 on 2026-10-29, and shard renderings are passed through secret redaction first. Renderings from gated datasets (SWE-chat, mle-traj) become publicly readable as a result.

## Sources and normalization

- SWE-chat transcripts in Claude Code, Codex, Pi and Copilot CLI formats are parsed; other agent formats (Cursor exports without tool calls, Gemini CLI, Grok CLI, OpenCode and smaller ones) are skipped and counted in SOURCES_STATUS.md: those four formats cover most sessions, and the time went to other sources.
- SWE-chat base_commit is the Codex session's git commit when present, otherwise the first commit linked to the session's canonical checkpoint (its parent is the start state): sessions.parquet has no start commit, and A3 needs one.
- SWE-chat prompt_pushback and prompt_intent come from the 6.4 GB conversations table, which was not downloaded: the persona and success labels are in sessions.parquet, and pushback is reported as unavailable.
- Hyperswitch traces are agent-only (real_user false), not real-user as the brief's table says: each is a single task prompt for a juspay__hyperswitch-<PR> instance followed only by tool results, from GLM, KAT and Qwen runs.
- Hyperswitch task_env is the instance id (juspay__hyperswitch-<PR>), which identifies the starting world for A3.
- Nebius OpenHands is sampled to 3,000 of 67,074 with a fixed seed (20261028), as the brief asks.
- Codex turn_aborted events with reason interrupted, and Pi assistant turns with stopReason aborted, count as user interruptions alongside Claude Code's `[Request interrupted by user]`: they are the same real-user stop in other harnesses.
- Codex injected context (environment_context, AGENTS.md, permissions instructions) and Claude Code meta and command messages become system steps, not user steps: they are not typed by the user, and counting them would inflate A4.

## Stages

- Stage A A2 counts edit and write tools, editor create or replace commands, and shell commands that change state (git write commands, file moves, installs, redirects): the brief gives no list.
- The domain rule assigns terminal_devops or ai_rnd when at least 3 actions and at least 15% of actions match the pattern; a weaker but present signal leaves the domain to Stage B: the brief leaves the rule's thresholds open.
- Stage B summaries shorten text in fixed passes until they fit 2,500 tokens (cap on user-message length, then action-line length, then eliding middle actions): every user message and action stays represented in order.
- Window candidates are Stage B best_steps first, then Stage A tags by kind priority (user_interruption, oversight_control, evaluation_rules, persistent_process, irreversible_action, ...), keeping candidates at least 15 steps apart: this de-duplicates overlapping windows.
- Renderings over 40k tokens are shortened in passes: user messages in the history summary get a cap, then earlier action lines and result snippets shrink, then later windows are dropped.
- Stage C1 input order is offline_rebuild (likely, unclear, unlikely), then Stage A prescore, then agent actions: the brief says "top 1,500 Stage B passes" without naming a score.
- Ranked lists exclude trajectories whose axis score is 0: a zero means no opportunity for that axis, so it is not a candidate.
- The pilot takes 40 Stage A survivors round-robin across domains in prescore order, and runs all 40 through C1, not just the B passes: this measures C1 cost on a full sample.
- Normalized steps are written as gzip-compressed JSONL (steps.jsonl.gz, still one JSON line per step): the home quota was at 61 of 100 GB with SWE-chat still to ingest, and compression saves about 5x.
- When a real-user session names a public GitHub repository but records no commit (Pi, DataClaw, SWE-chat without checkpoint commits), base_commit is inferred as the last commit on the default branch before the session's first timestamp, from a bare treeless clone, and meta.base_commit_method marks it as inferred: A3 asks for a public repo and commit, and this is the closest deterministic reconstruction. It is done only for sessions with at least 40 actions, since the rest fail A1 anyway.
- TheAgentCompany, OpenAgentSafety and HAL trajectories follow the loader decisions in their modules: history_author is the model named by the submission or run config, OpenHands `think` steps are dropped as reasoning, and HAL keeps the agent-model call with the longest history for each task.
- HAL benchmarks are mapped to domains as tau-bench and web benchmarks to tool_use, SWE-bench, USACO, SciCode and ColBench to swe, and CORE-Bench and ScienceAgentBench to data.
- TheAgentCompany history_author is the model named in the submission, for example sonnet-20241022; the API-reported model is kept in meta.response_models.
- Stage B calls run with reasoning disabled and up to 600 output tokens, and Stage C1 calls run with low-effort reasoning and up to 6,000 output tokens: Haiku 5.5 reasons by default and reasoning counts toward max_tokens, so the first pilot attempt's replies all hit the 400-token limit before writing JSON. C1 keeps some reasoning because it is the scoring pass that most rankings rest on.
- The token estimator uses 2.2 characters per token, calibrated on the first pilot attempt (Haiku 5.5 counted 142,433 prompt tokens where 3.2 characters per token predicted 102,628): the brief's 2,500-token summaries and 40,000-token renderings now hold in the model's own count.
- The first pilot attempt is kept in data/batches/pilot_*_attempt1 and data/pilot/attempt1, and its spend stays in data/costs.jsonl and counts toward the cap. The cost guard now sums every batch in costs.jsonl.
- The pilot sample was drawn from the sources ingested by 01:05 (all but SWE-chat, HAL and mle-traj, which were still downloading or ingesting): waiting would have cost about 20 minutes of a 6-hour window, and the cost margin was wide. The pilot covers swe, terminal_devops, workplace and ai_rnd.
- The full Stage B run starts once the pilot's B and C1 numbers show the projection within the cap, while the pilot's C2 cloud session runs in parallel: the run instructions make the go decision a cost check, and C2 agreement does not change B or C1.
- Model replies are normalized before validation: moment kinds named loosely (for example 'report to the user', 'user interruption') are mapped to the canonical identifier by keyword, else 'other', and best_steps keeps its first five: the verbatim Appendix A prompt describes kinds in prose, and 7 of 40 pilot replies used such names.
- Excerpts over 200 characters are trimmed to their first 200, not rejected: a prefix of a verbatim excerpt is still verbatim.
- SWE-bench experiments: 12 submissions from distinct model families, 150 shared Verified instances each; the flattened 20251127_openhands_claude-opus-4-5 export is excluded because it has no tool calls, and Claude is covered by the Sonar submission. A3 passes on task_env (swe-bench-verified:<instance>); base_commit would need the SWE-bench Verified table, which is not downloaded.
- Terminal-Bench 2.0 is sampled to 4,000 of the 10,426 structured Harbor trials, evenly across 24 submissions with a fixed seed; the 22,377 unstructured trials and git-lfs placeholder files are not used.
- mle-traj human Kaggle kernels stay real_user with history_author "human" and fail A4 (no user turns, no agent work), which is the check's intent; the agent runs (MLEvolve, Codex) are loaded as agent-only ai_rnd trajectories.
- Stage A thresholds are not changed even where they empty a domain, for example tool_use, where tau-bench style runs rarely reach 40 actions: the brief says to ask before changing thresholds.
- Stage B was submitted in two parts: first for the sources already through Stage A, then for whatever the top 8,000, recomputed over all sources, adds. Only the final selection's results are written to data/stage_b/results.jsonl; the overlap costs at most about $0.50 and saves roughly 30 minutes.
