# kami-drills v2: research digest

**Literature digest supporting the v2 proposal. Not results.**

Published evidence behind [the v2 proposal](v2-proposal.md), by theme. Each theme separates what sources show
(evidence) from what we conclude (inference). Figures from a secondary write-up rather than the primary paper are
marked *secondary*; figures not checked against a full text are marked *unverified*. Nothing here is a result from a
live season.

## 1. Do cheap evals predict live, long-horizon agents?

**Evidence.** The strongest published case that short tasks track something stable is METR's time-horizon work:
across 170 tasks the 50% horizon doubles about every 207 days (95% CI 166–240), and the trend reproduces on SWE-bench
Verified with a different doubling time ([METR](https://arxiv.org/html/2503.14499)). The same paper cautions that
its tasks are "self-contained and well-specified" and that its extrapolation holds only "if these results generalize
to real-world software tasks". METR's randomised trial of 16 experienced open-source developers on 246 issues found
they took 19% longer with AI while forecasting a 24% speed-up
([METR RCT](https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/)); the horizon line and the
RCT are presented as complementary, not jointly validated.

Two numbers matter for drill design. First, 80% horizons are 4–6× shorter than 50% horizons, and under a
constant-hazard reading long-task success is roughly per-step success raised to the number of steps
([Ord](https://www.forethought.org/research/is-there-a-half-life-for-the-success-rates-of-ai-agents)). Second,
Sinha et al. derive the horizon H_s(p) = ln(s)/ln(p) under constant step accuracy p, find "sharp growth in horizon
length beyond 80% single-step accuracy", and show that a model's own earlier errors in context degrade its later
accuracy ("self-conditioning"), that scaling makes this worse, and that thinking models stay stable
([Sinha et al.](https://arxiv.org/html/2509.09677v3)).

Long-horizon benchmarks agree on where failures come from. In Vending-Bench "each sub-task is very simple" yet
performance deteriorates through forgotten orders, misread delivery schedules and "meltdown loops"; failures do not
track context fullness (Pearson 0.167), and "all models exhibit very high variance across their five runs"
([Vending-Bench](https://arxiv.org/html/2502.15840)). Game Arena's chess results show models "roughly even early",
weaker ones drifting in the middlegame and endgame ([Game Arena](https://arxiv.org/html/2609.31473v1)). Project
Vend, the one real deployment with a published post-mortem, surfaced failures the simulated benchmark had not
flagged, including a model that "did not reliably learn from mistakes"
([Project Vend](https://www.anthropic.com/research/project-vend-1)).

Benchmark-to-benchmark correlation is high but partly an artefact. Epoch finds a median pairwise Spearman of
0.73–0.79, with the caveat that "benchmarks spanning wide time ranges tend to have higher correlations with each
other" ([Epoch AI](https://epoch.ai/data-insights/benchmark-correlations)). The only paper that defines predictive
validity for agent rankings as in-sample to out-of-sample rank correlation reports −0.13 (n = 13) and 0.69 (n = 20)
on two tracks of one competition, proposes ρ = 0.85 as a falsification bar, and calls its own position "too thin to
confirm" ([Beyond Static Leaderboards](https://arxiv.org/html/2606.19704v1)); its saturated public leaderboard (8
unique scores across 20 teams) shows that ceilings destroy rank transfer. No source found reports a correlation
between an offline agent eval and a measured live outcome across models.

Reporting standards converge. Anthropic's error-bars paper asks for a standard error under every score, clustered
when items share a source, paired comparisons, and several samples per item
([Miller](https://arxiv.org/html/2411.00640v1)). The ABC checklist found 7 of 10 assessed agentic benchmarks
violated task validity and all 10 had reporting gaps ([ABC](https://arxiv.org/html/2507.02825v5)). "AI Agents That
Matter" notes that "many agent benchmarks have inadequate holdout sets, and sometimes none at all"
([Kapoor et al.](https://arxiv.org/abs/2407.01502)); a log-analysis paper finds τ-bench Airline pass^5
"under-elicited by nearly 50%" ([Log analysis](https://arxiv.org/abs/2605.08545)).

**Inference.** Rank on short tasks transfers to long tasks only when the short tasks are scored for reliability,
because long-horizon success compounds per-step success: pass@k measures discovery, pass^k measures what compounds.
Drills replayed from clean snapshots understate live degradation, since live contexts carry the agent's own earlier
errors. A credible predictive-validity claim needs pre-registration, a held-out item set, a correlation with an
interval, multi-run variance on both sides, cost, and a declared falsification bar; 0.85 is a proposal no agent
benchmark has met.

## 2. Measurement science for a small-n suite

**Power.** Bonett and Wright give the Fisher-z standard error for Spearman's ρ as √((1 + ρ²/2)/(n − 3)) and a
sample-size approximation that follows from it
([Bonett & Wright](https://www.cambridge.org/core/journals/psychometrika/article/sample-size-requirements-for-estimating-pearson-kendall-and-spearman-correlations/840CA9F25E521DBD1C389D48638CE2F1)).
Applying it, the smallest ρ detectable at 80% power is about 0.84 at n = 10, 0.63 at n = 20 and 0.55 at n = 27;
below n = 25 exact permutation tests are preferable
([metricgate](https://metricgate.com/docs/power-spearman-rank-correlation/)). Runs of one model are not independent
units: in Business Arena a random-effects model attributes 64.8% of score variance to model identity and 35.2% to
repeated runs of the same model ([Business Arena](https://arxiv.org/pdf/2608.08621)). Leaderboard Lottery finds
paired-bootstrap "multiple comparisons with the best" sets held 8–35 plausible winners even on large leaderboards
([Leaderboard Lottery](https://icml.cc/virtual/2026/80012)).

**Attenuation.** The observable correlation between predictor and criterion is bounded by √(rel_x · rel_y)
([attenuation](https://sk.sagepub.com/ency/edvol/embed/survey/chpt/attenuation)), so the criterion's own
reliability caps what any drill suite can show. Spearman–Brown gives the gain from adding parallel items: doubling
lifts 0.6 to 0.75 and 0.7 to 0.82
([Spearman–Brown](https://sk.sagepub.com/ency/edvol/embed/the-sage-encyclopedia-of-research-design-2e/chpt/spearman-brown-prophecy-formula)).

**Reliability and pass^k.** τ-bench defines pass^k as the probability that all k trials succeed, averaged over
tasks; its GPT-4o agent falls from pass^1 ≈ 61% to pass^8 under 25% on retail
([τ-bench](https://arxiv.org/pdf/2406.12045)). STATE-Bench reports pass^5 beside mean completion and cost per task
([STATE-Bench](https://opensource.microsoft.com/blog/2026/05/19/introducing-state-bench-a-benchmark-for-ai-agent-memory/)).
Miller's decomposition Var(μ̂) = (Var(x) + E[σ²])/n implies K resamples per item cut variance by a third at K = 2 and
half at K = 4, with two thirds as the limit, so K should rise until E[σ²]/K is small beside between-item variance;
clustered standard errors were up to 3.05× naive ([Miller](https://arxiv.org/html/2411.00640v1)).

**Item selection without IRT.** tinyBenchmarks fit a 2PL model on 428 models
([tinyBenchmarks](https://arxiv.org/html/2402.14992v2)); Fluid Benchmarking on 102
([Fluid Benchmarking](https://arxiv.org/html/2509.11106v1)); metabench on more than 5,000
([metabench](https://arxiv.org/html/2407.12844v2)). Rasch item calibrations are stable to ±1 logit with 16–36
respondents and ±0.5 with 64–144 ([Linacre](https://www.rasch.org/rmt/rmt74m.htm)). Madaan et al. find that methods from the human-testing literature "struggle to meaningfully reduce variance" on LLM
evals ([Madaan et al.](https://arxiv.org/abs/2406.10229)). What transfers is Fluid Benchmarking's observation that
low-discrimination items are disproportionately mislabelled (0.01 versus 0.75 per 100-item session, adaptive versus
random), implemented with classical statistics: drop items with corrected point-biserial below 0.2
([item-total correlation](https://en.wikipedia.org/wiki/Item-total_correlation)), keep mid-difficulty items, and
exclude the models under test from any fitting set.

**Missing data.** IRT distinguishes "not administered" (ignorable when independent of ability) from "omitted";
ignoring missing responses always raises estimated ability relative to scoring them wrong, and not-reached items bias
estimates when speed correlates with ability
([Frontiers 2018](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2018.00964/pdf);
[NCES](https://nces.ed.gov/nationsreportcard/tdw/analysis/2000_2001/scaling_missing.aspx)).

**Pre-registration and proper scoring.** Registered Reports require that "any analysis not specified at Stage 1
must be reported separately and labelled exploratory"
([Registered Reports](https://www.cambridge.org/core/open-research/registered-reports)). For ordered tiers the
ranked probability score is strictly proper and penalises distance between forecast and realised category
([scoringrules](https://scoringrules.readthedocs.io/en/latest/_sources/theory.md)). The construct-validity audit of
445 benchmarks found 16% used uncertainty estimates or statistical tests and 31% compared to a more realistic
setting ([Bean et al.](https://arxiv.org/html/2511.04703v1)). Kane's argument-based validity decomposes a test into
scoring, generalisation, extrapolation and decision inferences, each needing its own backing
([Kane](https://www.ets.org/research/policy_research_reports/publications/article/2013/jrpe.html)).

**Inference.** At ten models the honest target is an interval that excludes zero and a correct top tier, not a
point ρ. Replay gaps behave like not-reached items whose rate depends on the agent, so they are not ignorable and
need a pre-registered coding rule with a score-0 sensitivity bound; the analogy is ours, since no LLM-eval paper
treats environment-side gaps this way. One pre-registered composite, RPS as primary metric and ρ as descriptive
follows the Registered Reports norm.

## 3. Continual learning and memory benchmarks

**How "learned" is operationalised.** Six definitions recur. (1) Gain over a reset control: Snorkel's "stateful
reward minus stateless reward" ([Snorkel](https://snorkel.ai/blog/continual-learning-ai-agents-explained/)) and
PAST-Bench's retained experience "switched on and off under matched conditions"
([PAST-Bench](https://arxiv.org/abs/2608.04003)). (2) A cumulative-success learning curve over an ordered stream
([Evo-Memory](https://arxiv.org/html/2511.20857v1)). (3) Continual-learning matrix metrics: SWE-Bench-CL defines
forgetting, forward and backward transfer, plasticity and stability over 273 tasks
([SWE-Bench-CL](https://arxiv.org/html/2507.00014v1)). (4) Sample efficiency: AlfWorld steps fell from 22.6 to 11.5
under Evo-Memory's ReMem; KamiBench's planned signal is unit cost against experience ([KamiBench](https://kamibench.ai/experiments/sustainability)). (5) Belief revision: MemoryAgentBench's
selective-forgetting competency ([MemoryAgentBench](https://arxiv.org/html/2507.05257v4)); Memora's FAMA, which
subtracts a forgetting penalty from memory-presence accuracy ([Memora](https://arxiv.org/pdf/2604.20006)).
(6) Pathway evidence: did the gain flow through save, retrieve and update (PAST-Bench).

**Findings.** Experience helps most on repetitive, structured tasks: Evo-Memory's gains correlate with within-dataset
task similarity (Pearson 0.717 and 0.563), and storing unfiltered failed trajectories lowers baselines. LifelongAgentBench finds replay effects "diminish in sequences exceeding six steps"
([LifelongAgentBench](https://arxiv.org/html/2505.11942v3)). EvoMemBench finds memory helps most under tight context
budgets and that at 128K several methods fall below no memory
([EvoMemBench](https://www.alphaxiv.org/abs/2605.18421.md)).
ApprenticeBench, a 100-invoice sequential job with thinning supervisor feedback, reports an ablation of 11 bills
passed with the handbook only, 31 with history, 35 with feedback and 43 with both, and that the top agent wrote about
192,000 words of notes against 2,800 for the most prolific human while slowing as tasks accumulated
([36Kr](https://eu.36kr.com/en/p/3990925961313283), *secondary*; no paper exists).

**Failure modes.** Revision is the weakest competency wherever it is separated from retention: MemoryAgentBench's
multi-hop fact consolidation tops out at 28%; STALE's best model resolves 55.2% of stale-state conflicts and shows a
"gap between retrieving updated evidence and acting on it" ([STALE](https://arxiv.org/abs/2605.06527)); in the
Memory Trust Gap suite models used a stale note over an authoritative tool 0.92–1.00 of the time, and larger models
"collapse most once a stale note looks current" ([Memory Trust Gap](https://arxiv.org/abs/2609.01852)); under
bounded self-maintained notes, accuracy on a changed fact fell from 68% to 28% as history grew
([Supersede](https://arxiv.org/html/2606.27472v1)). Pollution: committing an unverified
claim to durable memory raised downstream failure from 45.0% to 71.9% in PASB
([PASB](https://arxiv.org/html/2607.10526v3)). Self-feedback alone "induces recursive drift"
([SkillLearnBench](https://arxiv.org/pdf/2604.20087)). KamiBench's case studies from this game world show a rule
that held sixty sessions then broke when an expensive call fired before the notes were read, and a belief overturned
because it had been labelled a hypothesis
([What agents remember](https://kamibench.ai/blog/what-agents-remember)).

**Offline designs with precedent.** Taste-Bench freezes 502 decision forks from coding and research runs, counts an
answer only if correct in both option orders, and reports that a distilled judge's accuracy rose from 30.0% to 47.9%
and held-out agent success from 14.6% to 33.7%
([The Decoder](https://the-decoder.com/ai-agents-have-bad-taste-in-long-term-decisions-and-a-new-benchmark-proves-it/),
*secondary*; no arXiv id located). It is the only source found where an offline judgment score transfers to
held-out task success. Other precedents: matched experience-on/off arms (PAST-Bench, Snorkel); stale-note conflicts
(STALE, Memory Trust Gap, Supersede); guidance validated by downstream effect rather than rubric
([TORSI](https://arxiv.org/abs/2610.20665)).

**Inference.** The field's cleanest definition of learning is stateful minus stateless on the same sequence; an
early-versus-late comparison within one run confounds learning with environment drift unless there is a reset arm.
No published benchmark uses a repeat-mistake rate as a headline metric; the nearest are SWE-Bench-CL's forgetting
term and Taste-Bench's "detour" forks.

## 4. Initiative, pacing and persistence

**Early stopping and inactivity.** METR hand-labelled 31 failed GPT-4 1106 runs and 32 failed o1 runs: the first
mostly repeated failed actions (12 of 31), the second mostly quit, with "premature task abandonment" its largest
class (16 of 32) ([METR, Table 2](https://arxiv.org/html/2503.14499v4)). Vending-Bench reports that "tool use
declined over time for all models, with a notable drop after about 120 days"; one model "sometimes just waited for
the next day without restocking or emailing suppliers"; removing the daily fee did not raise sales; and models
declared the business dead while holding about half their cash
([Vending-Bench](https://arxiv.org/html/2502.15840)). The diagnosed root cause is that models assume a delivery has
arrived once its date passes, when "a human could recover simply by checking later". Vending-Bench 2's top models
"keep a consistent rate of tool use all year", though no idle-time distributions are published
([Vending-Bench 2](https://andonlabs.com/evals/vending-bench-2)).

**Budgeted versus free episodes.** UltraHorizon gives the cleanest instrument: removing the mandatory step cap cut
tool calls three- to five-fold for most models (DeepSeek-V3 92.85 to 21.94, Gemini-2.5-Pro 85.33 to 22.81), and one
model lost 6.44 points, attributed to "overconfidence or giving up early"
([UltraHorizon](https://arxiv.org/html/2509.21766)). The opposite failure exists too: a False Continue
Rate on infeasible tasks reaches 73.9% ([arXiv 2605.28532](https://arxiv.org/abs/2605.28532)).

**Hazard models.** Ord reads METR's data as a constant failure rate per human-minute of task, making the 50%
horizon a half-life ([Ord](https://arxiv.org/pdf/2505.05115)); a Weibull refit finds every model's shape parameter
near 0.6 and humans near 0.37, so hazard declines within a task and agents that survive the early part are
disproportionately likely to continue
([Ord, declining hazard](https://tobyord.com/writing/hazard-rates-for-ai-agents-decline)). Neither model includes
the agent's choice of session count.

**Progress metrics.** AgentBoard's progress rate scores partial completion because success rate "does not
differentiate between near-complete tasks and minimal task execution"
([AgentBoard](https://arxiv.org/pdf/2401.13178)). "Learning When to Act" is the one paper that treats the
wait interval as the decision, with an interval-aware reward; its LLM experiment had 15 episodes with both arms at
100% ([Learning When to Act](https://arxiv.org/abs/2603.22384)), so scheduling quality is not yet shown measurable.

**Offline and replay limits.** Importance-sampling off-policy evaluation suffers the "curse of horizon": variance
grows with trajectory length ([Liu et al.](https://arxiv.org/abs/1810.12429)); the LLM-agent alternative learns a
world model, reintroducing simulator-fidelity risk ([ADWM](https://arxiv.org/abs/2606.05558)). Fidelity is
measurable: replacing τ-bench's simulated user with 451
humans dropped agent success from up to 77.8% to 63.6%, simulators "quietly change strategy and cooperate" after
agent errors, and 70.6% of reward-0 runs were judged successful by humans
([Mind the Sim2Real Gap](https://arxiv.org/html/2603.11245v1)). Project Vend's second phase concludes that
simulations "only get you so far" ([Project Vend 2](https://www.anthropic.com/research/project-vend-2)).

**Inference.** Single-decision drills from a frozen state are the safest offline design because they need no
simulator; the price is that cadence must be measured by purpose-built items rather than inferred. Neither a schedule
decision scored against a clock-derived oracle nor a budgeted-versus-free episode is published; UltraHorizon's
comparison is the nearest precedent.

## 5. Efficiency vs progression

**Evidence.** The cost-aware evaluation literature treats cost as an axis, not a divisor. "AI Agents That Matter"
finds that a "narrow focus on accuracy without attention to other metrics" makes state-of-the-art agents "needlessly
complex and costly" and asks for cost and accuracy to be jointly optimised
([Kapoor et al.](https://arxiv.org/abs/2407.01502)). HAL ran 21,730 rollouts across 9 models and 9 benchmarks,
plots cost beside accuracy, and finds higher reasoning effort reduced accuracy in the majority of runs
([HAL](https://arxiv.org/abs/2510.11977v1)). OmniStepBench recommends reporting accuracy–cost trade-offs "rather
than a single aggregate score" ([OmniStepBench](https://arxiv.org/abs/2609.06059)). STATE-Bench reports completion,
pass^5, cost per task and a UX judge as four separate columns. ApprenticeBench reports dollars per task beside
cumulative pass rate: about $18.23 per invoice for the top agent against $7.21 for the best human, at 72% versus 51%
passed ([36Kr](https://eu.36kr.com/en/p/3990925961313283), *secondary*). KamiBench's
planned sustainability score nets inference cost and transaction fees against in-world earnings and reads learning as
"brute-force search appears as cost, while learned shortcuts appear as margin"
([KamiBench](https://kamibench.ai/experiments/sustainability)), noting that single-run survival near an absorbing
barrier rewards risk appetite as well as skill.

Three findings show why one ratio misleads. Vending-Bench's net worth includes unspent starting cash: the
lowest-activity model (Claude 3.5 Haiku) sold 23 units to the leader's 1,560, a 68× gap, while its net worth was
about a sixth of the leader's; its sales stopped on simulated day 8 against day 102
([Vending-Bench](https://arxiv.org/html/2502.15840)). The outcome metric hid most of the activity gap. ABC's
empty-response agent scores 38% on τ-bench at near-zero cost ([ABC](https://arxiv.org/html/2507.02825v5)), the
limit case of cost per task rewarding inaction. And effort is not monotonic in success: E-Bench reports a positive correlation between tool calls and success in one domain and a
negative one in another ([E-Bench](https://arxiv.org/pdf/2607.23722), r values *unverified*), and a coding benchmark
finds most agents issue more calls on failed runs ([TensorBench](https://arxiv.org/pdf/2606.05570), *secondary*).

**Inference.** Cost per completed task is minimised by an agent that completes one easy task and stops; progress per
unit time is maximised by an agent that burns tokens in loops. Neither alone ranks agents the way a week-long season
does. A forecast should carry both axes, progression (state-changing actions and quests over the season) and
efficiency (cost per state-changing action), with cost beside each model's score rather than folded into it, as HAL,
STATE-Bench and ApprenticeBench do. Cadence scoring must then separate two kinds of low activity. Waiting
is efficient when nothing is actionable, which Vending-Bench 2's "expected to sleep at night" framing and the
interval-aware reward in Learning When to Act both recognise; it is stalling when pending work exists, which is
Vending-Bench's "waited for the next day without restocking" and METR's premature abandonment. A game clock makes the
two separable offline: cooldowns, harvest timers and quest resets give an earliest useful wake, so a chosen interval
can be scored as |log(chosen / oracle)|, penalising a day's sleep before an hour-long cooldown and a wake every minute
alike. A budgeted-versus-free episode measures the other side: whether an agent stops with work still pending when
free to continue. Both are inferences; no published benchmark scores a chosen wait interval against an oracle.

## 6. Implications for kami-drills v2

- Target an interval that excludes zero and a correct top tier, with the criterion's reliability ceiling published
  beside it, not ρ ≥ 0.85 ([proposal §1](v2-proposal.md#1-what-ten-models-can-show)).
- Treat cadence as a second measured axis, with schedule and budget-use drills scored so that efficient waiting and
  stalling separate ([§2](v2-proposal.md#2-what-a-frozen-state-drill-cannot-see), [§4.2](v2-proposal.md#42-cadence)).
- Report progression and cost per state-changing action as separate columns, never cost per completed task alone
  ([§5](v2-proposal.md#5-scoring)).
- Score pass@1 primary and pass^2 secondary; set K from try-disagreement; bootstrap over snapshot families and report
  the cluster count ([§5](v2-proposal.md#5-scoring)).
- Pre-register gap coding with a score-0 sensitivity bound, and close gaps at source first
  ([§3](v2-proposal.md#3-what-v1s-own-numbers-say), [§4.1](v2-proposal.md#41-snapshot-forks-fixes-gaps)).
- Select items with classical statistics from a bank larger than the administered set; audit low-discrimination items
  as probable grader errors ([§4.7](v2-proposal.md#47-bank-and-selection)).
- Build memory drills around revision, the weakest measured competency: don't-repeat, stale-note with a "dated newer"
  variant, notes-before-first-call; score note-writing by downstream effect ([§4.3](v2-proposal.md#43-memory)).
- Add polluted-context variants for self-conditioning; chained decisions for late drift only once cadence is
  validated ([§4.4](v2-proposal.md#44-polluted-context), [§4.5](v2-proposal.md#45-chained-decisions),
  [§8](v2-proposal.md#8-open-questions)).
- Register one fixed composite and the accuracy-only forecast beside it; RPS over tiers against uniform and
  prior-season baselines; ρ descriptive with a model-cluster CI; MCB sets for pairwise claims
  ([§6](v2-proposal.md#6-validation-design-for-season-2)).
- A stateless arm for one or two models is the only design under which "learned" means what the literature means
  ([§7](v2-proposal.md#7-plan), item 17).
