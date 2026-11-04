# v2 drill spec and harness adapters

**Status: experimental, Season 2 proposal.** Nothing here touches `drills/`, the v1 loader, `task.py` (judge hash
`308c5deb…` unchanged) or `kami-drills run`. v1 stays frozen for the live Season 1 forecast.

A v1 drill is a transcript prefix plus replay rows keyed on kami-harness tool names, so it runs on one harness only.
A v2 drill is a **spec**: the game state at the fork in game terms, response rows over **intents** (abstract game
actions), and a scoring rule over the intent trace and the simulated end state. An **adapter** renders a spec into one
harness's tool surface and maps the model's tool calls back to intents. The scorer never sees a tool name, so a harness
can change what the model sees but never how a trace is judged.

```bash
uv run kami-drills spec check drills_v2/spec              # schema, three plays, lazy battery, adapter check (offline, free)
uv run kami-drills spec from-v1 drills_v2/spec/*.json     # refresh converted v1 specs from drills/
uv run kami-drills spec from-miner candidates.jsonl --runs "$KAMI_DRILLS_S1_RUNS" --out out/specs
uv run inspect eval kami_drills/adapters/harness_4_7.py -T paths=drills_v2/spec --model <model>
uv run inspect eval kami_drills/adapters/harness_4_7.py -T policy=oracle --model mockllm/model   # must pass
```

Code: `kami_drills/spec/` (`__init__.py` load/validate/simulate/score, `rules.py` + `kami_world.json` the kami-rules
library, `check.py`, `perturb.py`, `from_v1.py`, `from_miner.py`, `drill-spec.schema.json`),
`kami_drills/adapters/harness_4_7.py`. Specs: `drills_v2/spec/` (converted v1), `bank/v2.0-rc9/` (mined). Tests:
`tests/test_spec.py`, `tests/test_kami_rules.py`, `tests/test_adapter_4_7.py`, `tests/test_spec_convert.py`.

This is the **one scorer**: the miner gates every mined item with `spec.check` ([v2-miner.md](v2-miner.md)), and a
model's trace is scored by the same `spec.score`.

## 1. The spec

The JSON Schema (`kami_drills/spec/drill-spec.schema.json`, 2020-12) is authoritative. It is the D design draft
(spec_version 2.0.0) with one addition: reason code `BAD_ARGS` (a request the game refuses by rule: "not a trade",
"node does not exist", an unknown room). Without it 4.9% of Season 1 tool errors had no code; with it 1.2% do. The name matches the error code kami-harness already returns. Owner sign-off 2026-11-04.

| Part | Holds |
|---|---|
| `state`, `world` | account, kamis, inventory by item index, quests, gas; pools, rooms, catalog rows, `constants` |
| `fork` | kind, allowed `rendering` modes in order, `clock` (unix), `history`, `notes`, `native_prefix` |
| `responses[]` | intent pattern → result (`ok` / `revert` / `validation_error` / `gap`, `reason`, `fields`, `state_delta`, `native` text per harness), gated by flags (`when`/`unless`), the clock (`clock_gte`/`clock_lt`) and state predicates; first match wins |
| `scoring` | `rule` (v1 rule language over intents, plus `end_state` and `needs_action`) or `partial_credit` (weighted components, penalties, `zero_score`, `pass_threshold`); `null_play`; `reference_plays` (oracle, recorded mistake, others) |
| `state_dependencies` | every field the verdict reads, as JSON pointers; re-validation diffs these |
| `harness` | `bound`, `recorded_on`, `validated_on[]` |
| `provenance`, `heldout`, `content_hash` | source run/model (for leave-own-drills-out), seal; sha256 of canonical JSON minus stamps |

Fields added during integration (MINOR, folded into the 2.0.0 draft since nothing is released):

| Field | Meaning |
|---|---|
| `world.rules_library` | `kami-rules@1.0.0` opts into the shared rules library (below); rows still win over it |
| `world.native_text` | reason code → recorded refusal text per harness; library refusals carry it as `native` |
| `world.constants` | `call_seconds`, `cooldown_accept_from_s`, `cooldown_full_from_s`, `cooldown_evidence`, `late_after_unix`, `inventory_known`, `portal_tokens` |
| `scoring.refusal_penalty` | any kind: minus `amount` per refused act intent (reads, quotes, time and gaps are not refusals) |
| `scoring.lesser` | kind `rule`: lesser accepted plays; the best one whose rule passes gives its credit |
| `scoring.flag_caps` | any kind: the score is at most `max` when the simulation set `flag` (`cooldown_band`) |
| `heldout.method: private_variant` | a held-out perturbed variant (`perturb.py`), private until the season closes |
| `canary` | set on every mined spec |
| `role`, `retired_reason` | suite role (ranking / smoke / retired); a retired item states why, and has status retired |
| `scoring.timing_item` | a pure timing item: a battery play ending in wait 300 + the mistake may pass, only if it fails without that tail |
| `components[].any_of` | instead of `rule`: `[{rule, credit}]`, the component earns weight × the best passing credit (act = 1 or wake = 0.5) |
| `reference_plays[].is_authored_mistake` | a success fork's authored mistake; the battery's mistake plays use it when there is no recorded one |
| `result.native["*"]` | recorded text any adapter serves verbatim unless the drill is harness-bound (the v1.1 replay rows) |

`load` skips a directory's `MANIFEST.json` (a suite manifest, not a spec).

`validate()` adds checks the schema cannot state: the content hash matches, an `oracle` play exists, a mistake drill
has a recorded mistake, and a null play may pass only if the drill declares waiting correct (`null_play.expected` ≥
the pass threshold).

Scorers implemented: `rule`, `partial_credit`. `schedule_loss`, `horizon`, `budget_pair` and `ev_regret` are in the
schema but not wired yet; `spec check` reports them as not implemented instead of guessing.

## 2. Intents

The vocabulary is the schema's closed `intent.verb` enum (`spec.VERBS`): `feed`, `revive(kami, method)`,
`harvest_start/stop/collect`, `travel(room)`, `accept_quest`, `complete_quest`, `level_up`, `upgrade_skill`, `swap`,
`portal_deposit/withdraw`, `buy`, `wait(seconds)`, `schedule_wake(minutes)`, `end_session`, `note_read/write`,
`read(kind, …)`, `quote(of, …)`, `other_act`, `unknown`, and the rest of the economy verbs. `buy` keeps the kami
(`newbie_vendor_buy`'s index, `buy_kami`'s ids), and `max_unit_price` is always per unit (`buy_kami`'s total / qty, with
the total in `max_total_price`). Arguments are game names
(`kami`, `item`, `room`, `node`, `quest`, `min_out`), never harness argument names.

- A **dry run** of any act is `quote(of=<verb>, …)`: it answers what the act would do now and changes nothing. It is
  perception, never the act, so a probe is neither the answer nor a forbidden retry (B audit: dry-run probes failed
  `stale-cooldown-retry-start`).
- Patterns: `verb` (or list), `args` with v1 operators (`in`, `contains`, `gte`, …), `ok: true` (succeeded) and
  `ok: false` (failed only: v1's `ok` could not say this). Scoring reuses `task.rule_failures` unchanged by
  presenting intents as calls.

**The rules library** (`spec/rules.py`, `world.rules_library: kami-rules@1.0.0`). When no response row matches an act,
the library answers it from the simulated state the way the chain or the harness's pre-transaction check would, and
applies its effects (cooldown on start/stop/collect, room, HP, items, quest status, level). It covers cooldowns (gdd
`utility/cooldowns.md`: start, stop, collect, item use, the killer of a liquidation), harvest state and room, starving,
kami foods and held items, all five revive methods with their items (onyx: 33 of item 100), level-up only while
RESTING and ready (`experience-leveling.md`), node level caps from the gdd's `nodes.csv` (node 55 cap 15; the harness
copy leaves it blank), the room graph as kami-harness `rooms_graph.py` builds it (`travel` with `hop: true` from
`move_to_room` needs an adjacent room; `travel_to_room` a path), quest objectives and `requirements_met: false`
(a completion the chain refused although the check said yes), token deposits against a declared balance, and
constant-product pools from `world.pools`. Anything else is `None`, so the act is a gap as before. Static data is
`kami_world.json`, regenerated by `scripts/build_kami_world.py <kamigotchi-gdd>`.

Cooldown timing is calibrated on Season 1 (earliest acceptance 4.4 s after `cooldown_until`, refusals under 0.5% from
26 s): before `cooldown_until` refused and flagged `early_retry`; up to +4 s refused unflagged; up to +26 s accepted with
the `cooldown_band` flag (credit capped at 0.8); later accepted. `constants.cooldown_evidence` per kami moves the bounds
to an item's own record (never accepted before its last recorded refusal, full credit from its first acceptance).

**Waiting semantics.** Only the simulated clock decides. `wait` and `schedule_wake` advance it before rows match, so
split waits add up. `world.constants.call_seconds` makes every other intent take time too, so useful work during a
wait counts (0 by default: v1's behaviour). `spec.cooldown_rows(match, until, skew_s, late_from=)` builds the standard
cooldown rows: ok from `until + skew`, a flagged revert before `until`, an *unflagged* revert inside the skew window
(chain time lags the agent's clock; the live game reverts there, so retrying after it is fair), and an optional
`late` flag for acting long after the window.

## 3. Adapter contract

An adapter is one harness version plus one scaffold rung; pure code, no network, no state between attempts. The
4.7.0 adapter (`kh-4.7.0@1.0.0`, kami-harness 4.7.0 df7129f + kami-agent 0.7.5 + lens 1.0.5, rung `control`) provides:

| Member | 4.7.0 behaviour |
|---|---|
| `surface()` | `harness_tools.json` minus `chat_send` and the 9 OUTSOURCE tools (the Season 1 live denylist) plus the 8 scaffold tools: 107 tools |
| `to_intents(tool, args, sim)` | state-aware: `harvest_*` lists split per kami; `act_sequence` per step; level/feed batches per target, an empty batch is no intent (and a harness validation error); `dry_run: true` → `quote`; `pool_swap_quote` → `quote(swap)`; `move_to_room` → `travel(room, hop=true)`, `travel_to_room` → `travel(room)`; `use_item_batch` with a revive item on a DEAD kami → `revive`; `set_next_wake` clamped 5..1440, `wait` to 300 s |
| `from_intent(intent)` | inverse, for reference plays; `None` = not expressible here |
| answers | per intent: the row's `native` text for this harness, else 4.7.0-worded templates ("validation failed; no transaction sent: transaction dry-run reverted: {… 'revert: kami on cooldown: Reverted'}", JSON receipts); reads formatted from the simulated state (`lens_kami` with `cooldownSec` from the clock, `get_status` with the simulated `now`, party, roster, account, inventory, gas, quests), then pre-fork recorded reads while nothing has changed, then v1's world reads |
| gaps | the harness's own transient error, copied from S1: `Error executing tool T: 503 Server Error: Service Unavailable for url: …`. v1 said "not available in this drill" and 25% of tries quoted it back (B audit) |
| `reason_code(text)` | regex table over the message (not the tool name); 1.2% OTHER over 3,277 S1 tool errors (test) |
| `quirk(intent, sim)` | 4.7.0 pre-transaction refusals that win over the spec's rows (below) |
| `render_fork(spec)` | `native_replay` (a prefix recorded on another harness is served only if the drill is not harness-bound: v1's practice), `rerendered_history`, or `snapshot_brief` (`Session start.` then the scaffold's `lens_roster` and `get_gas_balance`, formatted from state) |
| `native_battery(spec)` | harness-level degenerate plays: empty batches, the oracle's acts as dry runs |
| `spec_v2` | Inspect task; graded score, binary `pass` in score metadata |

**Quirks (harness-bound behaviour).** `accept-owned-repeatable`: 4.7.0's `accept_quest` refuses any quest the account
owns, repeatables included (`executor/server.py:11161`), although the chain accepts a repeat of 3900 (eth_call). A
drill whose oracle needs that re-accept is `not_expressible` on this adapter and the check names the quirk, so it can
be tagged harness-bound. `completable-ignores-room`: `check_quest_completable` ignores room requirements.

## 4. The offline check

`kami-drills spec check` runs, per spec, with no model:

1. **Three plays.** The oracle must pass; the null play (no calls) must score its declared `expected` and fail; the
   recorded mistake must fail. Every other reference play must score its `expected`, every `accepted` path must pass.
2. **Degenerate battery** (`check.battery`), built state-blind from what the fork shows: no calls, bare
   `end_session`, wakes of 5/60/1440 min, `wait 300` (+ end, ×3), read-then-end, dry-run-only, the kitchen sink
   (accept/complete every quest; level, skill, stop, collect, feed, revive every kami; one unit of every portal token
   the spec declares, deposited and sold; wait 300; then the recorded mistake), always-feed, always-revive,
   always-level, always-stop, always-move, collect-all, start-then-level, complete-all-quests,
   move-to-each-room-then-the-mistake, wait-then-the-mistake, mistake-then-wake, and the token move. The mistake is the
   recorded one, else a success fork's authored one. Every play must fail, except time/read-only plays on a drill that
   declares waiting correct, and any play on a trap control (it passes by not doing the act).

   **Groups.** `check.check_bank(specs)` gates items that share `variants.group` together (an item and its control,
   an opportunity and its trap): a play may pass members of a group, but at most half of them, so a constant policy
   averages ≤ 0.5 on the group. Ungrouped items must fail every play themselves. `kami-drills spec check` uses it.
3. **Harness invariance**, per adapter: the reference plays are rendered to tools (`from_intent`), run through the
   adapter's episode (to_intents → quirks → simulator → answers) and scored again.

| Status | Condition |
|---|---|
| `ok` | every reference play keeps its verdict |
| `solved_by_harness` | the recorded mistake passes on this adapter |
| `not_expressible` | an oracle act has no tool here, or a harness quirk blocks it (harness-bound) |
| `verdict_changed` | another reference play's verdict differs: blocks release |
| `rendering_changed` | the drill is harness-bound to another harness, or no allowed rendering is possible |

A recorded mistake that cannot be re-expressed because an intent dropped arguments (a kamiswap buy) is checked at
the intent level only, and the note says so. Native lazy passes count as failures.

## 5. Converters

**v1 → spec** (`from_v1.py`). The game half (state, responses over intents, rule) is hand-normalized once and is the
spec file itself; `convert()` fills `native_prefix` verbatim from `drills/<id>.json` and stamps the v1 file hash.
`equivalence()` replays recorded tries through the 4.7.0 adapter. On the 66 recorded rc2 tries (11 models × 2 tries ×
3 drills, `tests/fixtures/v1_rc2_plays.json`) every verdict matches v1, and v1's own rule translated mechanically
(`h47.tool_rule_to_intent`) gives the same verdicts too.

| Drill | v1 pass / v2 pass | 4.7.0 | Battery passes (v1 flaws inherited) |
|---|---|---|---|
| risk-starving-feed-first | 21/22 = 21/22 | ok | kitchen sink, always-feed (the answer is "feed": a smoke test, as the B audit says) |
| stale-cooldown-retry-start | 17/22 = 17/22 | ok (foreign prefix served, not bound) | wakes 5/60/1440, waits, kitchen sink, wait-then-mistake (v1 passes any wake ≥ 3 min) |
| repeat-pool-swap-retry | 15/22 = 15/22 | rendering_changed (bound to pre-4.7 bare "Reverted") | 11 incl. bare `end_session` (v1's `require_any` lists it) |

These are reported, not fixed: the converted drills must stay verdict-equivalent to frozen v1. The fixed versions
are the v1.1 suite, `specs/v1.1/` ([v1.1.md](v1.1.md)): its game half is generated by `scripts/v11/` (overlay from the
v1 drill and its source transcript, then the audit fixes), and `run.py` fills the native half with `spec from-v1`. A v2 version of each
(wake = 0.5 partial credit, dry runs exempt) is a new `version`.

**Miner candidate → spec** (`from_miner.py`, `kami-drills/candidate@3`). The only path from the miner to the scorer:
the miner's gate calls it in memory for every candidate and runs `spec.check` on the result. State comes from the
session-start snapshot (`normalize_snapshot`), overlaid with the candidate's `fork_state` (a value stale at a
mid-session fork is removed, so no refusal is predicted from it). The native prefix and the agent's real continuation
come from the transcript. The spec opts into kami-rules and has no response rows of its own; options map to `rule` and
`lesser`, `zero_if` to `forbid_flags` / `forbid_before` / `forbid`, and 0.15 per refusal to `refusal_penalty`
([v2-miner.md](v2-miner.md) "One scorer"). Tier B items declare `scoring.model` (reported beside the forecast only).
The miner no longer emits `heldout.method: sealed` (the economic families are public since bank rc3); the schema keeps
the method for a future sealed season. Redacted held-out rows are refused.

The 22 kitchen-sink passes the earlier converter reported (every hp-liquidated, levelup-left and quest-left item) came
from unconditional `ok` rows plus free gaps; with kami-rules and the refusal penalty the kitchen sink passes none of
the bank specs (74 in bank v2.0-rc2 and rc3, 88 in rc4, 82 in rc5, 76 in rc6 to rc8, `check_bank`).

**Loading and exit codes** (H review M6). `spec.load` follows a bank root's `manifest.json` (`public[].file`), reads a
suite directory's `*.json` (not `MANIFEST.json`), searches a directory with neither recursively (never `private/`),
and raises when a path yields no spec. `spec check` reports a lazy pass on a smoke or retired item, or on a
verdict-equivalent v1 conversion (`drills_v2/spec`), as `expected` and exits 0 on it; any other failure exits 1.
`kami-drills spec check specs/v1.1 bank/v2.0-rc9 drills_v2/spec` → 109 specs, 0 failing, 14 expected, exit 0.

**Pseudonymization** (`spec/pseudonymize.py`; applied since 2026-11-03, owner decision). Every 40-hex address outside
the allowlist (harness surface, game contracts and tokens, the Palaestra S1 agents' own wallets:
`spec/pseudonym_allowlist.json`) becomes `0x00000000` + 32 hex of an HMAC, and every third-party player account (name
and index in its record) becomes `player-<hmac6>` with an index above 10^7; the same index after an account word in free
text gets the same alias. The drill's own account index, kami and
quest ids stay. Pseudonyms are recognizable, so a second pass is a no-op. It runs where specs are made: `from_v1.convert`
(v1.1, `drills_v2/spec`; public key `V1_KEY`, since the raw text is in the frozen `drills/`) and the miner's split
(bank; a key derived from the split key). By hand: `kami-drills spec pseudonymize PATHS --key-env VAR --out DIR`, which
refuses any spec whose verdicts change.

## 6. Versioning and re-validation

- `id@version` is immutable once released; any rule, response or state change is a new `version`. A result row is
  keyed by (drill `id@version`, `content_hash`, adapter `id@version`, judge hash, model, generation config).
- `spec_version` is semver: MAJOR breaks the schema, MINOR adds an intent, reason or field (`BAD_ARGS` is one, folded
  into the 2.0.0 draft since nothing is released), PATCH is docs.
- **New harness, agent or lens pin:** new adapter module and id (a MAJOR such as 4.7.0 → 5.0.0 is a new adapter
  major). Update the tool → intent table, templates, reason regexes (re-measure OTHER on its error texts) and quirk
  table; run `spec check` with both adapters; write `harness.validated_on` in a new suite version. Drill files are
  never edited in place.
- **Game patch:** diff gdd catalogs and patch notes against every spec's `state_dependencies`; a changed dependency
  means a new `version` or retirement.

## 7. Reporting model × harness

Always report model × adapter × rung. A season's forecast uses only the adapter for that season's live stack;
cross-harness tables are descriptive. A drill counts on an adapter only with status `ok`; `solved_by_harness` is
reported as harness credit, `not_expressible`/`rendering_changed` as not administered. Harness-bound drills are
reported per harness and never pooled.

## Not done

- `schedule_loss`, `horizon`, `budget_pair`, `ev_regret` scorers; the repetition breaker and consecutive-error caps
  (only the 50-call cap and session end are emulated); multi-session horizons.
- Conformance fixtures (`format(normalize(raw)) == raw` over every S1 snapshot) and the live `tools_hash`: the lens
  formatters produce the fields drills read, not byte-identical lens JSON.
- A 5.0.0 adapter, so the invariance check has run against one adapter only.
- The rules library is not the whole game: stamina beyond a single move, room gates, merchant rooms and listing buys
  are gaps.
