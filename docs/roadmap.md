# Roadmap

v1.0 drills test one decision at a moment. Live play also asks for stamina (staying coherent across a long session and
many sessions) and memory (writing, trusting and repairing notes). These are the planned additions, in rough order.

## v1.1 — gap-free drills from snapshot runs

[Palaestra Season 1](https://palaestra.run/research/season-1) logs a full state snapshot at every session start (account, roster, every kami, quest states,
inventory, gas, lens status) without the agent seeing it. Drills forked from those runs can answer any read truthfully,
which removes most replay gaps. New drills come from the mistakes those runs make, mined with the same pipeline as
v1.0 (see CONTRIBUTING.md). Low-information drills found by the item analysis are retired or replaced.

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
