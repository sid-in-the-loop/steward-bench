# Decisions

The choices that shape what is in this repository, one line each with the reason. The full
record of the Phase 1 run, including blockers and the pilot, is at the tag `phase1-full`.

## Scope

- Three domains: swe, terminal_devops and workplace. The owner dropped ai_rnd, data and tool_use because too few sessions survived the rules, and asked for the best 120 distinct tasks in each remaining domain.
- Seven sources are kept: swe_chat, dataclaw, swe_together, terminal_bench, the_agent_company, toolathlon and openagentsafety. The other seven contributed nothing to the final lists and were removed after the run.
- A session the domain rule or Stage B places outside the three domains is labelled `other` and goes no further.
- Toolathlon is filed under workplace: its agents act on email, Notion, Canvas, spreadsheets and cloud consoles. Toolathlon-Verified is not used, because its terms forbid redistribution.
- C2 shards and results are on public branches of this repository. The owner waived the brief's private-repository requirement, and shard text is passed through secret redaction first.

## Selection

- Stage A thresholds are the brief's and were not changed.
- A2 counts edit and write tools, editor commands, state-changing shell commands, and MCP tools whose name ends in a write operation (send, create, update and similar).
- Stage B and C1 take every survivor in terminal_devops and workplace, beyond the brief's top 8,000 and top 1,500, so those smaller domains have enough scored tasks. In swe the brief's limits apply, so about 1,000 of 5,822 triage passes were scored.
- A task is the trajectory's task environment, else its repository and commit. C2 reads up to 180 distinct tasks per domain and the final list keeps the best 120, so C2 can drop C1's false positives.
- The final list is ordered by the sum of the two highest axis scores, then agreement on the expected behavior, then agent actions. A trajectory whose jump-in candidates all follow unsafe behavior is left out.

## Data

- When a real-user session names a public repository but no commit, the base commit is inferred as the last default-branch commit before the session started, and marked in meta as inferred.
- Codex `turn_aborted` events and Pi aborted turns count as user interruptions alongside Claude Code's interrupt marker.
- Injected context (environment blocks, AGENTS.md, command messages) becomes system steps, not user steps, so it does not count as user participation.
- Terminal-Bench 2.0 is sampled to 4,000 of 10,426 structured trials, evenly across submissions with a fixed seed.
- Tokens are estimated at 2.2 characters per token, calibrated against Haiku's own counts.

## Models and cost

- Stage B and C1 use Claude Haiku 5.5 on the OpenRouter Batch API, capped at $15 combined; $8.77 was spent. Requests stay under 99k prompt tokens because the price is five times higher above 100k.
- Stage B runs without reasoning; C1 runs with low-effort reasoning, because it is the scoring pass the C2 selection rests on.
- Stage C2 runs Opus in `claude --cloud` sessions, at most six at a time. The origin fetch URL is the plain HTTPS address, because cloud sessions do not recognise SSH host aliases.

## After the run

- Output fields were renamed for clarity (buttons to available_shortcuts, checkability to detection_method, right_behavior_agreement to expected_behavior_agreement, candidates to jump_in_candidates). Nothing was re-scored, and the schema still reads the old names on the results branches.
- The per-axis top-40 lists were replaced by one list per domain that carries every axis score.
