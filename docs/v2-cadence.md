# v2 cadence drills

Draft, part of the [v2 proposal](v2-proposal.md) (sections 2 and 4.2). Cadence is a separate v2 format: v1 drills, the
v1 loader and the judge hash are untouched, and v1 never reads `drills_v2/`. Code: `kami_drills/cadence.py`. Tests:
`tests/test_cadence.py`. Offline only.

## Why cadence needs both sides

A cadence score has to tell **efficient waiting** (nothing is due, so a long sleep is right) from **stalling** (work is
pending, so a long sleep loses output). Scoring only "fewer calls" or "per-quest efficiency" rewards doing little: an
agent that sleeps all week has perfect efficiency on the quests it never starts. The schedule drill therefore scores the
wake time against a clock, in both directions, and the budget drill counts state-changing actions, not calls.

## Everything below is a prototype

Every schedule and budget drill here is a **prototype built from KamiBench-era v1 data** (public, CC-BY-4.0). They show
the format, the scoring and the loaders work. They are not the benchmark. The real bank comes from Season 1 after close;
no Season 1 data is in this repo. Each schedule file says `"prototype": true`.

## Schedule drill

`drills_v2/cadence/schedule-*.json`. The agent is at a session end and must call `set_next_wake` (`minutes_from_now`).
The prompt is the kami state plus one closing sentence, identical in every drill (`cadence.CLOSING`): *"You are at the end
of a session. Call set_next_wake with minutes_from_now."* Just before it, every situation carries the same operator
policy line (`cadence.POLICY`): *"Policy: restart a resting kami's harvest as soon as its cooldown ends; collect when
accrued MUSU reaches 250; act before HP falls below 30% of its maximum. The longest wake you can set is 1440 minutes."*
The thresholds (250 MUSU, 30% HP, the 1440 cap) are therefore stated facts the model reasons from, not guesses; the
loader rejects a drill whose text lacks the line or whose `params` differ from it. The line is identical in form in every
drill and says nothing about which kind a drill is; the closing stays neutral. World facts the oracle uses (HP drain per
hour, MUSU rate, cooldown) are shown.

- **Score:** raw loss `|ln(chosen / oracle)|`, always reported. Optional `tolerance_band` (a factor, default `null`):
  loss becomes `max(0, raw - ln(band))`. `pass_threshold` (ln units, 0.7 = a factor of 2) is applied to that loss.
- **Where drills fork.** Only at a point where the original v1 agent ended its own turn. The earlier drills forked at
  points where the v1 rule requires an action, so "wait" was the wrong answer there; those were removed. Scanning all 47
  public v1 prefixes: **no** prefix contains a `set_next_wake` or `end_session` call, and **two** contain a text-only
  stop by the agent (`stale-harvest-start` message 9, `onboard-fund-amount` message 7; the harness then said "Continue. To
  end this session, call end_session."). `onboard-fund-amount` has no kami, so there is no clock to schedule on. That
  leaves **one genuine session end**: `schedule-harvest-9811-session-end` (state from the `lens_party` read at message 4:
  27/70 HP, losing 5.09/hr, 26.62 MUSU/hr, no cooldown). v1's rule for that drill requires `harvest_collect` first and then
  accepts a `set_next_wake` of about 60 min. The original agent never collected, so the situation states the collect as
  done ("You have just collected its harvest ... 0 MUSU accrued"): this is the genuine state with v1's required action
  applied, not a verbatim record, and it is labelled `nothing_due` honestly because nothing is left to do. The oracle
  (70.7 min, HP binding) is within a factor of 2 of v1's 60.
- **Synthetic variants.** The other six files are **synthetic**: the same kami, with the clock fields changed so the
  oracles spread from 2 to 1440 min, so no single value fits. They are marked `"origin": "synthetic_variant"` and are not
  recorded states. They exist to test the scorer and to show the spread; replace them with real session ends from Season 1.
- **Kinds.** `pending` (a RESTING kami with cooldown, pending work `harvest_start`): oracle `cooldown_s / 60`, the cooldown end.
  This agrees with v1 `stale-cooldown-retry-start` (kami 9787, RESTING 71/90, cooldown about 100 s: wait it out, then
  `harvest_start`); HP is not waited on, so the earlier rest-to-full oracle, which contradicted v1, is gone. The two
  pending drills are `schedule-cooldown-124s` and `schedule-cooldown-900s`.
  `nothing_due` (a HARVESTING kami, nothing on cooldown): oracle is the earliest of the harvest reaching
  `collect_target_musu` and HP draining to `hp_risk_fraction * hp_total`, each a linear extrapolation, capped at 1440
  (the harness maximum). The kind is an author label; the model is not told it.
- **Untuned parameters.** `hp_risk_fraction` (0.3), `collect_target_musu` (250) and `pass_threshold` (0.7) are this
  drill's assumptions, not game facts, are recorded in each file (`params`), and have not been tuned. The first two are
  stated to the model as policy; the pass threshold is not. `schedule-harvest-idle` is a live harvest at 8 MUSU/hr, so the
  collect target is 31 h away and 24 h is right (the earlier 0 MUSU/hr version rewarded sleeping on a broken harvest). Quest completion
  has no clock in a kami state, so it is not an event.
- **Gaming checks** (`tests/test_cadence.py`): no constant wake time, and no constant chosen per drill kind with
  hindsight, passes more than half of the drills; the closing prompt is identical and the preceding text has no rule hints.
  This is a check on the shipped files, not a proof: seven drills is a small bank.
- Each drill records `source_v1_id` and either `source_end_msg` or `variant_of`. The loader checks that a genuine
  `source_end_msg` is an assistant message with no tool call.

## Budget-use drill

One short episode, two arms. Both arms get v1's 50-call session cap (`MAX_TOOL_CALLS`, the proposal's forced budget);
the budget arm has no `done` tool and a reply with no tool call is answered "keep working" up to 3 times, and the free
arm has a `done` tool. Metric: state-changing actions per arm (`actions`), and their ratio free over budget
(`action_ratio`). **Raw counts cannot separate a spammer from a follower**: a one-call off-path spammer and a perfect
follower both get `action_ratio` 1, and skipping a recorded revert lowers the count while repeating it raises it. The
budget metric is therefore based on **matched actions** (`matched_actions` per arm): the non-revert recorded state changes
the model matched. `path_actions` is what a perfect follower can reach, the path's non-revert state changes (11 or 12; the
raw 13 includes skippable reverts). Per drill: `matched_ratio` = free matched over budget matched, and `coverage` = matched
over `path_actions` per arm. **Matched ratio alone cannot separate persistence from early divergence**: leaving the path
ends both arms, so any model that diverges at the same action in both arms gets ratio 1, including an on-path spammer
that repeats the first recorded action (1 matched in each arm). The registered metric,
`cadence.pooled_budget_score(results)`, multiplies in budget-arm coverage: with F, B and P the free-arm matched,
budget-arm matched and `path_actions` summed over drills, it is min(F / B, 1) × B / P = min(F, B) / P. A perfect
follower scores 1, an off-path spammer 0, the on-path spammer 1 / P (0.11, 0.10 and 0.08 on the three prototypes) and a
follower that quits halfway in the free arm about 0.5 (`tests/test_episode.py`). `matched_ratio`, `action_ratio` and
`actions` stay as diagnostics.
**`act_sequence` limit:** the replay does not expand its steps (up to 64), so it is one unmatched call: it counts as one raw
action, ends the episode as `off_trajectory`, and adds nothing to matched actions.

**One classification for the package.** `harness_tools.json` has no ACT/PERCEIVE category, so `cadence.STATE_CHANGING`
is derived from it: every harness tool except an explicit, reviewed `READ_ONLY` list (get_*, quotes, status reads) and
the `lens_*` reads. That is 67 of 109 tools, including `newbie_vendor_buy`, `fund_operator`, `listing_buy`, `pool_swap`,
`craft_item`, `burn_items` and the other transaction tools. `cadence.is_state_changing(tool)` is the predicate. A test
checks that every harness tool is classified and that every tool whose description says it signs a transaction is
state-changing. Other modules should import this one set.

`cadence.run_budget_pair(drill, model)` runs both arms offline through `kami_drills/episode.py` and returns `actions`
and `matched_actions` per arm, `action_ratio`, `matched_ratio`, `coverage`, `path_actions`, `off_trajectory_rate` (share of the two arms that ended off the path) and
`steps` per arm. `model` is a policy `model(history, arm) -> (tool, args) | None`. The Inspect task is
`kami_drills/episode.py::budget_v2` (`uv run inspect eval kami_drills/episode.py`), two samples per drill; its scorer's
metadata carries the tool list so the ratio is computed per drill pair. It has not been run against a model.

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
  between may be skipped, and so may a recorded state change whose result was a revert or error (the model need not
  repeat a failure; a later recorded call still matches). A match returns the recorded result, errors included. Any
  other call ends the episode as `off_trajectory`, and nothing is answered. The call is counted as an action attempted.
- **Reads** keep v1 semantics: the nearest matching recorded read ahead on the path, then the drill's `recorded_reads`,
  then `_global_reads`. No match returns v1's "not available in this drill" error. No output is invented.
- **Ends:** `done` (free arm; `end_session` and `set_next_wake` also count), `budget_exhausted` (50 calls, both arms), `off_trajectory`, `trajectory_end` (the last recorded state change that did not revert
  matched, or a state change was attempted after it), or `stopped` (no tool call). Steps and actions are scored up to that point.

Budget drills (prototypes, KamiBench-era v1 data): `budget-vendor-run` (from `repeat-newbie-vendor-guess`, 44 recorded
calls, 13 state changes), `budget-harvest-run` (from `stale-cooldown-retry-start`, 41 and 13), `budget-onboard-run`
(from `onboard-complete-before-accept`, 48 and 13). The counts use the package-wide classification (`burn_items`,
`scavenge_claim_and_reveal` and `register_account` count; earlier text said 12, 12 and 11). `budget_calls` is 50 in every
file, not the path length. Some of the recorded state changes reverted (1 to 4 per path); those can be skipped. Few v1
drills have 10 or more recorded state changes before the fork; these are the best available.

## Limitations

- **On-trajectory only.** The episode rewards only paths the original agent took. It measures persistence along a
  known-good line, not exploration: a model that finds a different valid move is ended as `off_trajectory` and scored
  only up to there, so a high off-trajectory rate means "the replay cannot follow this model", not "the model is
  wrong". The original agent's own reverts are on the path too (skippable, and not counted as matched), and a
  path-following model gets matched ratio 1 by construction (so does any model that leaves the path at the same
  point in both arms; the budget score's coverage factor is what separates them). Paths are short (at most 13 state changes), end at the v1 fork, and the free arm can only stop early.
- The schedule task exists (`cadence.py::schedule_v2`, one `set_next_wake` call per drill, scored not executed) but has
  never been run against a real model; the tests use `mockllm`.
- **One genuine schedule drill.** Public v1 data holds one usable session end (see above); the other six schedule drills
  are synthetic clock variants of it, same kami, one rule family. Effective n is far below 7, there is no cluster
  structure, and a pass rate over this bank says little. The bank is a format check until Season 1 data replaces it.
- The oracle is still one rule per kind, with untuned parameters (0.3 risk line, 250 MUSU collect target, factor-2
  threshold). The first two are stated in the prompt; the 2x window is not. Pending drills cover only `harvest_start`
  after a cooldown.
- The "no constant policy passes more than half" check is on the shipped seven drills and a grid of constants; it does not
  cover policies that read the state (for example, "wait for HP to drain to 30%"), which is the intended solution.
- The clock is HP rest, HP drain, cooldown and harvest accrual. Harvest timers are not in v1 states (`harvestEnd` there
  is an event counter), and quests have no clock.
