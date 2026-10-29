# Pilot

40 Stage A survivors, picked round-robin across domains (ai_rnd: 3, swe: 13, terminal_devops: 12, workplace: 12), went through Stage B and Stage C1 on the OpenRouter Batch API with anthropic/claude-haiku-5.5. 40 of them have Stage C2 results from cloud sessions. Token counts and costs are the usage the batches reported. The first pilot attempt (pilot_b's first batch and pilot_c1_attempt1) ran with default reasoning and too small an output budget; it is kept in the spend table and excluded from the per-trajectory numbers.

| stage | requests | valid outputs | prompt tokens | completion tokens | prompt tokens per trajectory | cost (USD) | cost per trajectory (USD) |
|---|---|---|---|---|---|---|---|
| B | 40 | 40 | 114,786 | 6,862 | 2,870 | 0.0075 | 0.00019 |
| C1 | 40 | 40 | 994,482 | 64,414 | 24,862 | 0.0658 | 0.00165 |

Stage B answered YES for 40 of 40.

## Projection for the full run

- Stage B: 6,238 inputs at the pilot cost per trajectory, about $1.16.
- Stage C1: 1,500 inputs (the pilot YES rate applied to the B inputs, capped at 1,500), about $2.47.
- Total: about $3.63 against the $15.00 cap, so the full run fits.

## Actual spend

| stage | batches | requests | prompt_tokens | completion_tokens | cost_usd |
|---|---|---|---|---|---|
| pilot_b | 2 | 80 | 257,219 | 22,862 | 0.02 |
| pilot_c1 | 1 | 40 | 994,482 | 64,414 | 0.07 |
| pilot_c1_attempt1 | 1 | 40 | 1,161,249 | 90,588 | 0.08 |
| stage_b | 6 | 10,079 | 29,392,676 | 1,602,086 | 1.87 |
| stage_c1 | 3 | 1,500 | 42,873,528 | 2,406,093 | 2.75 |

Total spend on Stages B and C1, pilots included: $4.78 of the $15.00 cap.

## C1 to C2 agreement

40 trajectories have both C1 and C2 results. Across all criteria, scores agree exactly on 62% of pairs and within one point on 95%; Spearman correlation is 0.61.

| criterion | pairs | exact agreement | within one | mean C1 | mean C2 | spearman |
|---|---|---|---|---|---|---|
| safe_interruptibility | 40 | 50% | 88% | 0.93 | 0.50 | 0.30 |
| side_effects | 40 | 70% | 100% | 1.62 | 1.52 | 0.72 |
| absent_supervisor | 40 | 48% | 98% | 1.32 | 0.78 | 0.60 |
| reward_gaming | 40 | 50% | 92% | 1.23 | 1.10 | 0.42 |
| self_modification | 40 | 55% | 88% | 0.65 | 0.07 | 0.28 |
| distributional_shift | 40 | 82% | 95% | 0.55 | 0.57 | 0.68 |
| adversaries | 40 | 85% | 100% | 0.15 | 0.20 | 0.67 |
| safe_exploration | 40 | 60% | 100% | 0.90 | 1.05 | 0.56 |
