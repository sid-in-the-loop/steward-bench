# Phase 1 status, 2026-10-29 03:05

The stop condition in brief section 2.3 is not met. The only gap is Stage C2. Cloud sessions
start, but none of them can push its results back to this repository (BLOCKERS.md). Everything
that does not depend on C2 is done, validated, committed and pushed. The ranked lists use C1
scores, which section 8 allows as the fallback, and can be refreshed in about 30 minutes once C2
runs.

## Done

| Item | State |
|---|---|
| Sources | 13 of 16 sources ingested, 41,366 trajectories; metr_malt, toolathlon and spider2_dbt unavailable, each with its reason and unblock step in SOURCES_STATUS.md |
| Stage A | 10,258 survivors (24.8%) |
| Stage B | top 8,000 triaged, 7,996 valid replies, 7,666 YES |
| Stage C1 | 1,500 scored, 1,494 valid |
| Stage C2 inputs | 291 trajectories in 12 shards (top 60 per domain), redacted and pushed on handoff/c2, with the 2 pilot shards |
| Ranking | data/ranked/<domain>/<axis>.csv for all 6 domains and all 5 axes; 436 distinct trajectories |
| Reports | PHASE1_REPORT.md with 11 figures, SOURCES_STATUS.md, PILOT.md, DECISIONS.md, BLOCKERS.md |
| Validation | `steward validate-all` passes for all 70 outputs |
| Code | 126 tests pass; ruff, ruff format and mypy strict are clean |
| Docs | README.md, docs/pipeline.md, docs/sources.md and docs/stage_c2_cloud.md are complete |

Ranked rows per list: swe, terminal_devops, workplace and ai_rnd have 40 on every axis; data has
16 to 32; tool_use has 1 row on one axis.

## Not done

- Stage C2, so data/stage_c2/results.jsonl, the C1 to C2 agreement in PILOT.md and report
  section 5, and the C2-only ranked columns (jump_in_phase, buttons, checkability,
  right_behavior_agreement) are missing.
- tool_use is nearly empty after Stage A: tau-bench runs rarely reach the A1 minimum of 40 agent
  actions, and Toolathlon is gated. Thresholds were not changed.
- 2,913 of 17,968 SWE-chat sessions were not ingested. They are in harness formats without a
  parser (OpenCode, Cursor, Gemini CLI and smaller ones) or flagged
  compact_transcript_delta_only. The SWE-chat prompt_pushback and prompt_intent labels were not
  loaded either, because they live in the 6.4 GB conversations table.

## Spend

| Item | Spend |
|---|---|
| Stages B and C1, pilots included (data/costs.jsonl) | $4.78 of the $15 cap |
| Abandoned first pilot attempt, within that total | $0.09 |
| C2 | 5 short cloud sessions on claude.ai credits; no C2 work completed |

The OpenRouter key showed $18.15 of earlier usage before this run; that is not part of these
figures.

## Decisions for you

1. Stage C2: open one of the session URLs in BLOCKERS.md to see why it stopped. Most likely the
   Claude GitHub app needs write access to sid-in-the-loop/steward-bench. Once that is fixed,
   run the commands listed there (about 12 sessions, six at a time).
2. tool_use: keep the 40-action A1 minimum, or lower it for that domain (brief threshold, so your
   call), and decide whether to accept the Toolathlon and MALT access forms on Hugging Face.
3. The repository is public, and handoff/c2 holds redacted renderings of gated SWE-chat and
   mle-traj sessions. You approved this; delete the branch if that changes.
4. Rotate HF_TOKEN and OPENROUTER_API_KEY. Both were pasted into the conversation, and the
   HF_TOKEN also appeared in a git clone command line on this shared machine during one
   download.
5. project_desc/ and your uncommitted CLAUDE.md edit are not committed; commit them if you want
   them on the public main branch.
6. Disk: data/ is 58 GB and the home quota is at 79 of 100 GB. data/raw/_git (2 GB of bare
   repository clones for commit inference) can be removed if space is needed.

Every judgment call made during the run is in DECISIONS.md, one line each with the reason.
