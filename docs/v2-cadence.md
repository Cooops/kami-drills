# v2 cadence drills

Draft, part of the [v2 proposal](v2-proposal.md) (sections 2 and 4.2). Cadence is a separate v2 format: v1 drills, the
v1 loader and the judge hash are untouched, and v1 never reads `drills_v2/`. Code: `kami_drills/cadence.py`. Tests:
`tests/test_cadence.py`. Offline only.

## Why cadence needs both sides

A cadence score has to tell **efficient waiting** (nothing is due, so a long sleep is right) from **stalling** (work is
pending, so a long sleep loses output). Scoring only "fewer calls" or "per-quest efficiency" rewards doing little: an
agent that sleeps all week has perfect efficiency on the quests it never starts. The schedule drill therefore scores the
wake time against a clock, in both directions, and the budget drill counts state-changing actions, not calls.

## Schedule drill

`drills_v2/cadence/schedule-*.json`. The agent is at a session end with pending work and must call `set_next_wake`
(`minutes_from_now`).

- **Oracle:** minutes until the kami is off cooldown and fully rested, from the state's clock fields:
  `max(cooldown_s, (hp_total - hp) / hp_rate_per_hr * 3600) / 60`. The file records it and the loader checks it.
- **Score:** raw loss `|ln(chosen / oracle)|`, always reported. Optional `tolerance_band` (a factor, default `null`):
  loss becomes `max(0, raw - ln(band))`. `pass_threshold` (ln units, 0.7 = a factor of 2) is applied to that loss.
- Always 5 min and always 24 h both fail every example (oracle 59 to 106 min; raw loss 2.5 and 2.6 or more).
- Each drill records `source_v1_id` and the fork line. Examples use only drills sourced from the public KamiBench
  dataset, no Season 1 data.

## Two kinds: `pending` and `nothing_due`

Each schedule drill has `kind` (`"pending"` when absent, so the first three files are unchanged).

- **`pending`:** work is waiting on a clock (rest, cooldown). The oracle is the rule above. A model that always sleeps
  long fails these.
- **`nothing_due`:** no kami needs action: harvesting, HP well above the risk line, no cooldown, `pending_work` empty.
  Oracle = minutes to the earliest useful event, capped at 1440 (the harness maximum):
  `min(harvest collect, HP risk line)`, each a linear extrapolation of the recorded rate:
  `(collect_target_musu - accrued_musu) / rate_per_hr` and `(hp - hp_risk_fraction * hp_total) / hp_drain_per_hr`.
  A model that always sleeps 5 minutes fails these (and burns calls); a model that always sleeps 24 h usually fails too,
  since an event arrives earlier, and passes only where the oracle is near the cap.
- **Why both:** one kind alone is gamed by a constant. Always-long passes only nothing-due drills with a near-cap
  oracle and fails every pending one; always-short passes neither. Only a model that reads the clock passes both.
- **Parameters, recorded in each nothing_due file (`params`):** `hp_risk_fraction` (0.3) and `collect_target_musu` (250)
  are this drill's assumptions, not game facts. Quest completion has no clock in a kami state, so it is not an event.
- **Source and honesty:** clock fields come from the recorded `lens_kami` read in a public v1 drill (`hpRatePerHr`,
  `cooldownSec`, `musu.accrued`, `musu.spotRatePerHr`). No v1 state is verifiably nothing-due account-wide (quests,
  feeding and other work are not in a kami read), so each file carries a `flags` entry: these are the closest honest
  cases, where the kami clock alone is idle. Oracles are 4 to 11 h; none is at the cap, so the capped case is only
  unit-tested on a synthetic state.

## Budget-use drill

One short episode, two arms: a forced budget of N calls (v1's `MAX_TOOL_CALLS` is 50, and a model cannot stop early:
a reply with no tool call is answered "keep working" up to 3 times), and a free arm with a `done` tool. Metric:
state-changing actions in the free arm over the budget arm (`action_ratio`; below 1 means less initiative when nothing
forces it on). v1 has no ACT/PERCEIVE classification, so `STATE_CHANGING` is an explicit list of transaction-sending
tools, tested against the harness tool list (it omits some writers, e.g. `burn_items`, which therefore count as reads).

`cadence.run_budget_pair(drill, model)` runs both arms offline through `kami_drills/episode.py` and returns
`action_ratio`, `off_trajectory_rate` (share of the two arms that ended off the path) and `steps` per arm. `model` is a
policy `model(history, arm) -> (tool, args) | None`. The Inspect task is `kami_drills/episode.py::budget_v2`
(`uv run inspect eval kami_drills/episode.py`), two samples per drill; its scorer's metadata carries the tool list so the
ratio is computed per drill pair. It has not been run against a model.

### On-trajectory replay

Drills are `drills_v2/cadence/budget-*.json`. Each points at a public v1 drill (`source_v1_id`) and a `start` message in
its recorded prefix. The recorded path is every tool call from `start` to the v1 fork, with its recorded result. The
public v1 files hold nothing after the fork, so that is all the continuation there is. Each file records
`trajectory_calls` and `trajectory_actions` (state-changing); the loader checks them against the source.

- **Match rule** (`episode.same_call`): same tool and equal arguments after normalising. Dropped: `account`, `None`
  values, the "my account" spellings (-1, "main", "") of account index/key, and values equal to the tool's schema
  default. Id lists compare as sorted sets; numbers and numeric strings are equal; text is trimmed and lower-cased.
  Anything else must be equal, so an extra or different argument is a different call.
- **State-changing call** (in `STATE_CHANGING`): must match the next recorded state-changing call; recorded reads in
  between may be skipped. A match returns the recorded result, errors included. Anything else ends the episode as
  `off_trajectory`, and nothing is answered. The call is counted as an action attempted.
- **Reads** keep v1 semantics: the nearest matching recorded read ahead on the path, then the drill's `recorded_reads`,
  then `_global_reads`. No match returns v1's "not available in this drill" error. No output is invented.
- **Ends:** `done` (free arm; `end_session` and `set_next_wake` also count), `budget_exhausted` (budget arm at N calls;
  the free arm has the 50-call cap), `off_trajectory`, `trajectory_end` (the last recorded state change matched, or a
  state change was attempted after it), or `stopped` (no tool call). Steps and actions are scored up to that point.

Budget drills: `budget-vendor-run` (from `repeat-newbie-vendor-guess`, 44 calls, 12 actions), `budget-harvest-run` (from
`stale-cooldown-retry-start`, 41 and 12), `budget-onboard-run` (from `onboard-complete-before-accept`, 48 and 11). Each
sets `budget_calls` to the full recorded length. Few v1 drills have 10 or more recorded state changes before the fork;
these are the best available.

## Limitations

- **On-trajectory only.** The episode rewards only paths the original agent took. It measures persistence along a
  known-good line, not exploration: a model that finds a different valid move is ended as `off_trajectory` and scored
  only up to there, so a high off-trajectory rate means "the replay cannot follow this model", not "the model is
  wrong". The original agent's own reverts are on the path too, and a path-following model gets `action_ratio` 1 by
  construction. Paths are short (at most 13 state changes), end at the v1 fork, and the free arm can only stop early.
- No Inspect task runs the schedule drill yet; `score_schedule` takes a chosen wake time.
- Nothing-due examples are kami-clock-only (see above); four, from three kami (20699, 715, 17194; the last twice, in different sessions).
- Two examples share one kami state (different forks); effective n is 2. The oracle is one rule, and `pass_threshold` is
  untuned.
- The clock is HP rest and cooldown. Harvest timers are not in v1 states (`harvestEnd` there is an event counter).
