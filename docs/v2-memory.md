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
| `dont_repeat` | a: failure kept in context as transcript; b: failure removed, the lesson as a note; c: failure removed, nothing | the source drill's v1 rule holds (the required action is taken) AND the first state-changing call does not repeat the failure (same tool, same normalised error class). Waiting passes only where the source rule accepts a long enough `set_next_wake` (see the table below) |
| `stale_note` | `plain`, `dated_newer` (note stamped one day after the newest timestamp in the state) | the source drill's v1 rule holds, i.e. the action follows the world (`sn-levelup-pending` also requires a real action) |
| `notes_first` | one | `workspace_read` of `notes/lessons.md` itself comes before the first state-changing call (reading `reference/` does not count), AND the source drill's v1 rule holds, i.e. the action uses the fact, AND a state-changing call other than waiting or ending the session is made; the needed fact is only in the note |

State-changing means `kami_drills.cadence.STATE_CHANGING` (listed transaction tools); unlisted names count as reads.

Examples (11): 4 don't-repeat from `repeat-*`, 4 stale-note from `stale-*`, 3 notes-first from `repeat-*` and `stale-*`.
Every spec records its source drill id, and `validate` checks the failure against the source transcript.

## Passive-play baseline

`memory.null_baseline()` scores seven passive policies on every drill and arm (`PASSIVE_POLICIES`): no calls; a bare
`set_next_wake`; `set_next_wake` with 5, 30 and 60 minutes; `end_session`; a read of the notes file then a 30-minute
wake. The replay's own flag rows are applied (a wake can set the pass flag). `tests/test_memory.py` and
`tests/test_e2e_mock.py` (through Inspect with `mockllm`) assert the table below.

Not every drill should fail passive play. The cooldown drills test "don't repeat the failed action", and their v1 rule
accepts a long enough wake because waiting is the correct way to avoid the failure. There, passing by waiting is right,
and the arms still differ in whether a model repeats the failed call first. Everywhere else the scorer needs the
action.

| drill | correct play | passive play that passes | why |
|---|---|---|---|
| dr-cooldown-feed | wake 5+ min (do not feed/start 20375 now) | wake 5/30/60, read-then-wake | waiting is the answer; a bare wake, end_session, no calls fail |
| dr-stop-cooldown | wait, then harvest_stop 17194, or wake 4+ min | wake 5/30/60, read-then-wake | waiting is the answer; end_session alone and no calls fail |
| dr-listing-buy-underflow | listing_buy with a valid amount | none | needs the purchase (flag `bought`) |
| dr-stop-starving | buy food, feed 703, then harvest_stop | none | needs `fed` and `stopped` |
| sn-cooldown-over | wait out the cooldown, then harvest_start | wake 5/30/60, read-then-wake | the stale note says the cooldown is over; the world says wait, and a wake satisfies the source rule (flag `cooled`) |
| sn-fed-full-hp | follow the world, not the note | none | needs the corrective action |
| sn-levelup-pending | upgrade_skill (do not level_up again) | none | a replayed wake would set the pass flag, so the spec sets `needs_action`: a real state-changing call is required |
| sn-quest10-open | accept_quest 11 | none | needs the quest call |
| nf-feed-first | read notes, then feed | none | needs read and act |
| nf-harvest-node | read notes, travel, harvest_stop | none | needs read and act |
| nf-wait-then-start | read notes, wait, harvest_start | none | the v1 rule alone would accept a wake; `notes_first` now requires a non-wait state-changing call |

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
