# Phase 1 status, 2026-10-29 16:50

Stage C2 is complete. The owner narrowed the final lists to swe, terminal_devops and workplace,
with 120 distinct tasks each (DECISIONS.md, owner-directed extension), and all three lists are
full and C2-scored. The per-axis lists for every domain are refreshed with C2 scores.

## Done

| Item | State |
|---|---|
| Sources | 14 of 16 sources ingested, 48,361 trajectories; Toolathlon added for workplace; metr_malt and spider2_dbt unavailable, each with its reason in SOURCES_STATUS.md |
| Stage A | 11,367 survivors (23.5%) |
| Stage B | 9,399 triaged, 8,847 YES |
| Stage C1 | 3,989 scored, 3,970 valid |
| Stage C2 | 540 trajectories in 22 cloud sessions (180 distinct tasks per domain); 538 results, 0 invalid |
| Final lists | data/ranked/<domain>/top_120.csv for swe, terminal_devops and workplace, 120 rows each, all C2-scored |
| Per-axis lists | data/ranked/<domain>/<axis>.csv for all 6 domains and all 5 axes |
| Reports | PHASE1_REPORT.md with figures and C1 to C2 agreement, SOURCES_STATUS.md, PILOT.md, DECISIONS.md |

## Final lists

| Domain | Sources | Best axis |
|---|---|---|
| swe | swe_chat 112, dataclaw 7, swe_together 1 | side_effects 57, safe_interruptibility 34, reward_gaming 12, absent_supervisor 9, self_modification 8 |
| terminal_devops | swe_chat 79, terminal_bench 40, dataclaw 1 | side_effects 73, safe_interruptibility 25, reward_gaming 18, absent_supervisor 3, self_modification 1 |
| workplace | the_agent_company 48, toolathlon 48, openagentsafety 24 | side_effects 72, reward_gaming 36, absent_supervisor 9, safe_interruptibility 3 |

The sum of the two highest axis scores (out of 6) has a median of 4 in swe and workplace and 3
in terminal_devops.

## Gaps

- 2 of the 540 C2 inputs got no result (two Toolathlon runs in shard 019, skipped by the
  session). The workplace list still filled from the other 178.
- self_modification and absent_supervisor are thin in every domain, and safe_interruptibility is
  thin in workplace, where runs have no real user to interrupt.
- ai_rnd, data and tool_use have per-axis lists but no final list, as the owner directed.

## Spend

| Item | Spend |
|---|---|
| Stages B and C1, pilots included (data/costs.jsonl) | $8.78 of the $15 cap |
| C2 | 22 cloud sessions plus 2 pilot sessions on claude.ai credits |

## Decisions for you

1. Rotate HF_TOKEN and OPENROUTER_API_KEY, which were pasted into the conversation.
2. The probe branches claude/c2-probe3 and results/c2-probe3 on origin can be deleted.
3. project_desc/ and your uncommitted CLAUDE.md edit are not committed.
4. Disk: data/raw/_git (about 2 GB of bare clones for commit inference) can be removed if space
   is needed.

Every judgment call made during the run is in DECISIONS.md, one line each with the reason.
