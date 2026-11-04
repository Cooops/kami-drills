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
as recorded). `--json` gives the full profile, `--bars` adds a text bar chart, `--min-n` sets the thin cut (default 5).

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
area-to-area correlation and make the within-area bootstrap resample fractions of drills. **Override rule:** a drill
leaves its family's area when its scoring rule rewards another area's skill (`drills` in the file). Nine v1.1 drills
are moved:

| drill | family → area | why |
|---|---|---|
| repeat-take-trade-underflow | repeat_failed → economic | forbids the retry and any Scrap bid above 100 MUSU/unit; the price cap is what separates the plays |
| repeat-listing-buy-underflow | repeat_failed → economic | the rule scores buying from a venue the account can afford |
| onboard-first-kami-vendor | onboarding → economic | the pass is a price tolerance; overpaying fails |
| repeat-stop-starving | repeat_failed → survival | the stop is refused because the kami is starving; the rule rewards feeding it first |
| memory-luna-levelup-needs-no-points-belief | memory → progression | the rule only counts `level_up` calls |
| onboard-bridge-pending | onboarding → cadence | the answer is a wake for the bridge already in flight |
| onboard-operator-unfunded | onboarding → recovery | a refused accept: fund (fix the cause), then retry |
| stale-cooldown-retry-start | stale_state → recovery | a start refused twice on cooldown: wait it out, then retry |
| memory-sonnet-stamina-unreliable-belief | memory → recovery | the rule forbids a blind retry of the stalled travel |

The two vendor drills are split on purpose: onboard-first-kami-vendor's pass is a price cap (economic), while
repeat-newbie-vendor-guess has no price cap and scores not guessing again after the refusal (recovery).

Family defaults: risk, hp-liquidated → survival; levelup, progression, onboarding, levelup-left, quest-left →
progression; memory, stale_state → cadence; repeat_failed, cooldown-repeat, stalling, stuck-broke → recovery;
discovery-swap, market-price, reckless-convert, node-choice → economic. Genuine controls (detector `control@1`) have no
area: they are never scored.

**Competency check.** `tests/test_profile.py` fails any drill whose area is not one its spec's `competency` tags name
(risk_survival → survival, economy → economic, quest_planning → progression, error_recovery → recovery, state_tracking,
memory and persistence → cadence), unless the drill is an override above or its family is in `competency_exceptions`
(levelup, stalling, cooldown-repeat, stuck-broke), each with its reason in the file.

**v1.1 ranking drills per area:** survival 2, economic 3, progression 6, cadence 7, recovery 9 (27). Bank v2.0-rc8 adds
tier-A items to progression (8) and recovery (3) only: its survival and economic items are all tier B.

## What is scored

- A drill's score is the mean of its tries' spec scores (partial credit kept, 0 to 1). An area's score is the mean of its
  drill scores, with a percentile bootstrap 95% CI that resamples the area's drills (10,000 resamples, seed 0, via
  `scoring_v2.cluster_bootstrap_ci` with one drill per cluster; deterministic).
- **thin**: an area with fewer than `--min-n` (5) drills is flagged, not hidden. It gets no CI: too few drills for a
  CI; treat as anecdotal.
- **equal-weight**, the headline, is the mean of the non-thin area scores: every such area counts the same (owner's
  choice). It is None when no area has n ≥ 5. **equal-weight all** is the mean of every area score, thin ones included,
  and the output lists the thin areas it left out of the headline (`thin_areas_excluded` in `--json`).
  **drill-weighted** is the mean over every scored drill, so recovery's 9 drills count 4.5 times survival's 2.
- **Not scored**, as the gate and the forecast split them: controls and traps are gate-only; tier B items are reported
  beside a score, never in it; smoke and retired drills never rank. The output lists how many of each were left out.

## v1.1 on the recorded rc2 tries (sensitivity, not a result)

`KAMI_DRILLS_PLAYS` (11 models × 2 tries × 47 v1 drills, not public) replayed against `specs/v1.1`. As in
[v1.1.md](v1.1.md) section 5, the tries were generated under v1, so this checks the instrument, not the models. The
CLI prints the model names as the plays file keys them; the provider prefixes below were added by hand to match the S1
public results.

| model | equal-weight (n ≥ 5 areas) | equal-weight all | drill-weighted | survival (n=2, thin) | economic (n=3, thin) | progression (n=6) | cadence (n=7) | recovery (n=9) |
|---|---|---|---|---|---|---|---|---|
| anthropic/claude-opus-5.5 | .68 | .72 | .69 | .75 [thin] | .83 [thin] | .83 [.67–.96] | .64 [.36–.86] | .56 [.28–.83] |
| google/gemini-4-argon | .64 | .70 | .64 | .75 [thin] | .83 [thin] | .75 [.42–1.00] | .86 [.64–1.00] | .31 [.06–.58] |
| meta/muse-wonder | .63 | .65 | .62 | 1.00 [thin] | .33 [thin] | .67 [.33–1.00] | .64 [.36–.86] | .58 [.31–.83] |
| openai/gpt-6.1-sol | .62 | .77 | .70 | 1.00 [thin] | 1.00 [thin] | .50 [.17–.83] | .76 [.43–1.00] | .61 [.42–.81] |
| anthropic/claude-sonnet-5.5 | .59 | .65 | .63 | .50 [thin] | 1.00 [thin] | .58 [.33–.83] | .57 [.29–.86] | .61 [.33–.89] |
| google/gemini-3.8-flash | .54 | .56 | .56 | .50 [thin] | .67 [thin] | .58 [.25–.92] | .43 [.14–.71] | .61 [.33–.86] |
| openai/gpt-6-luna | .53 | .52 | .52 | .50 [thin] | .50 [thin] | .58 [.29–.88] | .45 [.17–.74] | .56 [.31–.81] |
| anthropic/claude-haiku-5.5 | .51 | .50 | .52 | .38 [thin] | .58 [thin] | .38 [.08–.71] | .57 [.29–.86] | .58 [.50–.69] |
| google/gemini-3.6-flash | .46 | .51 | .47 | .50 [thin] | .67 [thin] | .67 [.33–1.00] | .36 [.07–.71] | .36 [.11–.61] |
| x-ai/grok-4.7 | .35 | .49 | .39 | .88 [thin] | .50 [thin] | .62 [.50–.79] | .21 [.00–.50] | .22 [.06–.44] |
| deepseek/deepseek-v4-pro-0813 | .35 | .41 | .39 | .25 [thin] | .75 [thin] | .54 [.21–.88] | .07 [.00–.21] | .44 [.17–.72] |

The headline here is the mean of progression, cadence and recovery; survival and economic are thin.

Area-to-area Pearson r across the 11 models:

| | economic | progression | cadence | recovery |
|---|---|---|---|---|
| survival | −.07 | +.38 | +.53 | −.09 |
| economic |  | +.05 | +.32 | +.17 |
| progression |  |  | +.22 | −.34 |
| cadence |  |  |  | +.33 |

r with the mean of the other four areas: survival +.32, economic +.17, progression +.20, cadence +.70, recovery +.09.

**Reading.**

- **Cadence separates models best.** It has the widest spread of the non-thin areas (.07 to .86, SD .22, n = 7).
  deepseek-v4-pro .07 [.00–.21] and grok-4.7 .21 [.00–.50] sit below gemini-4-argon .86 [.64–1.00], but grok's interval
  barely touches gpt-6.1-sol's [.43–1.00] and overlaps the rest. Progression (.38 to .83) and recovery (.22 to .61)
  spread less than their CIs are wide (about ±.25); only their extremes separate. Recovery is where gemini-4-argon, top
  on cadence, is near the bottom (.31).
- **Survival and economic have too few drills** (2 and 3), so they get no CI and stay out of the headline. The bank does
  not help yet: its survival and economic items are all tier B. They still matter for the order: gpt-6.1-sol is first on
  drill-weighted (.70) and equal-weight all (.77) but fourth on the headline (.62), because its two perfect areas are
  the thin ones. Under equal-weight all, deepseek's survival score of .25 against the 1.00 ceiling costs it .15; without
  survival, deepseek (.45) ranks above grok (.39) on the other four areas.
- **grok-4.7 and deepseek-v4-pro tie** on drill-weighted (.39 each) and on the headline (.354 vs .353): the bottom two
  cannot be ordered.
- **Areas do not just track the aggregate.** The mean off-diagonal r is +.15; progression–recovery is −.34. Only
  cadence moves with the rest (+.70). With 11 models any |r| below about .6 is within noise, so read this as "not one
  factor", not as a structure.
- **The headline and drill-weighted agree broadly** (Pearson .97, Spearman .92); the largest move is gpt-6.1-sol, above.
- **What this means for v1.1.** The profile is informative for cadence, progression and recovery. Survival and economic
  need tier-A items before they can enter the headline.

## Judgment calls

- Primary areas only (no split weights), and nine drills moved off their v1 category by the override rule, above.
- `stale_state` is cadence: those drills ask the agent to keep earlier notes or reads straight against the live state,
  which is the "across sessions" half of cadence. The one stale drill that is a cooldown retry moved to recovery.
- `memory` is cadence (a plan or belief carried in notes), except where the rule only scores another area's act.
- Areas with no drill for a model count as thin for that model.

Tests: `tests/test_profile.py` (every drill maps, the competency check, the aggregate math, CI determinism, the
exclusions, the CLI on the fixture).
