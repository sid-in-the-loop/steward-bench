# Pilot

40 Stage A survivors, picked round-robin across domains (ai_rnd: 3, swe: 13, terminal_devops: 12, workplace: 12), went through Stage B and Stage C1 on the OpenRouter Batch API with anthropic/claude-haiku-5.5, and then through Stage C2 in one cloud session. Token counts and costs are the usage the batches reported.

| stage | requests | valid outputs | prompt tokens | completion tokens | prompt tokens per trajectory | cost (USD) | cost per trajectory (USD) |
|---|---|---|---|---|---|---|---|
| B | 40 | 40 | 114,786 | 6,862 | 2,870 | 0.0075 | 0.00019 |
| C1 | 40 | 40 | 994,482 | 64,414 | 24,862 | 0.0658 | 0.00165 |

Stage B answered YES for 40 of 40.

## Projection for the full run

- Stage B: 6,238 inputs at the pilot cost per trajectory, about $1.16.
- Stage C1: 1,500 inputs (the pilot YES rate applied to the B inputs, capped at 1,500), about $2.47.
- Total: about $3.63 against the $15.00 cap, so the full run fits.

## C1 to C2 agreement

No pilot C2 results are available yet.
