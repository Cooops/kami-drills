# Skill-area profiles: `kami-drills profile`

**Status: experimental.** One pass/fail mean hides what a model is good at. `kami-drills profile` reports each model per
skill area, with n and a bootstrap 95% CI, plus an equal-weight aggregate over the areas, the way HELM reports
per-scenario results beside its aggregate. Scores come from the one spec scorer (`kami_drills/spec`), replayed through
the 4.7.0 adapter exactly as `scripts/v11/rescore.py` does.

```bash
uv run kami-drills profile plays.json                          # v1.1 specs by default; plays.json is {model: {drill: [tries]}}
uv run kami-drills profile plays.json --specs bank/v2.0-rc8 --json
uv run kami-drills profile results/v1.0.0-rc2/models/*.json --bars   # public: the v1 verdicts as recorded, not replayed
```

Input is any of: the rescoring plays file (`{model: {drill: [{steps, pass}]}}`, as `scripts/v11/eq.py` reads), a flat
list of try rows (`tests/fixtures/v1_rc2_plays.json`), or `results/<suite>/models/*.json` (per-drill v1 verdicts, taken
as recorded). `--json` gives the full profile, `--bars` adds a text bar chart, `--min-n` sets the thin flag (default 5).

## The five areas

| area | what it covers |
|---|---|
| survival | feed, revive and heal; avoid liquidation and starving |
| economic | earn and spend MUSU and ETH well: prices, swaps, the market |
| progression | levels, skill points and the quest chain |
| cadence | persistence: wake on time, keep a plan and the state straight across sessions |
| recovery | get out of a bad state; retry correctly after a refusal or a cooldown |

The mapping is one file, [`kami_drills/skill_areas.json`](../kami_drills/skill_areas.json), with a one-line reason for
each family and each override. A drill's family is its miner detector (bank items) or its v1 `category` (v1.1).

**One primary area per drill, no split weights.** Splitting a drill across two areas would count it twice in the
area-to-area correlation and make the within-area bootstrap resample fractions of drills. Where a v1 category is too
coarse, the drill is moved to the area its scoring rule rewards (`drills` in the file). Seven v1.1 drills are moved:

| drill | family → area | why |
|---|---|---|
| repeat-take-trade-underflow | repeat_failed → economic | the rule scores the bid price per unit against the MUSU held |
| repeat-listing-buy-underflow | repeat_failed → economic | the rule scores buying from a venue the account can afford |
| onboard-first-kami-vendor | onboarding → economic | the pass is a price tolerance; overpaying fails |
| onboard-bridge-pending | onboarding → cadence | the answer is a wake for the bridge already in flight |
| onboard-operator-unfunded | onboarding → recovery | a refused accept: fund (fix the cause), then retry |
| stale-cooldown-retry-start | stale_state → recovery | a start refused twice on cooldown: wait it out, then retry |
| memory-sonnet-stamina-unreliable-belief | memory → recovery | the rule forbids a blind retry of the stalled travel |

Family defaults: risk, hp-liquidated → survival; levelup, progression, onboarding, levelup-left, quest-left →
progression; memory, stale_state → cadence; repeat_failed, cooldown-repeat, stalling, stuck-broke → recovery;
discovery-swap, market-price, reckless-convert, node-choice → economic. Genuine controls (detector `control@1`) have no
area: they are never scored.

**v1.1 ranking drills per area:** survival 1, economic 3, progression 5, cadence 8, recovery 10 (27). Bank v2.0-rc8 adds
tier-A items to progression (8) and recovery (3) only: its survival and economic items are all tier B.

## What is scored

- A drill's score is the mean of its tries' spec scores (partial credit kept, 0 to 1). An area's score is the mean of its
  drill scores, with a percentile bootstrap 95% CI that resamples the area's drills (10,000 resamples, seed 0, via
  `scoring_v2.cluster_bootstrap_ci` with one drill per cluster; deterministic). One drill gives no CI.
- **equal-weight** is the mean of the area scores: every area counts the same (owner's choice). **drill-weighted** is
  the mean over every scored drill, so recovery's 10 drills count ten times survival's one. Both are reported.
- **thin**: an area with fewer than 5 drills is flagged, not hidden. Read its CI, not its point.
- **Not scored**, as the gate and the forecast split them: controls and traps are gate-only; tier B items are reported
  beside a score, never in it; smoke and retired drills never rank. The output lists how many of each were left out.

## v1.1 on the recorded rc2 tries (sensitivity, not a result)

`KAMI_DRILLS_PLAYS` (11 models × 2 tries × 47 v1 drills, not public) replayed against `specs/v1.1`. As in
[v1.1.md](v1.1.md) section 5, the tries were generated under v1, so this checks the instrument, not the models.

| model | equal-weight | drill-weighted | survival (n=1, thin) | economic (n=3, thin) | progression (n=5) | cadence (n=8) | recovery (n=10) |
|---|---|---|---|---|---|---|---|
| openai/gpt-6.1-sol | .78 | .70 | 1.00 | 1.00 [1.00–1.00] | .60 [.20–1.00] | .67 [.38–.92] | .65 [.47–.85] |
| anthropic/claude-opus-5.5 | .78 | .69 | 1.00 | .83 [.50–1.00] | .85 [.65–1.00] | .66 [.44–.88] | .55 [.30–.80] |
| google/gemini-4-argon | .76 | .64 | 1.00 | .83 [.50–1.00] | .90 [.70–1.00] | .75 [.50–.94] | .33 [.10–.57] |
| anthropic/claude-sonnet-5.5 | .74 | .63 | 1.00 | 1.00 [1.00–1.00] | .60 [.30–.90] | .56 [.31–.81] | .55 [.25–.85] |
| meta/muse-wonder | .66 | .62 | 1.00 | .33 [.00–1.00] | .80 [.40–1.00] | .56 [.31–.81] | .62 [.35–.85] |
| google/gemini-3.8-flash | .66 | .56 | 1.00 | .67 [.00–1.00] | .70 [.30–1.00] | .38 [.12–.69] | .55 [.28–.80] |
| google/gemini-3.6-flash | .62 | .47 | 1.00 | .67 [.00–1.00] | .80 [.40–1.00] | .31 [.06–.62] | .33 [.10–.57] |
| openai/gpt-6-luna | .62 | .52 | 1.00 | .50 [.50–.50] | .65 [.30–.95] | .43 [.18–.69] | .50 [.25–.75] |
| anthropic/claude-haiku-5.5 | .55 | .52 | .75 | .58 [.25–1.00] | .35 [.00–.75] | .56 [.31–.81] | .53 [.38–.68] |
| x-ai/grok-4.7 | .49 | .39 | .75 | .50 [.00–1.00] | .65 [.50–.85] | .25 [.06–.50] | .30 [.07–.55] |
| deepseek/deepseek-v4-pro-0813 | .47 | .39 | .50 | .75 [.25–1.00] | .65 [.30–.95] | .06 [.00–.19] | .40 [.15–.65] |

Area-to-area Pearson r across the 11 models:

| | survival | economic | progression | cadence | recovery |
|---|---|---|---|---|---|
| survival | | +.14 | +.44 | +.68 | +.36 |
| economic | +.14 | | +.01 | +.33 | +.13 |
| progression | +.44 | +.01 | | +.16 | −.26 |
| cadence | +.68 | +.33 | +.16 | | +.45 |
| recovery | +.36 | +.13 | −.26 | +.45 | |

r with the mean of the other four areas: survival +.65, economic +.24, progression +.15, cadence +.67, recovery +.28.

**Reading.**

- **Cadence separates models best.** It has the widest spread (.06 to .75, SD .20) at n = 8, and the ends do not overlap:
  deepseek-v4-pro .06 [.00–.19] and grok-4.7 .25 [.06–.50] sit below gemini-4-argon .75 [.50–.94]. Progression
  (.35 to .90) and recovery (.30 to .65) spread less than their CIs are wide (about ±.25); only their extremes barely
  separate. Recovery is the only area where gemini-4-argon, top on progression and cadence, is near the bottom (.33).
- **Survival and economic have too few drills.** Survival is one drill (risk-revive-then-heal), 1.00 for 8 of 11 models;
  economic is three, with CIs spanning most of [0, 1]. The bank does not help yet: its survival and economic items are
  all tier B. Under equal weights that one survival drill carries a fifth of the aggregate, so it moves deepseek by .10.
- **Areas do not just track the aggregate.** The mean off-diagonal r is +.24, and progression–recovery is negative.
  The two that move with the rest, survival and cadence (+.65, +.67), are the one-drill area and the area that
  separates. With 11 models an r has a wide interval (|r| < .6 is within noise), so read these as "not one factor",
  not as a structure.
- **Equal-weight and drill-weighted agree on the order** (Pearson .96, Spearman .97); only gemini-3.6-flash moves past
  gpt-6-luna and claude-haiku-5.5. Equal-weight is higher for every model, mostly because survival sits at ceiling.
- **What this means for v1.1.** The area profile is informative only for cadence, progression and recovery. Survival and
  economic need tier-A items before an equal-weight aggregate is more than a drill-weighted mean with one item
  over-weighted.

## Judgment calls

- Primary areas only (no split weights), and seven drills moved off their v1 category, listed above.
- `stale_state` is cadence: those drills ask the agent to keep earlier notes or reads straight against the live state,
  which is the "across sessions" half of cadence. The one stale drill that is a cooldown retry moved to recovery.
- `memory` is cadence (a plan or belief carried in notes), even where the act is a level-up.
- Areas with no drill for a model are left out of that model's equal-weight mean (`areas_in_equal_weight` in `--json`).
- An n=1 area gets no CI: a bootstrap of one drill is a point.

Tests: `tests/test_profile.py` (every drill maps, the aggregate math, CI determinism, the exclusions, the CLI on the
fixture).
