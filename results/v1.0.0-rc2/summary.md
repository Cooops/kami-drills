# kami-drills v1.0.0-rc2: 11 models × 47 drills × 2 tries

Stack: kami-harness 4.7.0 df7129f, kami-agent 0.7.5 8e444bb, kami-lens 1.0.5 e604290, gdd 3c6f4f9.

| model | mean pass | 95% CI (bootstrap over drills) | pass on every try | drills where tries disagree | gap rate | approx. cost |
|---|---|---|---|---|---|---|
| google/gemini-4-argon | 74% | 63%–85% | 31/47 | 8 | 71% | $14.01 |
| openai/gpt-6.1-sol | 69% | 56%–82% | 31/47 | 3 | 63% | $15.00 |
| anthropic/claude-opus-5.5 | 67% | 55%–79% | 27/47 | 9 | 64% | $135.48 |
| google/gemini-3.8-flash | 65% | 52%–78% | 29/47 | 3 | 70% | $9.83 |
| meta/muse-wonder | 64% | 51%–76% | 26/47 | 8 | 73% | $56.32 |
| anthropic/claude-haiku-5.5 | 62% | 49%–74% | 26/47 | 6 | 48% | $8.91 |
| openai/gpt-6-luna | 61% | 49%–72% | 23/47 | 11 | 82% | $0.98 |
| anthropic/claude-sonnet-5.5 | 59% | 46%–71% | 25/47 | 5 | 57% | $50.30 |
| google/gemini-3.6-flash | 54% | 40%–67% | 23/47 | 5 | 68% | $7.04 |
| x-ai/grok-4.7 | 53% | 41%–65% | 17/47 | 16 | 69% | $25.03 |
| deepseek/deepseek-v4-pro-0813 | 48% | 36%–60% | 15/47 | 15 | 62% | $9.19 |

| model | levelup (1) | memory (6) | onboarding (7) | progression (7) | repeat_failed (10) | risk (5) | stale_state (10) | stalling (1) |
|---|---|---|---|---|---|---|---|---|
| google/gemini-4-argon | 50% | 58% | 79% | 93% | 50% | 100% | 80% | 100% |
| openai/gpt-6.1-sol | 0% | 33% | 86% | 50% | 80% | 100% | 70% | 100% |
| anthropic/claude-opus-5.5 | 0% | 58% | 79% | 64% | 75% | 90% | 50% | 100% |
| google/gemini-3.8-flash | 0% | 42% | 86% | 64% | 80% | 70% | 50% | 100% |
| meta/muse-wonder | 100% | 50% | 86% | 71% | 55% | 60% | 55% | 100% |
| anthropic/claude-haiku-5.5 | 0% | 33% | 86% | 21% | 70% | 100% | 65% | 100% |
| openai/gpt-6-luna | 0% | 25% | 64% | 64% | 60% | 80% | 70% | 100% |
| anthropic/claude-sonnet-5.5 | 0% | 25% | 86% | 50% | 65% | 60% | 60% | 100% |
| google/gemini-3.6-flash | 0% | 17% | 64% | 57% | 50% | 80% | 60% | 100% |
| x-ai/grok-4.7 | 50% | 58% | 64% | 50% | 45% | 60% | 45% | 100% |
| deepseek/deepseek-v4-pro-0813 | 50% | 17% | 71% | 57% | 35% | 70% | 40% | 100% |

Paired comparison (55 pairs, sign-flip permutation on per-drill differences, Holm-adjusted): 1 pair(s) with q < .05. Full list: [paired.txt](paired.txt).

Mean pass: per drill, the mean over tries, then the mean over drills. 95% CI: percentile bootstrap over drills (10,000 resamples, seed 0). Gap rate: share of attempts that met at least one call with no recorded response. Approx. cost: every model call priced from its token usage at pinned OpenRouter list prices ([prices-2026-10-16.json](../prices-2026-10-16.json)), or the call's logged OpenRouter cost where that is larger; see docs/method.md. Per-model files in [models/](models/) give pass per drill per try.

Logs: 3368872, 6d1ac51, b42af75 (eval revisions); these logs carry no suite metadata, so the suite version and stack above are those of this package.
