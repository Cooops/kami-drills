# kami-drills v1.0.0-rc1: 10 models × 47 drills × 2 tries

Stack: kami-harness 4.7.0 df7129f, kami-agent 0.7.5 8e444bb, kami-lens 1.0.5 e604290, gdd 3c6f4f9.

| model | mean pass | 95% CI (bootstrap over drills) | pass on every try | drills where tries disagree | gap rate | cost |
|---|---|---|---|---|---|---|
| google/gemini-4-argon | 71% | 60%–83% | 30/47 | 7 | 70% | $10.51 |
| openai/gpt-6.1-sol | 64% | 50%–78% | 29/47 | 2 | 62% | $13.49 |
| anthropic/claude-opus-5.5 | 63% | 50%–76% | 26/47 | 7 | 63% | $76.95 |
| meta/muse-wonder | 63% | 50%–76% | 26/47 | 7 | 72% | $31.64 |
| anthropic/claude-haiku-5.5 | 60% | 47%–72% | 25/47 | 6 | 48% | $6.00 |
| openai/gpt-6-luna | 59% | 46%–70% | 22/47 | 11 | 81% | $0.80 |
| google/gemini-3.6-flash | 54% | 40%–67% | 23/47 | 5 | 66% | $3.68 |
| anthropic/claude-sonnet-5.5 | 54% | 41%–68% | 23/47 | 5 | 54% | $34.47 |
| x-ai/grok-4.7 | 52% | 40%–64% | 17/47 | 15 | 70% | $15.19 |
| deepseek/deepseek-v4-pro-0813 | 47% | 35%–59% | 14/47 | 16 | 62% | $5.26 |

| model | levelup (1) | memory (6) | onboarding (7) | progression (7) | repeat_failed (10) | risk (5) | stale_state (10) | stalling (1) |
|---|---|---|---|---|---|---|---|---|
| google/gemini-4-argon | 50% | 58% | 79% | 93% | 50% | 80% | 75% | 100% |
| openai/gpt-6.1-sol | 0% | 33% | 86% | 50% | 75% | 80% | 60% | 100% |
| anthropic/claude-opus-5.5 | 0% | 58% | 79% | 64% | 65% | 80% | 45% | 100% |
| meta/muse-wonder | 100% | 50% | 86% | 71% | 55% | 60% | 50% | 100% |
| anthropic/claude-haiku-5.5 | 0% | 33% | 86% | 21% | 65% | 100% | 60% | 100% |
| openai/gpt-6-luna | 0% | 25% | 64% | 64% | 50% | 80% | 70% | 100% |
| google/gemini-3.6-flash | 0% | 17% | 64% | 57% | 50% | 80% | 60% | 100% |
| anthropic/claude-sonnet-5.5 | 0% | 25% | 86% | 50% | 55% | 60% | 50% | 100% |
| x-ai/grok-4.7 | 50% | 67% | 64% | 50% | 40% | 60% | 40% | 100% |
| deepseek/deepseek-v4-pro-0813 | 50% | 17% | 71% | 57% | 35% | 70% | 35% | 100% |

Paired comparison (45 pairs, sign-flip permutation on per-drill differences, Holm-adjusted): 1 pair(s) with q < .05. Full list: [paired.txt](paired.txt).

Mean pass: per drill, the mean over tries, then the mean over drills. 95% CI: percentile bootstrap over drills (10,000 resamples, seed 0). Gap rate: share of attempts that met at least one call with no recorded response. Cost: OpenRouter-billed cost per call, summed from the logs. Per-model files in [models/](models/) give pass per drill per try.

Logs: 3368872, b42af75 (eval revisions); these logs carry no suite metadata, so the suite version and stack above are those of this package.
