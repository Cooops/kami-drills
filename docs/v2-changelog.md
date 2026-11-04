# v2 changelog (since `proposal/v2-season2`)

Everything below is experimental and on the local branch `v2/drills`. v1 is frozen until Season 1 grading:
`drills/`, the v1 loader, `task.py` (judge hash `308c5deb…`) and `kami-drills run` are unchanged.

## Spec and adapter (`spec/v2`, merged)

- `kami_drills/spec/`: the drill-spec schema (2.0.0 draft plus reason code `BAD_ARGS`), load, validate and
  `content_hash`. A simulator over intents: first-match response rows, a simulated clock (`clock_gte` / `clock_lt`),
  state predicates and deltas. `rule` and `partial_credit` scoring reuse `task.rule_failures` unchanged.
- `check.py`: the oracle, null and recorded-mistake plays, the lazy and degenerate battery, and the harness-invariance
  check.
- `adapters/harness_4_7.py`: the Season 1 surface (99 harness + 8 scaffold tools) and a state-aware tool → intent
  table. Answers are 4.7.0-worded, with native text first. A gap returns a neutral harness 503. The adapter adds a
  reason-code table, the `accept_quest` re-accept quirk and an Inspect task.
- Converters: three v1 drills (verdict-equivalent on all 66 recorded rc2 tries) and miner candidates. CLI: `kami-drills
  spec check | from-v1 | from-miner`. Docs: [v2-spec.md](v2-spec.md).

## Miner (`miner/v2`, merged)

- `kami-drills mine`: rebuilds state at any line from the session-start snapshot plus the prefix, with trigger rules,
  value-swing severity, accepted sets and shared gates.
- miner 2.1 answered the first review (E): repeat families fork at the first repeat and need stated intent; first
  free refusals and re-collects are dropped; account-only foods are excluded; a degenerate battery was added; 3900
  completion is unblocked; liquidations are tier B.

## Integration (`v2/drills`, miner 3.0.0, spec kami-rules@1.0.0)

**One scorer.** The spec package is canonical. `miner/score.py` and `miner/items.json` are deleted.
- The miner converts every candidate through `spec/from_miner.py` and gates it with `spec.check`.
- The miner's refusal predictions are now the shared rules library `spec/rules.py` (`world.rules_library`). It covers
  cooldowns, rooms, kami state, starving, kami foods, held items, empty batches, quests (unmet and contradicted),
  token balances, level-up while RESTING, node caps, the room graph and revive methods.
- New scoring fields: `refusal_penalty`, `lesser`, `flag_caps`, `world.native_text`, `heldout.method:
  private_variant`.
- The 22 kitchen-sink passes on converted items came from unconditional `ok` rows plus free gaps. They are now 0.
- `kami_world.json` (items, node caps, quests, room graph) is built from the gdd by `scripts/build_kami_world.py`.

**Review round 2 (F) blockers**
- B1, held-out leaks:
  - stubs are opaque (`h` + HMAC), with labels only;
  - items split by session and group;
  - held-out items are private perturbed variants (`spec/perturb.py`), seeded by a private key;
  - a canary is on every row and spec;
  - `leak_check` uses digit boundaries, scans every public row and the stats, and is tested on mixed output with each
    review route planted;
  - output is written atomically.
- B2, level-up family:
  - level-up is refused unless RESTING;
  - node caps come from the gdd (55 → 15);
  - a trigger is rejected at the cap of the node the kami harvests next, or when the notes say not to level;
  - `always_level` is in the battery;
  - items a constant policy passes need a genuine paired control, gated by `check_bank`;
  - `levelup-left-gpt-6.1-sol-3-s0056-L7` is gone (`correct_play:level_cap_of_its_node`), and its situation is now a
    control.

**Majors**
- Room adjacency for `move_to_room`; onyx, pale-potion and Djed revives.
- Cooldown boundary fitted on S1: accepted from +4 s, full credit from +26 s, credit 0.8 in between, bounded by each
  item's own refusal and acceptance record.
- Trap controls for the sealed families are in the sealed module.
- Tier-A representatives in dedup and caps. Cooldown and room acts must cover every kami in a batch.
- Frozen bank (`bank/v2.0-rc1`, now replaced by rc2, below) with a manifest, per-file hashes and hash-only
  commitments; `bank.verify`.

**Minors**
- Stats are leak-checked before anything is written.
- Intent words use word boundaries.
- Objective-less quests are not competing work, and level-up items take the same competing work as quest items.
- `get_status` reports the simulated clock.
- A recorded mistake must break the rule itself, not only collect refusal penalties.
- Found by the verification pass and fixed: a same-turn repeat counted as "told"; repeat objectives could come from
  the replaced fork line; held-out control variants lost their `trap_control` origin.

**Tier A grown**
- One repeat after an explicit refusal is enough, on a later turn than the refusal (+2 cooldown items).
- Tier-A representative preference.
- Tier A: 9 → 13 (bank rc2: 24 public specs, 6 held out, 44 sealed). A `collect-then-stop@1` family was added and
  then dropped (below).

**Sealing.** The sealed families moved to `kami_drills/miner/sealed/`, which is gitignored and not on the branch.
`miner/sealing.py` is the neutral public loader. Public code and docs carry only generic token and pool mechanics.
`tests/test_sealing.py` hashes every token and token pair of every tracked text file against salted hashes of the
sealed terms (the plaintext list lives in the sealed module), so the guard names nothing.

## v1.1 specs (`v11/specs`, merged; integration step 2)

**Merged.** `specs/v1.1/` is the 47 v1 drills as specs (27 ranking, 10 smoke, 10 retired) with MANIFEST, the generator
`scripts/v11/` and `tests/test_v11_specs.py`. See [v1.1.md](v1.1.md).

**Spec package: the v1.1 "needs from spec"**
- Schema: `role` (ranking / smoke / retired) and `retired_reason`. `validate` requires the reason for a retired item
  and ties role retired to status retired.
- `load` skips a directory's `MANIFEST.json`, so `spec check specs/v1.1` works.
- Buy intents keep the kami index. `newbie_vendor_buy` keeps `kami`. `buy_kami` keeps `kami` (the ids), sets
  `max_unit_price` to total / qty and `max_total_price` to the total. `from_intent` maps a KamiSwap buy with ids back
  to `buy_kami`.
- `check.battery`: adds `collect_all` and `mistake_then_wake`. Its mistake plays fall back to a success fork's
  `is_authored_mistake` play.
- `scoring.timing_item`: a battery pass ending in wait 300 + the mistake is allowed only if the play fails without
  that tail.
- `components[].any_of`: `[{rule, credit}]`; the component earns its weight × the best passing credit.
- Native key `*`: recorded text that any adapter serves verbatim unless the drill is harness-bound. The v1 replay rows
  use it instead of a 4.7.0-named key.
- The game-half generator stays in `scripts/v11/` (it reads source transcripts outside the repo). Early-stop
  emulation, a pre-4.7 adapter and `use_item_batch` counting are on [roadmap.md](roadmap.md).

**Regenerated** with the integrated scorer: `move_to_room` rows carry `hop`, buy rows the index, wake = 0.5 is one
`any_of` component. Counts are unchanged (27 / 10 / 10).
- Every ranking spec fails the full battery.
- `check_bank` over v1.1 plus the bank is clean on every ranking and bank item.
- The Inspect oracle passes 46/46.
- Mechanical equivalence with v1 is 1027/1034 (was 996; the vendor drills now match).
- Rescored rc2 tries: 62 ranking try-verdicts change in 10 drills. The one new change is an off-display vendor buy,
  now a guess.
- Ranking pass rate .519. Point-biserial .172 with 7 reversed (was .166 with 8).
- The refusal penalty is opt-in, and v1.1 does not use it. Applied at 0.15, it would change 81 ranking try-verdicts.

## Final review (H) fixes, bank v2.0-rc2

- **B1** The sealing guard is hash-only (`sealing.TERM_HASHES`, `tests/test_sealing.py`) and scans `.py`, `.md`,
  `.json`, `.jsonl`, `.txt` (spec files without their recorded game text; the vendored surface, frozen `drills/` and
  the v1-derived polluted prefixes are exempt as already-public text). Sealed mistake classes moved into the sealed
  module; trap details and family names are gone from public docs and code.
- **M1** `collect-then-stop@1` dropped: collect banks the bounty and scavenge points without stopping
  (gdd `harvesting.md` Collect, step 5), so both mined items were correct play. Tier A 15 → 13.
- **M2** `perturb.py`: ids renamed at sentence-final periods (not inside decimals), bare dates and block numbers
  shifted, `perturb.survivors` drops any variant that keeps an original id; `tests/test_perturb.py`.
- **M3** quest-left items are never held out (quest ids are not renamed); the residual is documented in v2-miner.md
  "What the perturbation does not hide". Oracle-on-variant recovery: 0 of 5 non-control held-out items.
- **M4** private mining outputs (`*.heldout.jsonl`, `*.sealed.jsonl`, `*.unsplit.jsonl`, `*.rejections.jsonl`) are
  gitignored.
- **M5** `kami-drills spec pseudonymize` (opt-in, keyed, verdict-preserving; `tests/test_pseudonymize.py`). Not applied.
- **M6** `spec.load` follows a bank manifest and raises on zero specs; `spec check` exits 0 on declared smoke/retired/
  v1-equivalent lazy passes and 1 on anything else.
- **Minors** v1.1 cooldown timing items refuse the first 4 s past `cooldown_until` (S1 calibration; v1.1.md
  Caveats); the cooldown-repeat situation says "acting after a cooldown refusal"; `KAMIARENA_S1` replaces a home-path
  default in a test and v1.1.md names no local paths; dormant miner families are documented; doc drift fixed.
- **Bank** `v2.0-rc2` replaces rc1 (in history only). 74 specs, `check_bank` 0 failures, kitchen sink 0/46, leak
  check clean, `bank.verify` [].

## Owner decisions (2026-11-03), miner 3.1.0, bank v2.0-rc3

**1. The economic families are published.** discovery-swap@1 (ETH → Ether Shard by `portal_deposit` → MUSU by
`pool_swap`), stuck-broke@1 (every kami dead and broke, convertible ETH idle) and reckless-convert@1 (genuine traps), with
their synthetic trap controls.
- The rules moved from the gitignored `kami_drills/miner/sealed/` into `miner/rules.py`, the trap controls into
  `miner/controls.py`, and the sealed test into `tests/test_miner.py`. Their mistake classes are in
  `from_miner.MISTAKE`.
- They split like every other family: public specs, private perturbed held-out variants under the split key.
- Gone: `miner/sealing.py` and its hash-only term guard (`tests/test_sealing.py`), the `*.sealed.jsonl` output, the
  bank's sealed commitment and `private/sealed.jsonl`, the gitignore entries, and "a session holding a sealed item is
  never public". Kept: the held-out machinery. The schema keeps `heldout.method: sealed` for a future season, though
  the miner no longer emits it.
- The leak check no longer counts a token that is a public item's own answer: a fixed revive method, a trap's
  template situation. These are family vocabulary, not a private item's answer (both tripped it once the families
  split).

**2. Public specs are pseudonymized** (`spec/pseudonymize.py`, now applied where specs are made):
- v1.1 and `drills_v2/spec` through `from_v1.convert`, under the public key `V1_KEY`. Their raw text is in the frozen
  public `drills/`, and a clean clone can rebuild and test them.
- The bank through the miner's split, under a key derived from the split key. Bank aliases cannot be linked to v1.1
  aliases, and so not to `drills/`.
- Kept as recorded: game contracts and tokens, and the 54 Palaestra S1 agent owner and operator wallets
  (`spec/pseudonym_allowlist.json`; 45 appear in rc3).
- Pseudonyms start `0x00000000`, and players become `player-<hmac6>` with an index above 10^7, so a second pass is a
  no-op. Non-ASCII names are also matched in their JSON-escaped form.
- `tests/test_pseudonymize.py` checks that every committed spec has no raw third-party wallet or player record left.
- v1.1 rebuilt with `scripts/v11/run.py`. `spec check` verdicts, adapter status and the `rescore.py` output are
  identical to before, and v1.1 still agrees with v1 on **1027/1034** recorded tries (`eq.py`).

**Bank `v2.0-rc3`** replaces rc2 (in history only; same 27 runs, 1,924 sessions).
- 46 items (13 tier A, 33 tier B), 5 paired controls and 23 trap controls: 74 specs.
- 54 public (15 tier A, 39 tier B) and 20 held out (12 items, 1 control, 7 traps).
- `check_bank` (v1.1 + public + private) has 0 failures on any ranking or bank item. The kitchen sink passes 0 of
  the 54 public specs; every lazy and degenerate play passes 0/22 ungrouped items and 0/26 groups over half.
- Economic pairs: null 0.50, blind ≤ 0.10, state reading 1.00. Oracle-on-variant recovery: 0 of 12 non-control
  held-out items.
- Leak check clean, `bank.verify` []. Output `sha256[:16] 2065756e909d6639`, identical for `--jobs 1` and 10.

**Shortcut debt (not blockers)**: `kami_drills/scoring_v2.py` single-run model variance and per-model percentile (not
Hsu); `kami_drills/spec/__init__.py` four scorers not implemented; `kami_drills/spec/rules.py` travel partial result
on low stamina; `kami_drills/task.py` (frozen v1, left as is).

## Owner-approved additions (2026-11-03), miner 3.2.0, bank v2.0-rc4

**1. Quest 3039 un-rejected and room-aware** (`rules.contradicted`, `rules.quest_room_plan`).
- Root cause, from the transcript (gpt-6.1-sol-3 s4): the agent called `accept_quest(3039)` in room 4 and
  `complete_quest(3039)` in the very next call. The dry run reverted because the chain had not caught up with the
  accept. The check then said completable, and the agent left for room 5 and never retried. opus-5.5-1 s27 hit the same
  revert and completed on its retry.
- A refusal right after a successful accept, with a later completable check in the same session, is now chain lag.
  Other refused completions still reject (`tests/test_miner.py::test_quest_contradicted_by_a_refused_completion_is_rejected`).
- The 4.7.0 `completable-ignores-room` quirk note now records this root cause.
- In room 4 the answer is complete now. Elsewhere it is travel to room 4 and complete. Completing from where the account
  stands is accepted too: 3039's room requirement is on the accept, and S1 completed it from rooms 10, 33 and 62.
- Deferring is fair when the round trip costs more than the reward's 80 stamina, or when a kami needs urgent care
  (dead, or harvesting under 25% HP). Those triggers are rejected and counted (14 for urgent care, 0 for stamina in S1).
- The signature adds `in_room` / `elsewhere`. The 122 elsewhere triggers dedup to one item (gpt-6.1-sol-3 s5), and the
  in-room ones to one more (gemini-4-argon-1 s5).
- The per-model cap now drops gemini-4-argon-1 s51's quest-3900 item: the model has four quest-left items against a
  cap of three. quest-left stays at 6 items.

**2. node-choice@1** (tier A). A harvest started where a node within three ungated moves pays ≥ 1.5x and ≥ 5 MUSU/h
more for these kamis. It uses the gdd efficacy formula, with intensity over 12 h doubled, and checks stamina and level
caps. The rejections are listed in [v2-miner.md](v2-miner.md) "Node choice". 25 gated triggers give 6 items (4
public, 2 held out). `kami_world.json` gains node affinities, yield items, gated rooms and each quest's tied nodes and
items. It is rebuilt from the same gdd pin, and every existing field is unchanged.

**3. market-price@1** (tier B), with trap controls. A kami food bought when the same merchant's read at the fork sold
the same HP for ≤ 1/1.5 of the MUSU (GDA batches) and ≥ 30 MUSU less. 8 gated triggers give 4 items, each paired with a
trap: the read rewritten so the agent's food is the cheap one. The family is never held out, because item ids are not
renamed and an original's oracle passed its variant.

Pool timing is not mined. At a fork the pool price is the price available, so a better price is past or future. The
agents' only other venue, the order book, was worse in every read. The indexer has no S1 trade history (`ecs.trades`
ends 2026-04-09) and only daily pool aggregates.

**Battery.** start here, highest-index, highest-cap and nearest node (node-choice drills); always-buy, buy-the-cheapest
and always-sell (market drills). These run only on drills that declare the matching constants, so v1.1 and
`drills_v2/spec` verdicts are unchanged.

**Bank `v2.0-rc4`** replaces rc3 (in history only; same 27 runs, 1,924 sessions).
- 56 items (19 tier A, 37 tier B), 5 paired controls and 27 trap controls (21 gas, 4 merchant-price, 2 genuine): 88
  specs.
- 66 public (19 tier A, 47 tier B) and 22 held out (14 items, 1 control, 7 traps).
- `spec check specs/v1.1 bank/v2.0-rc4`: 111 specs, 0 failing. The kitchen sink passes 0 of the 66 public specs, and
  every lazy and degenerate play passes 0/28 ungrouped items and 0/30 groups over half.
- Economic pairs: null 0.50, kitchen sink 0.00, token move 0.08, always-revive 0.09. The market pairs: always-buy 0.00,
  buy-the-cheapest and always-sell 0.50. State reading 1.00.
- Oracle-on-variant recovery: 0 of 14 non-control held-out items. 48 S1 agent wallets appear as recorded, and no raw
  third-party wallet does.
- Leak check clean, `bank.verify` []. Output `sha256[:16] 480fa57f793442e6`, identical for `--jobs 1` and 10.

## Review of rc4 (FIX-FIRST), miner 3.3.0, bank v2.0-rc5

**M1. Pseudonymization was incomplete.**
- The text fallback for truncated tool results only matched records that start with `{"index"`, so records that lead
  with their `id` were missed. That left 39 player records in two discovery-swap specs, and hundreds in v1.1 and
  `drills_v2/spec` (the owner's handle among them, next to its alias).
- The fallback now also matches `id`-led records under any key.
- A decimal 160-bit account id gets its wallet's alias as an integer, and a wallet truncated to 36-39 hex gets a
  same-length pseudonym. The S1 agent wallets stay as recorded.
- v1.1 and `drills_v2/spec` were rebuilt (`scripts/v11/run.py`, `spec from-v1`). Every play and battery verdict is
  unchanged on all 50 specs.
- `tests/test_pseudonymize.py` now uses a detector independent of the tool:
  - when the transcripts are next to the repo (a local Palaestra S1 runs directory, `$KAMI_DRILLS_S1_RUNS`), it harvests player
    records from them and finds 0 left in any committed spec;
  - otherwise it falls back to fixed regexes.
- The docs state that `V1_KEY` is public.

**M2. market-price-grok-4-7-3-s0038-l64 had the wrong answer** (25/70 HP: a 100-HP cookie gives at most 45). Usable HP
now counts each unit as min(food HP, cap), under both the largest missing HP at the fork and the largest max HP. The
item is gone. The family keeps 2 items, from gemini-3.6-flash-1 s55 and gemini-4-argon-3 s119.

**M3. node-choice-haiku-5-5-1-s0027-l36 is now rejected** (`stated_plan:quest_or_scavenge`: its prose named the
liquidation plan; corrected in rc6). The new scouting rule (`correct_play:liquidation_quest_scouting`: an open
liquidation quest and the node read as an attacker) rejected gpt-6-luna-1 s9. Scavenge, liquidation and objective reasons are now looked for in all of
the session's prose up to the fork.

**M4. Gaming.**
- **node-choice:** each item pairs with a control where moving is wrong (a real start from the run, with every node
  in reach paying ≤ 1/1.2). `body_match_node` and `hand_match_node` are in the battery. On rc5 the reviewer's policy
  scores 1.0 on the item and 0.0 on its control (pair mean 0.50).
- **market:** the traps forbid every food dearer per usable HP than the agent's own, and credit is capped at 2x the
  needed units. `bulk_cheapest` (60 of the cheapest unit price) is in the battery. On rc5 it scores 0.0 on both items
  and 1.0 on both traps (pair mean 0.50).
- 2 node-choice items found no control and were dropped.

**Minor.**
1. The fork state takes the latest in-prefix HP and level over the snapshot, and level-ups update the trace's level.
2. Quest 3039 completes in place everywhere, and travelling first is accepted. The stamina and urgent-care rejections
   are gone, because completing is one free call. The in-room / elsewhere signature split is gone too. quest-left keeps
   6 items: 3039 (gpt-6.1-sol-3 s5, 139 triggers) and 3900 (gemini-4-argon-1 s51, back under the per-model cap).
3. The lag rule needs a successful completion of the quest somewhere in S1. On S1 it un-rejects only quest 3039: 58
   gpt-6.1-sol-3 sessions (the verifier's count; corrected in rc6, where 56 counted only the session starts with a
   completable check).
4. The docs now separate item counts from spec counts. Dual-affinity nodes are skipped: `LibAffinity` compares one
   string, while the gdd takes the better pairing. No rc4 or rc5 node-choice item used one.

**Bank `v2.0-rc5`**, replacing rc4 (in history only).
- 50 items (15 tier A, 35 tier B), 7 controls and 25 trap controls: 82 specs.
- 60 public (17 tier A, 43 tier B) and 22 held out (13 items, 2 controls, 7 traps).
- `check_bank` over v1.1, the public specs and the private variants: 0 failures. Kitchen sink: 0 of 60. Every play
  passes 0/22 ungrouped items and 0/30 groups over half.
- Oracle-on-variant recovery: 0 of 13. 47 S1 agent wallets appear; no raw third-party wallet, decimal id or player
  record does.
- Output `sha256[:16] 1d77e73d4d49d4f3`, identical for `--jobs 1` and 10.

## Verifier of rc5 (FIX-FIRST), miner 3.4.0, bank v2.0-rc6

**1. node-choice dropped from the bank** (owner decision). Both rc5 items were short harvests made on purpose to
finish quest 4 "What Kami Do: Harvesting" (a harvest-time quest with no node). "Go to the nearest body-match node,
the current one included" scored 1.0 on both halves of the pair.
- The rule stays, off by default: `--experimental node-choice`.
- The quest-tie gap is fixed anyway: a harvest-time quest with no node ties every node (`quests[].harvest_any_node`).
  The session's notes and `end_session` reason are scanned for the plan too.
- With the fixes, S1 gives 0 node-choice items. The researched method and its stats are in [v2-miner.md](v2-miner.md),
  "Node choice (researched, not shipped)".

**2. market-price.**
- An open quest that counts MUSU spent (item 1) or shop buys now rejects. 2002 "Spend 1000 MUSU at Mina's Shop" made
  gemini-3.6-flash-1 s55 correct play. 3 triggers are rejected this way.
- Credit now runs from the right-sized heal (filling the kami missing most HP) up to 2x the largest need. The
  verifier's "cheapest per usable HP" policies then score 1.0 on the pair. They read the prices and the HP, which is
  the skill tested, so this is the answer and not gaming. The blind market plays stay at or under 0.50.
- Fork MUSU now comes from the latest in-prefix read, with a swap's own MUSU side applied. (rc6 said 1,774 for
  gemini-4-argon-3 s119; that missed a 461-MUSU collect payout, and the value is 2,235: corrected in rc7.)
- Foods stay fresh-only. Refreshing them from a stale read missed foods bought since, and made a valid cooldown-repeat
  oracle (sonnet-5.5-1 s18) refuse a feed.
- One item is kept: argon-3 s119 with its trap, tier B. opus-5.5-3 s46 came back under the new credit range, but
  buy-the-cheapest then passes both halves of its pair, so check_bank drops it.

**3. Names in free text** (`spec/pseudonymize.py`; the hash file `spec/pseudonym_names.json` was removed in rc7). The rule is in
[v2-miner.md](v2-miner.md), "Bank", "Names in free text".
- Harvested S1 player names (committed as salted hashes in rc6; built at mine time from rc7), the account index beside them, and the drill's own name
  are replaced at any escaping depth. The drill's own name is the owner's handle in v1.1.
- Plain-word names ("today", "Bramble") are replaced only in name contexts.
- S1 agent names stay.
- v1.1 and `drills_v2/spec` were rebuilt, and every verdict is unchanged.
- The tests grep all free text against the harvested names.
- `drills_v2/polluted/` is outside scope: it is identical to the public base it was built from (`drills/`).

**4. Docs.** The haiku s27 reason and the lag-rule count in the rc5 section are corrected above.

**Bank `v2.0-rc6`**, replacing rc5 (in history only).
- 47 items (13 tier A, 34 tier B), 5 controls and 24 trap controls: 76 specs.
- 56 public (15 tier A, 41 tier B) and 20 held out (12 items, 1 control, 7 traps).
- `check_bank` over v1.1, the public specs and the private variants: 0 failures. Kitchen sink: 0 of 56. Every play
  passes 0/22 ungrouped items and 0/27 groups over half.
- Oracle-on-variant recovery: 0 of 12. Output `sha256[:16] 073e2db24c964055`, identical for `--jobs 1` and 10.

## Verifier of rc6 (two blockers), miner 3.5.0, bank v2.0-rc7

**F1. The free-text pass corrupted ordinary text.** Plain or short names matched ordinary tokens: "account 'main'"
(12 times in the bank, 57 in v1.1, 4 in `drills_v2/spec`), "All 1 kami(s)" (8 of 9 public stuck-broke specs),
"time windows", "level-1", "node 6".
- Numerals, tokens of 2 characters or fewer and harness/game/tech words are never names now
  (`pseudonymize.real_name`, `VOCAB`).
- The "<name> kami" context needs a real name, and harness refusal templates (`world.native_text`) are out of the pass.
- New tests: no alias on `account '<label>'`, `All player-`, `player-… kami(s)`. Every v1.1 native prefix differs from
  its v1 drill only at names, their indices and wallets or ids. The private held-out variants were checked too
  (0 of 20 hit, nothing printed).

**F2. The market pair was gameable on sticker price.** "Buy 5 or 10 of the lowest-priced food" scored 1.0/1.0.
- When the item's answer is its lowest sticker, the trap now keeps the smallest food the lowest sticker (11301 at 62
  against 11303 at 83) while making it a ≥ 1.5x overpay for the same usable HP (GDA batch costs).
- Traps forbid only foods that cost ≥ 1.5x the agent's, and the text says so.
- `sticker_lo` and `sticker_hi` are in the battery.
- On rc7 the verifier's sticker policies score 0.50 on the pair.

**F3.** Fork MUSU now applies collect, stop and claim payouts made after the latest read. argon-3 s119 is 2,235.

**F4.** No name hashes are committed. The season's names are built at mine time from the transcripts and kept in
memory. The tests harvest them when the transcripts are present (a local Palaestra S1 runs directory, `$KAMI_DRILLS_S1_RUNS`);
otherwise committed specs are checked by the fixed regexes.

**F5.** Credit is capped at 2x the right-sized heal, matching the doc. The heal passes; 3 cookies where 1 heals fails.

**F6.** Account ids truncated to 16-35 hex in an `id` field are aliased (`stale-double-complete-quest`).

**Bank `v2.0-rc7`**, replacing rc6 (in history only). Same membership: 47 items, 5 controls, 24 traps.
- 56 public (15 tier A, 41 tier B), 20 held out.
- `check_bank` 0 failures. Kitchen sink 0 of 56. 28 plays, each 0/22 ungrouped and 0/27 groups over half.
- Oracle-on-variant 0 of 12. Output `sha256[:16] 87186e3c1421a44f`, identical for `--jobs 1` and 10.

## Pre-publication cleanup of rc7, miner 3.6.0, bank v2.0-rc8

1. **The name pass left non-names alone again.**
   - A player named "0x00" had turned 16 `maxPriorityFeePerGas {"hex":"0x00"}` into an alias in
     `specs/v1.1/repeat-quest-prereq-locked.json`. Hex and number literals are never names now.
   - Quest 1's registry record ("Welcome to Kamigotchi World") shares index 1 with that drill's own account, and was
     taken for the drill's own name at 3 sites in `drills_v2/spec/repeat-pool-swap-retry.json`. The own name now comes
     only from an account record (not one with a description, type, rarity or objectives), and quest, item and node
     names are never names.
   - v1.1 and `drills_v2/spec` were rebuilt; every verdict is unchanged.
   - `test_v1_specs_change_only_names_and_wallets` now covers `drills_v2/spec` too. It aligns every alias site: each
     alias is the V1_KEY alias of a recorded name, and a record name that `real_name` rejects is aliased at its record
     sites only. It fails on the rc7 files (17 vs 1 and 6 vs 2 sites) and passes on rc8.
2. **Market fork state.** Stamina comes from the latest in-prefix read or travel (`stamina_remaining`, minus later
   moves): argon-3 s119 shows 50, not 100, on both the item and its trap. A kami's bounty comes from the latest read
   and is 0 after a collect, stop or start.
3. **Trap margin and wording.** The trap's 1.5x margin is measured at the right-sized heal. Its forbid list is scoped
   to the merchant that was read. The doc says how the credited range starts.

**Bank `v2.0-rc8`**, replacing rc7 (in history only). The bytes changed (stamina, bounty, the trap), but the
membership did not: 47 items, 5 controls, 24 traps.
- 56 public (15 tier A, 41 tier B), 20 held out.
- `check_bank` 0 failures; kitchen sink 0 of 56; 28 plays, each 0/22 ungrouped and 0/27 groups over half.
- Oracle-on-variant 0 of 12; the private variants hit no broken-alias pattern.
- Output `sha256[:16] 2a23b0a148d55cdc`, identical for `--jobs 1` and 10.

## Skill-area profiles (owner-approved 2026-11-04 UTC)

- `kami-drills profile` and `kami_drills/profile.py`: per-model scores for survival, economic, progression, cadence
  and recovery (mapping: `kami_drills/skill_areas.json`, one primary area per drill, nine v1.1 drills moved off their
  v1 category where the rule rewards another area; a test checks areas against the competency tags), each with n and a
  deterministic bootstrap 95% CI over its drills. The headline equal-weight aggregate counts only areas with n >= 5;
  equal-weight over all areas and the drill-weighted mean are reported beside it. Thin areas (n < 5) get no CI;
  controls, traps, tier B, smoke and retired drills get no score.
- On the rc2 tries under v1.1 (sensitivity): survival has 2 drills and economic 3, both thin; cadence separates models
  best; mean area-to-area r +.15 ([v2-profile.md](v2-profile.md)).
- `from_v1.equivalence` rows now also carry the spec score.

## Calldata wallets (PR #3 blocker)

- A third-party wallet survived 552 times as a `0{24}`-padded 32-byte word inside tx `data` hex (7 v1.1 specs and
  `drills_v2/spec/stale-cooldown-retry-start`), next to its aliased `0x` form. `pseudonymize` now aliases padded
  address words in `0x` hex data (after a 4-byte selector, or bare words) with the `0x` form's alias. A word counts as
  an address only when its 40-hex tail has a nonzero byte in its top 4 bytes, so no uint below 2^128 changes and
  pseudonyms are never re-aliased. A decimal account id cut to 40-45 digits by a truncated result is aliased too.
- v1.1 rebuilt (`scripts/v11/run.py`), `drills_v2/spec` refreshed (`spec from-v1`): only those substitutions and the
  content hashes changed. `spec check` 0 failing; `eq.py` 1027/1034. Bank rc8 had no such calldata (unchanged).
- Not pseudonymized, as before: the frozen `drills/`, `defs/` and the v1-format `drills_v2/polluted/` variants.

## Open questions for the owner

1. ~~Publish the sealed families?~~ **Decided 2026-11-03: published** (bank rc3, above). Still push only the single
   squashed commit on `origin/proposal/v2-season2`, by explicit refspec: the local working branches carry history that
   is not meant for the remote.
2. **Split key.** `.split-key` in the worktree (gitignored) seeds the split and the variants. Losing it means the
   held-out set cannot be regenerated, only read from `private/`.
3. **Held-out policy.** Private perturbed variants, as implemented, or embargo the held-out source transcripts?
4. ~~Personal data in public specs?~~ **Decided 2026-11-03: pseudonymized** (v1.1, `drills_v2/spec`, bank rc3; S1
   agent wallets kept). Free text (agent prose, kami names) is as recorded.
5. **Controls.** They test only that a blind act is avoided. Should they count toward a family score, or gate it only
   (as now)?
6. ~~Quest 3039 (gpt-6.1-sol-3)?~~ **Decided 2026-11-03: chain lag after the accept**, un-rejected and room-aware
   (bank rc4, above).
7. **v1.1 and the refusal penalty.** v1.1 keeps v1's replay rows and does not opt into `refusal_penalty`. Opting in
   (0.15) changes 81 ranking try-verdicts on the rc2 tries, and the battery gate still holds. Decide before a v1.1
   administration.
