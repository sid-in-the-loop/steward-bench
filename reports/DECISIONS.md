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
