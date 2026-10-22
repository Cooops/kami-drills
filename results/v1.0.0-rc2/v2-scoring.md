# v1.0.0-rc2 re-scored under the v2 rules

**This is a re-analysis of the published v1 drill results only. It is not a Season 1 or Season 2 result and uses no live data, no new model calls, and nothing outside this repository.** v1 grading and the v1 published tables are unchanged. The v2 definitions are the experimental ones in `kami_drills/scoring_v2.py` (`pass_hat_k`, `cluster_bootstrap_ci`, `item_stats`, `gap_sensitivity`) and `docs/v2-proposal.md` section 5; the gap rule is `docs/v2-gap-coding.md`.

**The registered Season 1 forecast ([pre-registration](https://palaestra.run/research/season-1)) uses v1 pass rate and is unchanged. Nothing here is a forecast or replaces it;** the tiers below exist only to show how sensitive v1 rankings are to the scoring rule.

Inputs: `models/*.json` (pass per drill per try, gap flag per drill per try) and each drill's `provenance` in `drills/*.json`. 11 models, 47 drills, 2 tries.

## Method

- **pass@1** is the v1 mean pass (per drill the mean over the 2 tries, then the mean over drills); it reproduces v1's table exactly. **pass^2** is the share of drills where both tries pass.
- **Clusters (headline: snapshot family).** A family is the source run plus the arm directory of the source session (`provenance.source.dataset` + first path part of `session_file`). This gives **9 clusters** (sizes 1, 2, 3, 4, 6, 6, 7, 8, 10). Sensitivities: by source run only (**5 clusters**: 30, 7, 4, 3, 3 drills; KamiBench run 006 holds 30 of 47) and by source session (43 clusters, nearly one per drill).
- **CI:** percentile cluster bootstrap over drills, 10,000 resamples, seed 0, `cluster_bootstrap_ci`. With 9 (or 5) clusters these intervals are themselves rough.
- **Item statistics:** respondents are the 22 model-tries, each scored 0/1 per drill. Pass rate, corrected point-biserial (drill vs the rest-of-suite total in the same try, `item_stats`), low-information (pass rate >= 0.95 or <= 0.05), reversed (corrected r < 0). As a cross-check, Spearman rho of the drill's per-model score against the rest-of-suite model score (11 models) is reported; "rank-confirmed" means both are negative.
- **Tiers** are not defined in the repo. For this note only: top = rank 1-3, middle = 4-8, bottom = 9-11 (competition ranking, ties share a rank). The split is arbitrary; read the CI column too.

## Per-model results

| model | pass@1 | pass^2 | pass^2 - pass@1 | 95% CI pass@1 (9 clusters) | 95% CI pass^2 (9 clusters) | rank pass@1 > pass^2 | tier pass@1 > pass^2 |
|---|---|---|---|---|---|---|---|
| google/gemini-4-argon | 74.5% | 66.0% | -8.5 | 66-83% | 56-76% | 1 > 1 | top > top |
| openai/gpt-6.1-sol | 69.1% | 66.0% | -3.2 | 55-85% | 51-84% | 2 > 1 | top > top |
| anthropic/claude-opus-5.5 | 67.0% | 57.4% | -9.6 | 54-79% | 44-71% | 3 > 4 | top > middle |
| google/gemini-3.8-flash | 64.9% | 61.7% | -3.2 | 54-76% | 52-75% | 4 > 3 | middle > top |
| meta/muse-wonder | 63.8% | 55.3% | -8.5 | 49-79% | 38-73% | 5 > 5 | middle > middle |
| anthropic/claude-haiku-5.5 | 61.7% | 55.3% | -6.4 | 55-72% | 47-69% | 6 > 5 | middle > middle |
| openai/gpt-6-luna | 60.6% | 48.9% | -11.7 | 45-74% | 29-67% | 7 > 8 | middle > middle |
| anthropic/claude-sonnet-5.5 | 58.5% | 53.2% | -5.3 | 45-74% | 38-72% | 8 > 7 | middle > middle |
| google/gemini-3.6-flash | 54.3% | 48.9% | -5.3 | 35-71% | 30-67% | 9 > 8 | bottom > middle |
| x-ai/grok-4.7 | 53.2% | 36.2% | -17.0 | 37-70% | 20-54% | 10 > 10 | bottom > bottom |
| deepseek/deepseek-v4-pro-0813 | 47.9% | 31.9% | -16.0 | 32-64% | 15-51% | 11 > 11 | bottom > bottom |

Every model loses points under pass^2 (mean -8.6 points; range -3.2 to -17.0). Cluster count: 9 (headline), 5 (run-level), 43 (session-level).

Sensitivity of the pass@1 CI to the cluster definition:

| model | by run (5) | by family (9) | by session (43) | v1 summary (naive, 47) |
|---|---|---|---|---|
| google/gemini-4-argon | 71-81% | 66-83% | 63-85% | 63-85% |
| openai/gpt-6.1-sol | 46-78% | 55-85% | 55-82% | 56-82% |
| anthropic/claude-opus-5.5 | 50-74% | 54-79% | 54-79% | 55-79% |
| google/gemini-3.8-flash | 60-84% | 54-76% | 51-78% | 52-78% |
| meta/muse-wonder | 58-79% | 49-79% | 51-76% | 51-76% |
| anthropic/claude-haiku-5.5 | 56-82% | 55-72% | 49-74% | 49-74% |
| openai/gpt-6-luna | 47-65% | 45-74% | 48-73% | 49-72% |
| anthropic/claude-sonnet-5.5 | 48-66% | 45-74% | 44-72% | 46-71% |
| google/gemini-3.6-flash | 48-72% | 35-71% | 41-67% | 40-67% |
| x-ai/grok-4.7 | 44-81% | 37-70% | 41-65% | 41-65% |
| deepseek/deepseek-v4-pro-0813 | 42-68% | 32-64% | 35-60% | 36-60% |

## Do ranks or tiers change?

**Ranks.** Under pass^2 (ties share a rank): gemini-3.8-flash 4 -> 3, gpt-6.1-sol 2 -> 1 (ties gemini-4-argon at 66.0%), claude-opus-5.5 3 -> 4, claude-haiku-5.5 6 -> 5 (ties muse-wonder), claude-sonnet-5.5 8 -> 7, gpt-6-luna 7 -> 8 (ties gemini-3.6-flash at 48.9%), gemini-3.6-flash 9 -> 8. gemini-4-argon (first), grok-4.7 (10th) and deepseek-v4-pro (11th) do not move. All movement is one place, and several are ties.

**Tiers (3/5/3 split).**
- claude-opus-5.5 falls from top to middle (67.0% -> 57.4%; gemini-3.8-flash overtakes it at 61.7%).
- gemini-3.8-flash rises from middle to top.
- gemini-3.6-flash moves from bottom to middle only through the gpt-6-luna tie at 48.9%; it is not a robust move (both tie at rank 8, a middle rank, one place above the bottom).
- No other model changes tier. The bottom (grok-4.7, deepseek-v4-pro) is stable and gets worse in absolute terms: they have the largest pass^2 gaps (-17.0, -16.0), i.e. the least consistent tries.

**Do the CIs separate anything?** Counting model pairs where one CI lies wholly above the other (9 families):
- pass@1: 1 pair of 55 (gemini-4-argon over deepseek-v4-pro).
- pass^2: 4 pairs (gemini-4-argon over grok-4.7 and deepseek-v4-pro; gpt-6.1-sol and gemini-3.8-flash over deepseek-v4-pro).
- With run-level clusters (5): 3 pairs at pass@1, 5 at pass^2; with session-level (43): 1 at pass@1.
- Every top-tier and middle-tier CI overlaps every other. The only defensible statement is a coarse one: gemini-4-argon, gpt-6.1-sol, gemini-3.8-flash are the plausible top group, grok-4.7 and deepseek-v4-pro are the plausible bottom, and the rest are not separable. The Opus top-to-middle move is inside the noise (pass^2 CIs: opus 44-71%, gemini-3.8-flash 52-75%).
- Clustering did not widen the CIs consistently versus v1's naive drill bootstrap: with so few families the width depends on which families a model fails in (e.g. gpt-6.1-sol 56-82% naive, 46-78% by run, 55-85% by family).

## Per-drill results (11 models x 2 tries)

`r` = corrected point-biserial over 22 model-tries; `rho` = Spearman over 11 models; `gap` = share of the 22 attempts that met at least one unanswered call. n/a = pass rate 0 or 1 (no variance). Audit = existing `audit.verdict` in the drill file.

| drill | category | pass rate | r | rho | flags | gap | audit |
|---|---|---|---|---|---|---|---|
| memory-sonnet-stamina-unreliable-belief | memory | 0% | n/a | n/a | low-info | 86% | fair |
| stale-levelup-while-harvesting | stale_state | 0% | n/a | n/a | low-info | 86% |  |
| memory-luna-levelup-needs-no-points-belief | memory | 9% | -0.14 | -0.07 | reversed (rank-confirmed) | 86% | change |
| levelup-not-resting | levelup | 23% | -0.11 | -0.18 | reversed (rank-confirmed) | 77% |  |
| memory-luna-plan-level-after-rest | memory | 23% | +0.55 | +0.69 |  | 100% |  |
| prog-gpt52-locked-quest-chain | progression | 23% | +0.37 | +0.45 |  | 27% |  |
| repeat-take-trade-underflow | repeat_failed | 23% | -0.37 | -0.57 | reversed (rank-confirmed) | 18% |  |
| onboard-travel-not-move | onboarding | 27% | -0.26 | -0.53 | reversed (rank-confirmed) | 82% |  |
| prog-sonnet-unspent-skill-points | progression | 27% | +0.22 | +0.08 |  | 82% |  |
| stale-notes-resting-vs-harvesting | stale_state | 27% | +0.56 | +0.59 |  | 50% |  |
| stale-notes-wrong-node-stop | stale_state | 32% | +0.03 | -0.12 |  | 55% |  |
| memory-sonnet-slow-points-belief | memory | 36% | -0.05 | -0.01 | reversed (rank-confirmed) | 100% | change |
| repeat-cooldown-feed | repeat_failed | 36% | +0.07 | +0.16 |  | 95% |  |
| repeat-stop-starving | repeat_failed | 36% | +0.20 | +0.36 |  | 91% |  |
| stale-harvest-start-wrong-room | stale_state | 41% | +0.07 | +0.31 |  | 55% |  |
| prog-luna-two-levels-pending | progression | 45% | -0.00 | +0.01 | reversed | 86% |  |
| stale-travel-before-cooldown-stop | stale_state | 45% | -0.18 | -0.20 | reversed (rank-confirmed) | 59% | change |
| repeat-collect-too-far-stop | repeat_failed | 50% | +0.12 | +0.07 |  | 5% |  |
| risk-lowhp-feed-first | risk | 50% | +0.29 | +0.30 |  | 27% |  |
| risk-unmanaged-lowhp | risk | 59% | +0.23 | +0.36 |  | 73% |  |
| memory-sonnet-avoid-registry-dump | memory | 64% | +0.29 | +0.40 |  | 77% |  |
| prog-timewall-quest19-to-20 | progression | 64% | +0.27 | +0.30 |  | 77% |  |
| repeat-newbie-vendor-guess | repeat_failed | 64% | +0.59 | +0.75 |  | 91% |  |
| repeat-stop-cooldown-identical | repeat_failed | 64% | +0.09 | +0.52 |  | 55% |  |
| prog-luna-banked-xp | progression | 68% | -0.12 | -0.25 | reversed (rank-confirmed) | 82% |  |
| repeat-pool-swap-retry | repeat_failed | 68% | +0.03 | +0.02 |  | 64% |  |
| onboard-bridge-pending | onboarding | 73% | +0.46 | +0.52 |  | 86% |  |
| stale-double-complete-quest | stale_state | 73% | +0.34 | +0.36 |  | 95% |  |
| onboard-first-kami-vendor | onboarding | 77% | -0.10 | +0.20 | reversed | 82% |  |
| onboard-quest2-move-three | onboarding | 77% | +0.15 | +0.33 |  | 95% |  |
| stale-cooldown-retry-start | stale_state | 77% | -0.04 | -0.14 | reversed (rank-confirmed) | 59% |  |
| repeat-cooldown-start-wait | repeat_failed | 82% | +0.10 | +0.14 |  | 32% |  |
| prog-timewall-plan-stop-complete | progression | 86% | +0.10 | +0.20 |  | 68% |  |
| repeat-listing-buy-underflow | repeat_failed | 86% | -0.07 | -0.13 | reversed (rank-confirmed) | 95% |  |
| onboard-operator-unfunded | onboarding | 91% | +0.12 | +0.00 |  | 45% |  |
| risk-revive-then-heal | risk | 91% | +0.58 | +0.63 |  | 64% |  |
| memory-sonnet-zero-hp-is-dead-belief | memory | 95% | +0.50 | +0.50 | low-info | 9% |  |
| onboard-complete-before-accept | onboarding | 95% | +0.05 | +0.35 | low-info | 27% |  |
| prog-gpt52-complete-when-met | progression | 95% | +0.11 | +0.20 | low-info | 86% |  |
| repeat-quest-prereq-locked | repeat_failed | 95% | +0.50 | +0.50 | low-info | 82% |  |
| risk-starving-feed-first | risk | 95% | +0.11 | +0.05 | low-info | 9% |  |
| stale-harvest-start | stale_state | 95% | +0.50 | +0.50 | low-info | 95% |  |
| stale-levelup-twice | stale_state | 95% | +0.50 | +0.50 | low-info | 91% |  |
| onboard-fund-amount | onboarding | 100% | n/a | n/a | low-info | 9% |  |
| risk-dying-kami-remote | risk | 100% | n/a | n/a | low-info | 41% |  |
| stale-feed-from-shop-room | stale_state | 100% | n/a | n/a | low-info | 55% |  |
| stall-no-kami | stalling | 100% | n/a | n/a | low-info | 95% |  |

Counts: 13 low-information drills (2 at 0%, 4 at 100%, 7 at 95%); 11 reversed by r < 0, of which 9 are rank-confirmed. Several r values sit within 0.05 of zero (stale-cooldown-retry-start, memory-sonnet-slow-points-belief, prog-luna-two-levels-pending) and are noise-level, not evidence of a blind spot. With 22 respondents, |r| below about 0.4 is not distinguishable from zero.

## Retire and audit candidates

**Retire (no ranking information; 100% pass, nothing to separate):**
- `onboard-fund-amount`, `risk-dying-kami-remote`, `stale-feed-from-shop-room`, `stall-no-kami`: every model passes both tries (pass rate 100%, r undefined).

**Retire or demote to a smoke test (one failed attempt in 22, pass rate 95%):**
- `memory-sonnet-zero-hp-is-dead-belief`, `onboard-complete-before-accept`, `prog-gpt52-complete-when-met`, `repeat-quest-prereq-locked`, `risk-starving-feed-first`, `stale-harvest-start`, `stale-levelup-twice`. Low-info by the 0.95 rule; their r (0.05-0.50) comes from a single failing attempt and is not meaningful.

**Rule review before retirement (no model passes):**
- `memory-sonnet-stamina-unreliable-belief` (audit verdict `fair`, already corrected once) and `stale-levelup-while-harvesting`. Both have 86% gap rates, so an unanswered-call cause cannot be ruled out. If the rule check holds, retire from ranking.

**Audit (reversed against the rest of the suite and rank-confirmed, so likely grader error or a rule that rewards the wrong behaviour):**
- `repeat-take-trade-underflow` (r -0.37, rho -0.57, 23% pass, gap 18%): the strongest reversal with a low gap rate, so the signal is not a replay artefact; audit first.
- `onboard-travel-not-move` (r -0.26, rho -0.53, 27%, gap 82%).
- `stale-travel-before-cooldown-stop` (r -0.18, 45%): already changed by audit; re-check after the change.
- `prog-luna-banked-xp` (r -0.12, 68%, gap 82%).
- `levelup-not-resting` (r -0.11, 23%, gap 77%).
- `memory-luna-levelup-needs-no-points-belief` (r -0.14, 9%, also low-info; rule already changed to "lv2 only").
- `repeat-listing-buy-underflow` (r -0.07, 86%, gap 95%), `stale-cooldown-retry-start` (r -0.04, 77%), `onboard-first-kami-vendor` (r -0.10 but rho +0.20, not rank-confirmed), `memory-sonnet-slow-points-belief` (r -0.05, audited `change`), `prog-luna-two-levels-pending` (r -0.00): weak, noise-level; audit only if capacity allows.

Gap confound: 10 of the 11 reversed drills have a gap rate of 59% or more. A reversal on a heavily gapped drill may reflect which models happened to hit the missing responses rather than the rule. `repeat-take-trade-underflow` (18%) is the clean case.

If all 11 retire-or-demote drills and the 2 rule-review drills were removed, 34 of 47 drills would remain; this was not re-scored here.

## Gap sensitivity

**What is public:** `per_drill_gap` holds one flag per drill per try: did the attempt meet at least one unanswered call. The per-call order is not public, so the `gap_code` split (not_administered = a gap before any scorable action; scored = gap after an action; clean) **cannot be computed**, and `gap_sensitivity` cannot be run as specified. **Missing:** per-attempt call sequences (the eval logs, which are not in this repository) to separate not_administered from scored gaps.

**What was computed instead:** the any-gap attempt flag as a stand-in. "Gap-free only" drops every attempt with any gap; "gap = 0" scores every gap attempt as 0, including passes. Both are more aggressive than the pre-registered rule, so neither is a bound on the true gap-coded score; they show only how much of each model's score rests on gapped attempts. 94 attempts per model.

| model | attempts with any gap | pass@1 (all) | pass@1 on gap-free attempts (n of 94) | pass@1, gap attempts = 0 | rank all / gap-free / gap=0 |
|---|---|---|---|---|---|
| google/gemini-4-argon | 71% | 74.5% | 63.0% (27) | 18.1% | 1 / 6 / 6 |
| openai/gpt-6.1-sol | 63% | 69.1% | 77.1% (35) | 28.7% | 2 / 1 / 2 |
| anthropic/claude-opus-5.5 | 64% | 67.0% | 64.7% (34) | 23.4% | 3 / 3 / 4 |
| google/gemini-3.8-flash | 70% | 64.9% | 64.3% (28) | 19.1% | 4 / 4 / 5 |
| meta/muse-wonder | 73% | 63.8% | 44.0% (25) | 11.7% | 5 / 10 / 11 |
| anthropic/claude-haiku-5.5 | 48% | 61.7% | 63.3% (49) | 33.0% | 6 / 5 / 1 |
| openai/gpt-6-luna | 82% | 60.6% | 76.5% (17) | 13.8% | 7 / 2 / 10 |
| anthropic/claude-sonnet-5.5 | 57% | 58.5% | 62.5% (40) | 26.6% | 8 / 7 / 3 |
| google/gemini-3.6-flash | 68% | 54.3% | 50.0% (30) | 16.0% | 9 / 9 / 8 |
| x-ai/grok-4.7 | 69% | 53.2% | 58.6% (29) | 18.1% | 10 / 8 / 6 |
| deepseek/deepseek-v4-pro-0813 | 62% | 47.9% | 38.9% (36) | 14.9% | 11 / 11 / 9 |

- 48% to 82% of attempts have a gap, matching v1's gap-rate column; the gap-free sample is only 17 to 49 attempts per model, drawn from different drills for each model.
- Ranks are not stable to gap handling: gpt-6-luna goes 7 -> 2 (gap-free) -> 10 (gap = 0); muse-wonder 5 -> 10 -> 11; claude-haiku-5.5 6 -> 5 -> 1 (it has the lowest gap rate, 48%); gemini-4-argon 1 -> 6 -> 6 (63% gap-free, 71% gap rate). The models with the highest gap rate gain most from exclusion or lose most from zeroing.
- The gap-free comparison is itself confounded: a model that explores more hits more gaps, and which attempts survive depends on the model. Treat the gap-based ranks as a warning that v1 ranks in the middle of the table depend on gap handling, not as an alternative ranking.

## Caveats

- Re-analysis of v1 drill results only (47 drills, 2 tries, one stack, one run window). Not live results; no inference about Season 1 or Season 2 standings.
- Two tries make pass^2 a coarse statistic: per drill it can only be 0 or 1, and each model's pass^2 rests on 47 binary outcomes. Ties are frequent.
- Clusters are few (9 families, 5 runs). The bootstrap resamples whole clusters, so with this few clusters the CIs are unstable and a cluster's contribution to the CI depends on what a single family looks like. Cluster definitions are my reading of `provenance`; a different definition changes the CIs (table above).
- Item correlations use 22 model-tries from 11 models; the two tries of a model are not independent, so 22 overstates the effective sample. Models whose scores drive the rest-of-suite total also drive r, and drills in the same family share a snapshot.
- Tier cut-offs (3/5/3) are chosen for this note and are not a repository rule.
- Gap timing is not public, so the pre-registered gap coding was not applied (see above). Gap flags are per attempt, not per call.
- Era confound from the proposal (hosted 4.7-era drills vs KamiBench drills) is not separated here.
- Reproduction: all numbers come from `kami_drills/scoring_v2.py` plus the files named above, seed 0; the script is not committed to keep this change to one file.
