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

## Budget-use drill

One short episode, two arms: a forced budget of N calls (v1's `MAX_TOOL_CALLS` is 50), and a free "done" tool. Metric:
state-changing actions in the free arm over the budget arm (`action_ratio`; below 1 means less initiative when nothing
forces it on). v1 has no ACT/PERCEIVE classification, so `STATE_CHANGING` is an explicit list of transaction-sending
tools, tested against the harness tool list.

## Limitations

- **Budget runner is not implemented**: `run_budget_pair` raises "needs multi-step replay (see proposal §4.5)". v1
  replay answers one fork and cannot change state between calls. The format and scorer are done. No budget example exists.
- No Inspect task runs the schedule drill yet; `score_schedule` takes a chosen wake time.
- Examples are only the "work pending, wait for rest" type. Nothing-due drills (long sleep correct) are needed before
  the pair of cases can separate waiting from stalling; v1 states have only a few usable clocks.
- Two examples share one kami state (different forks); effective n is 2. The oracle is one rule, and `pass_threshold` is
  untuned.
- The clock is HP rest and cooldown. Harvest timers are not in v1 states (`harvestEnd` there is an event counter).
