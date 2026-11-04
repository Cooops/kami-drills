# Roadmap

v1.0 drills test one decision at a moment. Live play also asks for stamina (staying coherent across a long session and
many sessions) and memory (writing, trusting and repairing notes). These are the planned additions, in rough order.

## v1.1 — gap-free drills from snapshot runs

Status: the v1.1 specs and the mined bank v2.0-rc8 are built (README, v2 section). The Season 1 re-mine on the full
7-day logs comes after close. What is still open is listed under "deferred" below.

**At Season 1 close (2026-11-04 06:51 UTC), in order:**
- [ ] **Grade the registered S1 forecast** (H1/H3 on day-7 quests, as pre-registered) after all 27 runs complete.
  Done = grading published with the Palaestra Season 1 results.
- [ ] **Re-mine on the full 7-day logs**: re-sync the S1 logs, then `kami-drills mine ... --bank bank/v2.0-rcN` (a new
  version; never overwrite rc8). Done = the new bank passes `spec check` and an independent review.
- [ ] **Cross-benchmark check**: collect public agent-benchmark scores (e.g. Vending-Bench, τ-bench) for the 10 S1
  models; test which drill families track which benchmarks and what they add beyond overall model strength; pre-register
  it as an S2 hypothesis. Done = an analysis note and an S2 pre-registration entry.

[Palaestra Season 1](https://palaestra.run/research/season-1) logs a full state snapshot at every session start (account, roster, every kami, quest states,
inventory, gas, lens status) without the agent seeing it. Drills forked from those runs can answer any read truthfully,
which removes most replay gaps. New drills come from the mistakes those runs make, mined with the same pipeline as
v1.0 (see CONTRIBUTING.md). Low-information drills found by the item analysis are retired or replaced.

## v1.1 specs and the v2 scorer: deferred

- **Early-stop emulation.** `decision_point.early_stop` is unread by the spec agent, so v1's early stop (the overpaid
  stop in onboard-first-kami-vendor) is not emulated.
- **A pre-4.7 KamiBench adapter.** The harness-bound `repeat-pool-swap-retry` (bare `Reverted`) is not administered
  anywhere until one exists.
- **`use_item_batch count n`** becomes n feed intents, while v1 matched it once (quest19).

## Memory

- **Notes repair.** The agent receives its real notes from a past run with one belief made wrong, plus the game docs.
  It passes by finding the conflict and correcting the note, or by checking the docs before acting on it.
- **Docs lookup.** The answer is not in the history; it is in `reference/`. The drill scores whether the agent looks
  it up and acts on what it finds.
- **Carry-over.** A drill presents session N and later session N+k of the same run. The model writes the notes in the
  first part that it must rely on in the second.

## Stamina

- **Context load.** The same decision is presented early in a session and near the session token cap. The score is
  the change in pass rate, per model. (Trimming histories changed verdicts in v1.0, which is the effect this
  measures on purpose.)
- **Multi-session chains.** Three to five forks from one run, with time skipped between them, scored together.
  A model that is right once and then drifts is distinguished from one that stays right.

## Measurement

- Report pass^k (passes on every one of k tries) beside the mean, as τ-bench does.
- Grow toward a few hundred drills so per-category results become claims rather than descriptions.
- Re-record the tool surface and re-tag the suite whenever the stack pins move.

Proposals and PRs are welcome; see CONTRIBUTING.md.
