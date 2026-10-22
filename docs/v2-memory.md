# v2 memory drills

Draft for Season 2 (see [v2-proposal §4.3](v2-proposal.md)). Season 1 is graded on the v1 suite only: nothing here
changes `drills/`, `kami_drills/task.py` or the suite hash. Specs live in `drills_v2/memory/`, which the v1 loader
(`drills/*.json`) never reads; `tests/test_memory.py` checks that.

**Prototype.** Every example drill here is a prototype built from KamiBench-era v1 data (public v1 drills). The real
bank comes from Season 1 runs after close; none of that data is used or committed here.

## Format

Each spec is one small JSON file that points at a public v1 drill (`source_drill`) and a cut point (`base_len`, how many
prefix messages are kept). `kami_drills.memory.materialise(spec, arm)` builds a v1-shaped drill from it, so v1's replay
engine, tool surface and rule language run unchanged. Notes are shown as a `workspace_read` of `notes/lessons.md`.

| kind | arms | pass |
|---|---|---|
| `dont_repeat` | a: failure kept in context as transcript; b: failure removed, the lesson as a note; c: failure removed, nothing | the source drill's v1 rule holds (the required action is taken) AND the first state-changing call does not repeat the failure (same tool, same normalised error class). Doing nothing fails |
| `stale_note` | `plain`, `dated_newer` (note stamped one day after the newest timestamp in the state) | the source drill's v1 rule holds, i.e. the action follows the world |
| `notes_first` | one | `workspace_read` of `notes/lessons.md` itself comes before the first state-changing call (reading `reference/` does not count), AND the source drill's v1 rule holds, i.e. the action uses the fact; the needed fact is only in the note |

State-changing means `kami_drills.cadence.STATE_CHANGING` (listed transaction tools); unlisted names count as reads.

Examples (11): 4 don't-repeat from `repeat-*`, 4 stale-note from `stale-*`, 3 notes-first from `repeat-*` and `stale-*`.
Every spec records its source drill id, and `validate` checks the failure against the source transcript.

## Null-policy baseline

`memory.null_baseline()` scores a model that makes no calls and one that only calls `set_next_wake` with no arguments,
on every drill and arm. Result: 0 of 11 drills (0 of 23 drill-arms, both null policies) pass, so a pass needs a real
action. `tests/test_memory.py` asserts it and `tests/test_e2e_mock.py` plays the null policy through Inspect on every
drill with `mockllm` (it fails each time; the oracle play passes each time).

## What each arm isolates

All three don't-repeat arms share the same world and replay rows; only what is shown differs. **c** is the baseline
(how often the model repeats unprompted). **b - c** is the effect of a self-written note alone. **a - c** is the effect
of seeing the raw failure. **a - b** (derived) is excerpt versus distilled lesson. `memory.arm_report` gives per-arm pass
rates and the deltas; positive means the arm made repeating less likely. The stale-note variants isolate how much a
newer date makes a wrong note more persuasive: compare `dated_newer` with `plain`.

## Grounding

- [STALE](https://arxiv.org/abs/2605.06527): best model resolves 55.2% of stale-state conflicts.
- [MemoryAgentBench](https://arxiv.org/html/2507.05257v4): multi-hop consolidation tops out at 28%.
- [Memory Trust Gap](https://arxiv.org/abs/2609.01852): models acted on a stale note over an authoritative tool 92-100% of the time, most when the note was dated newer.
- [KamiBench: what agents remember](https://kamibench.ai/blog/what-agents-remember): fork templates (notes read after the expensive call; "hypothesis" versus fact).
- [PASB](https://arxiv.org/html/2607.10526v3): committing an unverified fact to memory raised downstream failure by 27 points.

## Limitations

- Note-writing quality is out of scope. Notes are authored by hand, not by the model; a downstream-effect score for
  model-written notes (PASB-style) is future work.
- The note is injected as a read in the prefix, not found by the model, so arm b tests use, not retrieval. `notes_first` tests retrieval order only.
- "Repeat" needs the replay to return the same error class. A repeat the replay answers differently, or one hidden inside
  `act_sequence`, is not counted. The first state-changing call is the only one scored.
- Few examples (11) and one failure per drill, so deltas are indicative; the notes are short and may leak the answer more or less than the transcript does.
- `memory.memory_v2` (the Inspect task) runs end to end on `mockllm` with scripted plays (oracle passes, null fails); no real model has been evaluated on these drills.
- The first state-changing call is the repeat check; a repeat hidden inside `act_sequence` is not seen by it, but the v1 rule still has to hold.
