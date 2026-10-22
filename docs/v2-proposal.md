# kami-drills v2: proposal for Season 2

**Status: draft for review, not adopted.**

v1 scores one thing: whether a model makes the right next move from a frozen moment in a real run. This proposal
adds a second measured axis, **cadence** (how often and how long an agent keeps acting once it is free to stop),
fixes v1's largest known defects (replay gaps, low-information drills, a single-try pass rate), and sets out how
the v2 ranking is registered and graded against [Palaestra Season 2](https://palaestra.run).

Nothing here touches Season 1. The Season 1 forecast is frozen and is graded at close, 2026-10-25 20:41 UTC. v2 is
a new forecast, registered by timestamped commit before Season 2 opens and labelled "informed by Season 1". Anything
that uses Season 1 data to choose drills, weights or scoring is out-of-sample for Season 2 only; following the
[Registered Reports](https://www.cambridge.org/core/open-research/registered-reports) convention, anything not in
the registered document is reported as exploratory.

## 1. What ten models can show

Season 1 has ten drill-scored models with two to three live runs each. That is enough to grade a tier forecast and
not much more, and v2 should be designed from that arithmetic rather than from the number that comes out at close.

- **Power.** With the Bonett–Wright standard error for Spearman's ρ, the smallest correlation detectable at 80%
  power with n = 10 is about 0.84; at n = 20 it is 0.63 and at n = 27 about 0.55
  ([Bonett & Wright](https://www.cambridge.org/core/journals/psychometrika/article/sample-size-requirements-for-estimating-pearson-kendall-and-spearman-correlations/840CA9F25E521DBD1C389D48638CE2F1)).
  A true ρ of 0.6 at n = 10 yields sample values from slightly negative to about 0.9. Whatever Season 1 returns is
  a weak test of the drills, not a verdict on them.
- **Run level is not free n.** Runs of one model share one drill score. The one agent benchmark that decomposed
  run variance found model identity explains 64.8% and repeated runs of the same model 35.2%
  ([Business Arena](https://arxiv.org/pdf/2608.08621)). The effective n sits between 10 and 27; inference must
  cluster by model.
- **The bar in the literature is low.** The only paper defining predictive validity for agent rankings as
  in-sample to out-of-sample rank correlation reports −0.13 (n = 13) to 0.69 (n = 20), proposes ρ = 0.85 as a
  falsification threshold, and calls its own evidence "too thin to confirm"
  ([Beyond Static Leaderboards](https://arxiv.org/html/2606.19704v1)). Benchmark-to-benchmark correlations
  (median Spearman 0.73–0.79 across Epoch's suites) are inflated by wide release-date spans
  ([Epoch AI](https://epoch.ai/data-insights/benchmark-correlations)); Season 1's models are contemporaneous. No
  source found reports a correlation between an offline agent eval and a measured live outcome across models.
- **Two ceilings.** The observable correlation is bounded by the square root of the product of the two
  reliabilities ([attenuation](https://sk.sagepub.com/ency/edvol/embed/survey/chpt/attenuation)). If the drills
  are 0.7 reliable and a week of quests is 0.6 reliable across seeds, the ceiling is about 0.65. So the live
  criterion's across-run reliability must be measured, and drill reliability follows Spearman–Brown: doubling
  parallel items lifts 0.6 to 0.75 and 0.7 to 0.82. That is the case for a larger suite.

The Season 2 target is therefore a confidence interval that excludes zero and a correct top tier, with the
criterion's noise ceiling published beside it. Not ρ ≥ 0.85, which no agent benchmark has met.

## 2. What a frozen-state drill cannot see

A v1 drill wakes the model at a decisive moment, asks one question, and stops once the verdict is decided. It
measures decision quality when woken. It cannot measure whether the model wakes, how long it keeps acting, or
whether it keeps acting usefully. Every long-horizon agent benchmark that has looked finds that axis dominant:

- In Vending-Bench "tool use declined over time for all models"; one model "sometimes just waited for the next day
  without restocking", and failures did not track context fullness (Pearson 0.167), so shorter sessions would not
  fix them ([Vending-Bench](https://arxiv.org/html/2502.15840)).
- METR's hand-labelled failures split into two styles: one model mostly looped on failed actions (12 of 31),
  another mostly quit early (16 of 32) ([METR, Table 2](https://arxiv.org/html/2503.14499v4)).
- UltraHorizon gives the cleanest instrument. Remove the mandatory step cap and most models stop far earlier: one
  model's tool calls fell from 92.85 to 21.94 on the same tasks, another's from 85.33 to 22.81
  ([UltraHorizon](https://arxiv.org/html/2509.21766)). A budgeted-versus-free comparison of a short episode is a
  cheap, published measure of initiative.
- Ord's refit of METR's data finds hazard rates decline within a task (Weibull shape ≈ 0.6 for every model): agents
  that survive the early part are disproportionately likely to keep going
  ([Ord](https://tobyord.com/writing/hazard-rates-for-ai-agents-decline)). If that holds for a week-long season,
  first-day behaviour (loop established, or asleep?) should predict the week better than average drill accuracy.
- Only one paper treats the wait interval itself as the decision under test, with 15 LLM episodes and both arms at
  100% ([Learning When to Act](https://arxiv.org/abs/2603.22384)). A schedule drill is untested territory.
- Offline settings are kinder than live ones. Replacing τ-bench's simulated user with 451 real humans dropped the
  best agent from 77.8% to 63.6%, because simulators "quietly accommodate" agent errors
  ([Mind the Sim2Real Gap](https://arxiv.org/html/2603.11245v1)). A drill that hands over a clean state and a
  bounded question is forgiving in the same way.

The hypothesis for v2, to be tested on Season 1 data after close and then out-of-sample in Season 2: **live output
behaves like per-session yield multiplied by the number of sessions in which the agent keeps acting, and v1
measures a proxy for the first factor only.** Raw volume is not the second factor; HAL found more reasoning effort
lowered accuracy in most of 21,730 rollouts ([HAL](https://arxiv.org/abs/2510.11977v1)), so cadence should count
state-changing actions, not tokens or calls. Keeping v2 drills single-decision avoids the simulator-fidelity
risk; it means cadence must be measured by purpose-built items, not inferred.

## 3. What v1's own numbers say

Everything in this section is computed from the published v1 results
([`results/v1.0.0-rc2/summary.md`](../results/v1.0.0-rc2/summary.md),
[`models/`](../results/v1.0.0-rc2/models/), [`paired.txt`](../results/v1.0.0-rc2/paired.txt)).

**Signal is concentrated.** Comparing the mean of the three models the Season 1 forecast placed in the top tier
against the other eight drilled models (seven live-cohort models and the drill-only control): risk drills separate by +0.24 (4 of 5 drills in the expected direction), memory by +0.17
(5 of 6), progression by +0.15 (6 of 7). Onboarding separates by +0.05; repeat_failed and stale_state, 20 of the 47
drills, by +0.11 with three and two reversed drills. Six drills rank the top tier below the rest. Thirteen are
passed or failed by essentially everyone, including two no model passes, which need a rule review before
retirement. Hand-assigned difficulty does not track empirical difficulty (mean pass: easy 0.66, mid 0.69,
hard 0.44). Low-discrimination items are disproportionately mislabelled
([Fluid Benchmarking](https://arxiv.org/html/2509.11106v1)), so the reversed drills are as likely grader errors as
real blind spots and should be audited. Saturation matters too: the top model scores 100% on risk and most models
sit at 79–86% on onboarding; separating the top three from each other needs drills forked from their own mistakes.

**Replay gaps are the largest single threat.** Between 48% and 82% of v1 attempts hit at least one call with no
recorded answer. In the v1 eval logs reads are about 64% of miss instances, led by `quest_state`, `lens_account`,
`lens_kami`, `lens_status`, `travel_to_room`, `move_to_room` and `check_quest_completable`. The gaps are not
neutral: models that explore more hit more of them, and a "not administered" item is ignorable only when
non-administration is independent of ability; otherwise ignoring it inflates estimated ability
([missing responses in IRT](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2018.00964/pdf)).
Risk, the least gapped category, is the one whose separation is least likely an artefact; memory and progression,
the most gapped, the least trustworthy despite separating well. At a 60% gap rate a model is effectively scored on
about 19 drills, which roughly halves the suite's reliability.

**Era is a confound.** On the 10 drills forked from 4.7-era hosted runs, Sol scores 0.40 against 0.77 on the 37
KamiBench drills, while Gemini 3.6 Flash flips the other way (0.70 vs 0.50). v2 is built entirely from 4.7-era
Season 1 runs, so some v1-to-v2 rank movement will be source era, not capability, and should be reported as such.

**Tries disagree unevenly.** Drills where the two tries disagree range from 3 to 16 per model: the per-item
variance needed to decide whether a third or fourth try pays (section 5).

**Pairwise tests reject almost nothing.** Holm over 55 pairs leaves one significant pair. A "multiple comparisons
with the best" set, which held 8–35 plausible winners even on large leaderboards, is the honest pairwise claim at
this n ([Leaderboard Lottery, ICML 2026](https://icml.cc/virtual/2026/80012)).

## 4. Drill families for v2

### 4.1 Snapshot forks (fixes gaps)

Season 1 logs a full state snapshot at every session start (account, roster, every kami, quest states, inventory,
gas, lens status) that the agent never sees. Forking every v2 drill at a session start answers the snapshot's reads
exactly and at a known block, which removes the top read gaps; mid-session forks only where the transcript carries
the reads. Before Season 2, widen the live snapshot to the tools agents actually asked for (adjacent-room
`lens_node`, scavenge points, market, full quest list) so Season 2 data is gap-free at source, and align the drill
tool surface with the live one (live agents see 99 of 109 tools, drills show 108;
[pre-registration §5](https://palaestra.run/research/season-1)).

### 4.2 Cadence

- **Schedule drill.** The agent is at a session end with pending work and a known game clock (cooldowns, harvest
  timers). The oracle interval is computed from the clock; score is |log(chosen / oracle)|, so sleeping a day when
  a harvest finishes in an hour and waking every minute are both penalised.
- **Budget-use drill.** The same short episode run twice: once with a forced 50-call budget, once with a free
  "done". Report the ratio of state-changing actions. This is UltraHorizon's free-step collapse, instrumented
  per model.

### 4.3 Memory

Revision is the weakest competency everywhere it is measured: STALE's best model resolves 55.2% of stale-state
conflicts ([STALE](https://arxiv.org/abs/2605.06527)), MemoryAgentBench's multi-hop consolidation tops out at 28%
([MemoryAgentBench](https://arxiv.org/html/2507.05257v4)), and in the Memory Trust Gap suite models acted on a
stale note instead of an authoritative tool 92–100% of the time, larger models most when the note is dated newer
([Memory Trust Gap](https://arxiv.org/abs/2609.01852)). KamiBench's case studies supply fork templates from this
world: a rule that held for 60 sessions and broke when the expensive call fired before the notes were read; a
belief labelled "hypothesis" that got overturned where one stated as fact would not
([What agents remember](https://kamibench.ai/blog/what-agents-remember)).

- **Don't-repeat drill.** A live failure replayed with the agent's prior failure shown as a transcript excerpt, as a
  note it wrote, or not at all; score whether the next call repeats the normalised failure reason.
- **Stale-note drill.** A planted note contradicts the snapshot; reward the world. Include a "dated newer" variant.
- **Notes-before-first-call drill.** Does the agent read its notes before the first expensive call.
- **Note-writing**, if included, is scored by its downstream effect on a fresh session, not by a rubric; committing
  an unverified fact to memory raised downstream failure by 27 points in PASB
  ([PASB](https://arxiv.org/html/2607.10526v3)). ApprenticeBench's ablation (handbook only 11 bills passed, plus
  history 31, plus feedback 35, both 43; secondhand via [36Kr](https://eu.36kr.com/en/p/3990925961313283)) is the
  shape of effect to expect.

### 4.4 Polluted context

Injecting a model's own earlier errors into its context degrades late-turn accuracy, scaling makes it worse, and
thinking models stay stable ([Sinha et al.](https://arxiv.org/html/2509.09677v3)). Live agents operate on contexts
polluted by their own reverts. Take 10–15 existing drills and present each with and without the agent's own prior
error prepended; score the delta per model.

### 4.5 Chained decisions

Late failure in every long-horizon benchmark is coherence loss, not inability to do the step: Vending-Bench's
"meltdown loops", Game Arena's models "roughly even early" then drifting as games lengthen
([Game Arena](https://arxiv.org/html/2609.31473v1)). A chained drill runs 5–10 sequential decisions from one
snapshot, each result feeding the next, scored by step of first failure. It needs a forkable chain (an anvil fork
of Yominet with `evm_snapshot`/`evm_revert`), is the most expensive item here, and is subject to the chain
operator's approval. Taste-Bench is the one published case of a frozen-fork score transferring to held-out task
success (judge 30.0% to 47.9%, agent 14.6% to 33.7%; secondhand via
[The Decoder](https://the-decoder.com/ai-agents-have-bad-taste-in-long-term-decisions-and-a-new-benchmark-proves-it/));
its hardest forks were "detours" where the agent later corrected itself, a fork type Season 1 transcripts can supply after close.

### 4.6 API habits

Candidate API-habit classes v1 has no drill for, to be mined from Season 1 logs after close: wrong configuration
keys, reads of missing files, reads outside the local mirror, and swap or trade underflows. These are cheap to fork. Weighting families by how often the decision occurs live is the direct fix for
a suite whose mix is not matched to the live action mix.

### 4.7 Bank and selection

IRT will not rescue a ten-respondent test: Rasch difficulties are stable only to ±1 logit with 16–36 respondents
([rasch.org](https://www.rasch.org/rmt/rmt74m.htm)), and IRT-inspired selection "struggle[s] to meaningfully reduce
variance" on LLM evals ([Madaan et al.](https://arxiv.org/abs/2406.10229)). The selection logic transfers with
classical statistics: write a bank of 80–100 candidates, administer the 40–50 with corrected point-biserial ≥ 0.2
and model-level pass rates between 0.1 and 0.9, re-check each season, apply changes only prospectively.

## 5. Scoring

- **pass@1 primary, pass^2 secondary.** A quest fails if any step fails, so under constant per-step accuracy p an
  n-step task succeeds near p^n; METR's 80% horizons are 4–6× shorter than its 50% horizons
  ([METR](https://arxiv.org/html/2503.14499)), and τ-bench's agent fell from pass^1 ≈ 61% to pass^8 under 25%
  ([τ-bench](https://arxiv.org/pdf/2406.12045)). pass^k is the long-horizon quantity; pass@k measures discovery.
- **K from try-disagreement.** K = 2 captures about a third of recoverable resampling variance, K = 4 about half;
  stop raising K once E[σ²]/K is small beside between-item variance
  ([Miller](https://arxiv.org/html/2411.00640v1)). K = 3–4 on high-disagreement items.
- **Cluster-aware intervals.** Clustered standard errors can be 3× naive (same paper). 30 of v1's 47 drills come
  from one KamiBench run, and v2 forks share snapshots and mistake families. Bootstrap over snapshot families and
  report the cluster count.
- **Gap coding, pre-registered.** A gap before any scorable action is "not administered"; a gap after a wrong
  action is scored. Publish per-model gap rate beside pass rate and a score-0 sensitivity bound.
- **Tiers, not ranks**, with cost per model beside the score.

## 6. Validation design for Season 2

- **Forecast model.** One fixed two-component composite: decision score (pass^2 over the selected bank) predicts
  quests per session; cadence score (schedule + budget-use drills) predicts the share of sessions in which the
  agent keeps acting. The composite is their product or an equal-weight z-sum, written down before Season 2 and
  never revisited. Weights cannot be fitted on ten points; they are fixed from Season 1 and declared. The v1-style
  accuracy-only forecast is registered beside it, so Season 2 tests whether cadence adds, out-of-sample.
- **Returning models.** Season 1 live cadence is a legitimate registered predictor if declared; it tests whether
  cadence is a stable trait across seasons.
- **Primary metric.** Ranked probability score over top/middle/bottom tiers against two baselines, uniform and
  "Season 1 ranks". Strictly proper, pre-registrable, graded at close. ρ is reported descriptively with a
  model-cluster bootstrap CI. Pairwise claims use a multiple-comparisons-with-the-best set, not Holm.
- **Run-level model.** `live_run = b0 + b1·drill + u_model + e`, with a model random intercept. Hold out one third
  of drills from any tuning and report ρ on the held-out subset.
- **Validity chain.** Four claims with one piece of evidence each, following Kane and the NeurIPS 2025
  construct-validity audit (16% of 445 benchmarks used any uncertainty estimate;
  [Bean et al.](https://arxiv.org/html/2511.04703v1)): scoring (human audit of a drill sample), generalisation
  (try-try agreement, split-half reliability, across-run ICC of the criterion), extrapolation (the registered
  correlation with week-end quests), decision (RPS against the two baselines).
- **Power.** None of this makes n = 10 powerful. The levers are more models at 2–3 runs each, and pooling seasons
  with season as a fixed effect (two seasons ≈ 20 units, three ≈ 30), accumulating run-level response matrices
  toward the ~100 respondents a Rasch model would eventually need.

## 7. Plan

Ordering rule: fix the environment the instrument runs in, measure the noise ceiling, then add items, and register
everything before Season 2 opens. Effort is for one person: S under a day, M two to five days, L a week or more.
Items marked "after close" need Season 1 live data and wait for 2026-10-25 20:41 UTC.

| # | Item | Covers | Effort | Why |
|---|---|---|---|---|
| 1 | Fork every v2 drill from a Season 1 session-start snapshot; mid-session forks only where the transcript carries the reads | Gaps | M | Reads are ~64% of v1 misses |
| 2 | Widen the live snapshot to the tools agents ask for; align drill and live tool surfaces | Gaps | M | Season 2 gap-free at source; target < 10% |
| 3 | Pre-register gap coding; publish gap rate and a score-0 sensitivity bound per model | Scoring | S | Gap rate tracks ability |
| 4 | After close: across-run ICC of week-end quests, split-half seed reliability, attenuation ceiling | Validation | S | Caps achievable ρ; drills vs runs spend |
| 5 | After close: item analysis of v1 against week-end quests; retire the 13 low-information drills; audit the 6 reversed and 2 universally failed | Scoring | S | Discrimination selection without IRT |
| 6 | 80–100 drill bank from the 27 Season 1 runs, weighted by live action frequency, including top-tier mistakes and API-habit classes; administer the 40–50 that pass item filters | Bank | L | Reliability; separates the top tier |
| 7 | Cadence family: schedule drill and budget-use drill | New family | M | The axis v1 cannot see |
| 8 | Memory family: don't-repeat, stale-note (with "dated newer"), notes-before-first-call | New family | M | Revision is the weakest competency |
| 9 | Polluted-context variant of 10–15 existing drills | New family | S | Measured decay mechanism |
| 10 | pass@1 primary, pass^2 secondary; K from try-disagreement; bootstrap over snapshot families; tiers not ranks | Scoring | S | Compounding reliability; clustered SEs |
| 11 | Register one fixed composite (decision + cadence) and the accuracy-only forecast beside it, by timestamped commit | Forecast | S | Tests whether cadence adds |
| 12 | RPS over tiers vs uniform and Season-1-ranks baselines; ρ descriptive with cluster CI; MCB set for pairwise | Validation | S | Proper, registrable, graded at close |
| 13 | Run-level mixed model; hold out one third of drills; report held-out ρ | Validation | S–M | Within-model variation; holdout norm |
| 14 | After close: classify every session end (completed / slept with pending work / gave up / errored); mine requested vs clamped wake intervals; test whether day-1 loop establishment predicts the week | Mine S1 | M | METR-style labelling; Ord's hazard |
| 15 | After close: recode v1 attempts by gap type against live rank; mine mistake families with a windowed repeat definition; diff notes against snapshots for stale beliefs | Mine S1 | M | Tests gap ignorability; seeds drills |
| 16 | Chained drills on an anvil fork of Yominet, scored by step of first failure | New family, gaps | L | After 1–15; subject to the chain operator's approval |
| 17 | A notes-reset (stateless) arm for one or two models in Season 2 | Validation | L (cost) | Stateful minus stateless = "learned" ([PAST-Bench](https://arxiv.org/abs/2608.04003)) |

## 8. Open questions

- **Composite form.** Product (matches the yield × persistence hypothesis) or equal-weight z-sum (robust to one
  noisy component)? Decide before Season 2 and do not revisit.
- **Schedule oracle.** The game clock gives a lower bound on the useful wake interval, not a unique answer. Is
  |log ratio| against the earliest useful wake the right loss, or should there be a tolerance band?
- **Era.** Drop the KamiBench-era drills entirely (removes the confound) or keep a labelled subset (keeps v1
  comparability)?
- **Returning-model predictor.** A composite that includes Season 1 live cadence is partly live-derived. Is that
  still a drill forecast, or a third registered forecast?
- **Chained drills.** Worth the anvil-fork cost before the cadence family has been validated once?
