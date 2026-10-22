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
  ([Business Arena](https://arxiv.org/pdf/2608.08621)). The forecast is graded on model means, so its effective n
  is 10, not 27. A run-level mixed model cannot recover within-model drill effects, because every run of a model
  carries the same drill score; it is used only to estimate the criterion's across-run reliability (ICC).
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

The Season 2 primary claim is therefore **positive RPS skill against a calibrated Season 1 baseline** (section 6.1),
with the criterion's noise ceiling published beside it. The baseline gives each returning model P(tier) from
bootstrapping its own Season 1 live runs, the same calibration machinery the drill forecast gets. Not "a ρ interval
that excludes zero": the v2 bank is chosen and tuned on Season 1, so a bank that merely reproduces Season 1's order
would clear that bar whenever ranks persist across seasons. Not skill against the Season 1 ranks taken as certain
either: a hedged copy of the Season 1 tiers (0.7 on the Season 1 tier, 0.25 adjacent, 0.05 far) carries no drill
information and still beats the certain forecast (review arithmetic: about +0.06 RPS skill with 2 of 10 tier swaps,
+0.28 with 4). In a simulation where seasons differ only by run-to-run noise (30 seasons, 10 models, 3 runs each;
`tests/test_scoring_v2.py`) the copy averages +0.23 skill against the certain forecast and −0.04 against the
calibrated baseline. What positive skill would show: the drill forecast placed Season 2 models better than
Season 1's live results did, with their run noise priced in. What it would not show: the bootstrap is calibrated to
run-to-run noise only, not to change between seasons, so when ranks shift for other reasons any forecast hedged wider
than run noise can beat it without drill information. That is why the hedged copy is reported beside it as a control,
and why at n = 10 a positive skill is a direction, not proof that the drills measure what Season 1 could not. Not
ρ ≥ 0.85 either, which no agent benchmark has met.

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
KamiBench drills, while Gemini 3.6 Flash flips the other way (0.70 vs 0.50). The example v2 drills now in
`drills_v2/` are prototypes forked from KamiBench-era v1 data, built to exercise the rules and scorers; the real v2
bank is built from 4.7-era Season 1 runs after close, so some v1-to-v2 rank movement will be source era, not capability, and should be reported as such.

**Tries disagree unevenly.** Drills where the two tries disagree range from 3 to 16 per model: the per-item
variance needed to decide whether a third or fourth try pays (section 5).

**Pairwise tests reject almost nothing.** Holm over 55 pairs leaves one significant pair. A "multiple comparisons
with the best" set, which held 8–35 plausible winners even on large leaderboards, is the honest pairwise claim at
this n ([Leaderboard Lottery, ICML 2026](https://icml.cc/virtual/2026/80012)).

## 4. Drill families for v2

### 4.1 Snapshot forks (fixes gaps)

*The drills in `drills_v2/` are prototypes from KamiBench-era v1 data. The bank this section describes is forked
from Season 1 after close (2026-10-25 20:41 UTC).*

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
classical statistics, with one caution: at n = 10 models the standard error of a correlation is about 0.33, so a
point-biserial ≥ 0.2 threshold mostly selects noise. The registered rule (`select_items`): write a bank of 80–100
candidates; drop low-information items (model pass rate ≥ 0.95 or ≤ 0.05) and reversed items (sent to audit); of the
rest, administer the 40–50 with the highest *internal* discrimination (corrected point-biserial against the rest of
the suite, from drill responses only). Correlation of an item with live outcomes is reported as exploratory and never
used for selection, since that would tune the bank to Season 1's ranks. Held-out membership is a stable hash of the
drill id (`is_heldout`: sha256 of category/id, one third in expectation per category), fixed before any selection, so
retiring or adding items never moves another item across the split. Re-check each season and apply changes only
prospectively.

## 5. Scoring

- **pass@1 primary, pass^2 reported.** The decision score in the forecast (section 6) is pass@1. A quest fails if any step fails, so under constant per-step accuracy p an
  n-step task succeeds near p^n; METR's 80% horizons are 4–6× shorter than its 50% horizons
  ([METR](https://arxiv.org/html/2503.14499)), and τ-bench's agent fell from pass^1 ≈ 61% to pass^8 under 25%
  ([τ-bench](https://arxiv.org/pdf/2406.12045)). pass^k is the long-horizon quantity; pass@k measures discovery.
- **K from try-disagreement.** K = 2 captures about a third of recoverable resampling variance, K = 4 about half;
  stop raising K once E[σ²]/K is small beside between-item variance
  ([Miller](https://arxiv.org/html/2411.00640v1)). Registered rule (`choose_k`): from a K = 2 pilot, take the
  smallest K in 2–4 with mean within-item variance / K ≤ 0.1 × between-item variance; K = 4 if none qualifies.
- **Cluster-aware intervals.** Clustered standard errors can be 3× naive (same paper). 30 of v1's 47 drills come
  from one KamiBench run, and v2 forks share snapshots and mistake families. Bootstrap over snapshot families and
  report the cluster count.
- **Gap coding, pre-registered** ([v2-gap-coding.md](v2-gap-coding.md)). A scorable action is a state-changing
  call, not a read. A gap before any scorable action is "not administered"; a gap after one is scored. The score
  with not-administered excluded and the score with it counted as 0 are co-primary, with per-model gap rate beside.
- **Tiers, not ranks**, with cost per model and cost per state-changing action (`cost_per_action`) beside the score.

## 6. Validation design for Season 2

### 6.1 Registered forecast (text to register)

> 1. **Models and tiers.** The forecast covers the N models drilled and run live in Season 2. Top and bottom each
>    hold floor(0.3 × N + 0.5) models (halves round up: (3, 4, 3) at N = 10, (5, 5, 5) at N = 15) and the middle the
>    rest (`default_tier_sizes`).
> 2. **Decision score.** pass@1 per decision item (mean over tries, K per `choose_k`), averaged over the selected
>    decision bank. A not-administered item (gap coding) is left out of that model's mean. pass^2 and the score-0 gap
>    bound are reported, not forecast.
> 3. **Cadence score.** For each model, the mean of two z-scores taken across the N models (`cadence_score`):
>    z(−median schedule loss) over the schedule drills, loss = |ln(chosen / oracle)| with no tolerance band and an
>    invalid or missing wake counted as ln(1440); and z(min(pooled budget ratio, 1)). A model's **pooled budget ratio**
>    is its *matched* actions (recorded, non-revert state changes it reproduced: `matched_actions` from
>    `cadence.run_budget_pair`) summed over the free arms of the budget drills, divided by the same sum over the
>    forced-budget arms (`pooled_ratio`, equal to `cadence.pooled_budget_ratio`); a zero denominator counts as 0, so a
>    model that matches nothing scores lowest.
> 4. **Composite.** Equal-weight z-sum: z(decision) + z(cadence) (`composite`, form "zsum").
> 5. **Tier probabilities.** `forecast(decision_items, schedule_losses, budget_ratios, tier_sizes, n=10000, seed=0,
>    clusters)`, with decision_items {model: {item: pass@1, or None if not administered}}, schedule_losses
>    {model: {drill: loss}}, budget_ratios {model: {drill: (free-arm matched actions, budget-arm matched actions)}}, tier_sizes from
>    step 1 and clusters {item or drill: snapshot family}. P(top), P(middle), P(bottom) per model are the shares of
>    10,000 replicates (seed 0) in which the model lands in each tier. Each replicate is a stratified cluster bootstrap:
>    within each of three strata (decision items, schedule drills, budget drills) it draws, with replacement, as many
>    snapshot families as the stratum holds; a stratum with a single family draws its items instead. A model's
>    decision mean skips items it was not administered, and is its mean over all administered items if the draw holds
>    none of them. The decision score, the schedule medians, the pooled budget ratios (over the drawn budget drills),
>    the composite and the ranking are recomputed each replicate; ties are broken at random.
>    (`forecast` also accepts each model's pooled budget ratio as a number, held fixed across replicates; that form is
>    not the registered one.)
> 6. **Outcome.** Each model's live tier is its rank by mean week-end quests over its Season 2 runs, ties broken by
>    model id, cut at the step 1 sizes (`live_tiers`).
> 7. **Primary metric.** Mean ranked probability score over models (`rps`, `rps_skill`). The primary claim is RPS skill
>    1 − RPS / RPS_baseline > 0 against the **calibrated Season 1 baseline** (`season_baseline_probs`, n = 10,000,
>    seed 0): over the models that ran live in both seasons, resample each model's Season 1 runs with replacement, rank
>    the resampled means (ties at random), cut at `default_tier_sizes` of that model count, and take each model's tier
>    shares. Models new in Season 2 get uniform probabilities. Secondary: skill against the **certain Season 1
>    forecast** (the same models' Season 1 `live_tiers` at probability 1, `certain_probs`; new models uniform) and
>    against **uniform**. Reported as a control: the **hedged Season 1 copy** (0.7 on the Season 1 tier, 0.25 on an
>    adjacent tier, 0.05 on the far one; 0.15 / 0.7 / 0.15 for the middle tier; new models uniform), scored the same way.
> 8. **Beside it.** The accuracy-only forecast (decision score alone: `tier_probs_bootstrap` over the decision items
>    with the same clusters, n = 10,000, seed 0; graded as in steps 6–7), so Season 2 tests whether cadence adds.
>    Spearman ρ with a model-cluster bootstrap CI (`rho_cluster_ci`) is descriptive only. Pairwise claims use a
>    multiple-comparisons-with-the-best set (`mcb_set`), not Holm.
> 9. **Exploratory, not registered.** The product composite (decision × cadence), correlation of individual items
>    with live outcomes, and anything else not listed here.

**Why the z-sum, not the product.** The product matches the yield × persistence hypothesis (section 2), but it needs
both components on a ratio scale with a meaningful zero, which the schedule loss is not, and it lets one noisy
component swing the whole score. The z-sum is robust to one noisy component and needs no scale choice. Weights cannot
be fitted on ten points, so they are equal and declared. The product is reported as exploratory.

**Why a calibrated Season 1 baseline.** The bank is selected and tuned on Season 1. A bank that proxies Season 1's
ranks would beat uniform and show a ρ interval above zero whenever ranks persist across seasons. Against Season 1
ranks taken as certain, any hedge wins, drills or not. The calibrated baseline prices Season 1's own run noise the way
the drill forecast prices item noise, so beating it means the drill forecast placed models better than Season 1's
live results with their noise priced in. It does not price change between seasons; the hedged copy is the control for
that, and a drill forecast that beats the calibrated baseline but not the hedged copy has shown hedging, not drill
information.

### 6.2 Supporting analyses

- **Returning models.** Season 1 live cadence is a legitimate registered predictor if declared; it tests whether
  cadence is a stable trait across seasons.
- **Effective n.** The forecast is graded on 10 model means. The run-level model
  `live_run = b0 + u_model + e` is fitted only for the criterion's across-run reliability (ICC); it cannot identify a
  within-model drill effect, because all runs of a model share one drill score.
- **Held-out items.** One third of drills is held out from any tuning by stable hash (`is_heldout`); ρ is also
  reported on the held-out subset.
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
| 5 | After close: internal item analysis of v1 (drill responses only); retire the 13 low-information drills; audit the 6 reversed and 2 universally failed; item-vs-live correlation as exploratory only | Scoring | S | Discrimination selection without IRT or live leakage |
| 6 | 80–100 drill bank from the 27 Season 1 runs, weighted by live action frequency, including top-tier mistakes and API-habit classes; administer the 40–50 chosen by `select_items` | Bank | L | Reliability; separates the top tier |
| 7 | Cadence family: schedule drill and budget-use drill | New family | M | The axis v1 cannot see |
| 8 | Memory family: don't-repeat, stale-note (with "dated newer"), notes-before-first-call | New family | M | Revision is the weakest competency |
| 9 | Polluted-context variant of 10–15 existing drills | New family | S | Measured decay mechanism |
| 10 | pass@1 primary, pass^2 secondary; K from try-disagreement; stratified bootstrap over snapshot families; tiers not ranks | Scoring | S | Compounding reliability; clustered SEs |
| 11 | Register one fixed composite (decision + cadence) and the accuracy-only forecast beside it, by timestamped commit | Forecast | S | Tests whether cadence adds |
| 12 | RPS skill vs the calibrated Season 1 baseline (primary), the certain Season 1 forecast and uniform, hedged Season 1 copy as control; ρ descriptive with cluster CI; MCB set for pairwise | Validation | S | Proper, registrable, graded at close |
| 13 | Criterion ICC from the run-level model; hash-based held-out third; report held-out ρ | Validation | S | Noise ceiling; holdout norm |
| 14 | After close: classify every session end (completed / slept with pending work / gave up / errored); mine requested vs clamped wake intervals; test whether day-1 loop establishment predicts the week | Mine S1 | M | METR-style labelling; Ord's hazard |
| 15 | After close: recode v1 attempts by gap type against live rank; mine mistake families with a windowed repeat definition; diff notes against snapshots for stale beliefs | Mine S1 | M | Tests gap ignorability; seeds drills |
| 16 | Chained drills on an anvil fork of Yominet, scored by step of first failure | New family, gaps | L | After 1–15; subject to the chain operator's approval |
| 17 | A notes-reset (stateless) arm for one or two models in Season 2 | Validation | L (cost) | Stateful minus stateless = "learned" ([PAST-Bench](https://arxiv.org/abs/2608.04003)) |

## 8. Open questions

- **Schedule oracle.** The game clock gives a lower bound on the useful wake interval, not a unique answer. Is
  |log ratio| against the earliest useful wake the right loss, or should there be a tolerance band?
- **Era.** Drop the KamiBench-era drills entirely (removes the confound) or keep a labelled subset (keeps v1
  comparability)?
- **Returning-model predictor.** A composite that includes Season 1 live cadence is partly live-derived. Is that
  still a drill forecast, or a third registered forecast?
- **Chained drills.** Worth the anvil-fork cost before the cadence family has been validated once?
