# v2 memory drills

Draft for Season 2 (see [v2-proposal §4.3](v2-proposal.md)). Season 1 is graded on the v1 suite only: nothing here
changes `drills/`, `kami_drills/task.py` or the suite hash. Specs live in `drills_v2/memory/`, which the v1 loader
(`drills/*.json`) never reads; `tests/test_memory.py` checks that.

## Format

Each spec is one small JSON file that points at a public v1 drill (`source_drill`) and a cut point (`base_len`, how many
prefix messages are kept). `kami_drills.memory.materialise(spec, arm)` builds a v1-shaped drill from it, so v1's replay
engine, tool surface and rule language run unchanged. Notes are shown as a `workspace_read` of `notes/lessons.md`.

| kind | arms | pass |
|---|---|---|
| `dont_repeat` | a: failure kept in context as transcript; b: failure removed, the lesson as a note; c: failure removed, nothing | the first state-changing call does not repeat the failure (same tool, same normalised error class) |
| `stale_note` | `plain`, `dated_newer` (note timestamped later than anything in the state) | the source drill's v1 rule holds, i.e. the action follows the world |
| `notes_first` | one | a `workspace_read` comes before the first state-changing call; the needed fact is only in the note |

Examples (12): 5 don't-repeat from `repeat-*`, 4 stale-note from `stale-*`, 3 notes-first from `repeat-*` and `stale-*`.
Every spec records its source drill id, and `validate` checks the failure against the source transcript.

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
- Few examples (12) and one failure per drill, so deltas are indicative; the notes are short and may leak the answer more or less than the transcript does.
- `memory.memory_v2` (the Inspect task) is wired to v1's solver but has not been run end to end; only the pure scorers and sample building are tested. No model has been evaluated on these drills.
