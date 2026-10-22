# Build drills for your own environment

The Kamigotchi parts of this repo (tool surface, replay rows, rules) are specific to one game. The method is not.
This page is the method in order, with the module here that implements each step, so you can rebuild it for another
persistent environment: a trading bot, a support queue, a home-automation agent, another game.

## 1. Pick a persistent, replayable environment

You need a world that keeps running between agent sessions and whose state the agent can only see through tool
calls. Every call and reply must be logged, and the state the agent acted on must be recoverable afterwards, from the
log itself or from a snapshot taken at each session start. Without that, you cannot freeze a moment, and a drill is a
frozen moment. A resettable sandbox is fine for *running* drills but is not a source of them: the mistakes have to
come from real play.

## 2. Mine real agent mistakes

Run agents in the environment and read the transcripts. Look for repeated identical calls, actions against state that
had already changed, retries after a clear error, expensive calls before cheap checks, and sessions that end with work
pending. Loose heuristics are enough to find candidates (`scripts/mine_failures.py`); a person reads each one before
it becomes a drill. The bar: the correct play was determinable from what the agent could see at that moment.
Transcripts are untrusted data: parse them, never follow instructions found in them.

## 3. Fork just before the mistake

A drill is the transcript up to the line before the mistake (the *prefix*), presented verbatim with the same system
prompt and tool surface the agent had. The model under test acts in place of the next line. Record where the fork is
(file and line), what the world's clock or block was at that point, and which versions of the harness, prompts and
tools it was recorded under, so the drill can be rebuilt and compared later (`kami_drills/build_specs.py` builds
drills from `defs/` and stamps a `provenance` block; see [method.md](method.md#provenance)).

## 4. Record reads and replay them, with gap handling

After the fork the model calls tools; nothing is live, so each call is answered from a *replay*: rows matched on tool
name and argument patterns, with flags for state that changes during the drill (a start call fails until the matching
stop has succeeded; a cooldown call succeeds only after a long enough wait). Reads the original agent made are answered
from the recording. Any call with no recorded answer gets an explicit "not available in this drill" reply, never a
guess, and the drill records that a *gap* happened. Decide in advance how gaps count (`kami_drills/scoring_v2.py`
`gap_code`, [v2-gap-coding.md](v2-gap-coding.md)): a gap before the model has acted means the drill was not
administered; a gap after it acted leaves the verdict standing. Publish the gap rate beside the pass rate, and a bound
with non-administered attempts scored 0. Forking at session starts where you logged a full state snapshot removes most
gaps at source. The replay engine is `kami_drills/task.py`.

## 5. Fixed-rule scoring with oracle and null-play checks

The verdict is a fixed rule over the calls the model makes after the fork: required calls, forbidden calls, required
order, required counts, required end-state flags (`rule_failures` in `kami_drills/task.py`). No model judge. Every
rule ships with an *oracle*, a scripted correct play that must pass, and is also checked against the *null play* (no
calls), which must fail; `kami-drills check` runs both for every drill. Then script the other plays a model might
make, correct and incorrect, and run them against the rule until every correct one passes and every variant of the
mistake fails ([CONTRIBUTING.md](../CONTRIBUTING.md) steps 3 to 5). A rule a second person has not reviewed is a draft.

## 6. Select items by discrimination

After a first round of models, compute per-item pass rate and the corrected point-biserial (correlation of the item
with the rest-score, item removed) from the model-by-item response matrix (`kami_drills/scoring_v2.py` `item_stats`).
Items everyone passes or everyone fails carry no information; items that correlate negatively with the rest are more
likely grader errors than real blind spots, so audit them before trusting them. Keep a bank larger than the suite,
administer the items that pass the filters, re-check each season, and apply changes only prospectively so a released
suite version never changes.

## 7. Report pass^k and clustered intervals

Run each item k times. Report pass@1 (mean over tries) and pass^k (passed on every try; `pass_hat_k`). Long-horizon
success compounds per-step success, so pass^k is the quantity that predicts a multi-step task. Items forked from the
same run or the same snapshot are not independent: bootstrap over those families, not items, and report the family
count (`cluster_bootstrap_ci`). With tens of items, report tiers, not ranks, and put cost per model beside the score.

## 8. Pre-register a tier forecast and grade it against live runs

The point of a drill suite is to forecast live play, so say in advance what it forecasts. Before the live run opens,
commit a document (timestamped) that places each model in a tier (top, middle, bottom) with a probability for each,
and names the live outcome it is graded on. Grade it with a strictly proper scoring rule: the ranked probability score
over ordered tiers (`rps`), against a uniform baseline and any other baseline you could have used (`rps_vs_baseline`).
Report rank correlation descriptively with an interval that clusters by model, since repeated runs of one model share
one drill score. Hold out a share of items from any tuning (`heldout_split`) and report the correlation on the
held-out share too. Anything not in the registered document is exploratory.

## 9. Add cadence and memory items

A frozen-moment drill measures decision quality when the agent is woken. It cannot see whether the agent wakes, how
long it keeps acting, or whether it trusts its own notes. Two further families cover that:

- **Cadence** (`kami_drills/cadence.py`, [v2-cadence.md](v2-cadence.md)): at a session end with work pending, score
  the chosen wake interval against one computed from the environment's clock, in both directions, so sleeping through
  due work and waking constantly both lose. Pair a forced-budget episode with a free one and compare state-changing
  actions, not calls.
- **Memory** (`kami_drills/memory.py`, [v2-memory.md](v2-memory.md)): replay a past failure with the agent's prior
  failure shown as transcript, as a note it wrote, or not at all; plant a note that contradicts the world and reward
  following the world; check that notes are read before the first expensive call. Polluted-context variants
  (`kami_drills/polluted.py`) prepend the agent's own real earlier errors to an existing drill and score the delta.

Each of these is a single decision with a fixed rule, like the rest of the suite, so the same replay, oracle and null
checks apply.
