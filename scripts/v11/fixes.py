"""kami-drills v1.1: the B-audit fixes as spec edits on top of the mechanical v1 conversion (conv.overlay).
Each drill gets a role (ranking / smoke / retired), an explicit stated objective (decision_point.closing), and its fix."""
import copy

import conv
from kami_drills.spec.rules import ACCEPT_FROM_S

W = lambda verb, **a: {"verb": verb, **({"args": a} if a else {})}
WAKE = {"verb": "schedule_wake", "args": {"minutes": {"gte": 5}}, "ok": True}
END = {"verb": "end_session", "ok": True}
LUNA_GOAL = "maximize banked MUSU over the season while keeping kami 21340 alive; it is your only kami"

RETIRED = {
    "onboard-travel-not-move": "B audit (c): the goal room is inferred from a dry run and the live agent's next call, never stated; a mistake-only rule still reverses (r -.38). Needs a re-fork where the agent has written the goal.",
    "levelup-not-resting": "B audit (c): the level-up has no stated purpose, the prefix shows no XP and the revert says nothing about whether a level-up is possible; the rescored rule still reverses (-.21).",
    "stale-levelup-while-harvesting": "B audit (a)+(b): the kami is run by a Kamibots strategy whose tools are hidden in Season 1, XP is never shown, quest checks are gapped in 14/22 tries; 0% pass.",
    "memory-sonnet-slow-points-belief": "B audit (a): every try banks the points; the verdict is decided by a note path/phrasing regex. Behaviour-only scores 100%. Rebuild as a v2 memory drill with a structured note.",
    "onboard-fund-amount": "B audit: 100% pass, no information.",
    "risk-dying-kami-remote": "B audit: 100% pass, no information.",
    "stale-feed-from-shop-room": "B audit: 100% pass, no information.",
    "stall-no-kami": "B audit: 100% pass, no information (and its oracle buy_kami is not expressible on kh-4.7.0: the adapter drops kami_ids).",
    "stale-harvest-start": "B audit: passes on a wake alone and sits at 95%.",
    "stale-levelup-twice": "B audit: passes on a wake alone and sits at 95%.",
}
SMOKE = {
    "stale-travel-before-cooldown-stop": "B audit: with the accepted set {wait -> stop (-> travel), keep harvesting + wake} it scores 95%.",
    "memory-sonnet-zero-hp-is-dead-belief": "B audit: 95% pass.",
    "onboard-complete-before-accept": "B audit: 95% pass.",
    "prog-gpt52-complete-when-met": "B audit: 95% pass.",
    "repeat-quest-prereq-locked": "B audit: 95% pass (and a wake is the correct answer).",
    "risk-starving-feed-first": "B audit: 95% pass; the answer is 'feed' (always-feed passes).",
}
# downgraded by the v1.1 battery gate (docs/v1.1.md section 3): ranking drills where a degenerate play passes and no rule fix is sound
GATE_SMOKE = {
    "prog-gpt52-locked-quest-chain": "battery gate: the native batch play 'harvest_collect every kami, end_session' passes. The answer is to collect the only kami, so no rule separates the skill (leave the locked quests alone) from the reflex.",
    "prog-sonnet-unspent-skill-points": "after aligning with the bank-is-valid audit (any point spent passes) the recorded tries pass 21/22: at ceiling, so a smoke test like the other 95% items.",
    "risk-unmanaged-lowhp": "battery gate: always_stop passes. The answer is to stop the 17% HP kami first, so the reflex 'stop every harvest, end' is the oracle.",
    "risk-lowhp-feed-first": "battery gate: always_feed passes. The answer is to feed first, so the reflex 'feed every kami with every item' is the oracle (the same reason risk-starving-feed-first is a smoke test).",
}

# explicit stated objective: shown to the model as the closing user turn, never the move
OBJ = {
    "levelup-not-resting": "Operator goal: get kami 9811 to its next level.",
    "memory-luna-levelup-needs-no-points-belief": f"Operator goal: {LUNA_GOAL}.",
    "memory-luna-plan-level-after-rest": f"Operator goal: {LUNA_GOAL}. Carry out the plan in plan.md.",
    "memory-sonnet-avoid-registry-dump": "Operator goal: keep this session's quest checks cheap, record what you learned in notes.md, and close the session.",
    "memory-sonnet-slow-points-belief": "Operator goal: progress the scavenge tiers at node 3.",
    "memory-sonnet-stamina-unreliable-belief": "Operator goal: continue the trip you were making; complete as many quests as possible.",
    "memory-sonnet-zero-hp-is-dead-belief": "Operator goal: keep kami 20699 earning; do not lose it.",
    "onboard-bridge-pending": "Operator goal: get the operator wallet funded on Yominet so onboarding can continue.",
    "onboard-complete-before-accept": "Operator goal: finish quest 2 and get quest 3 'Your First Kami' accepted.",
    "onboard-first-kami-vendor": "Operator goal: get your first kami and finish quest 3 'Your First Kami'.",
    "onboard-fund-amount": "Operator goal: get quest 1 accepted.",
    "onboard-operator-unfunded": "Operator goal: get quest 1 accepted.",
    "onboard-quest2-move-three": "Operator goal: finish quest 2 and get quest 3 accepted.",
    "onboard-travel-not-move": "Operator goal: reach room 11.",
    "prog-gpt52-complete-when-met": "Operator goal: bank quest progress you have earned; complete as many quests as possible.",
    "prog-gpt52-locked-quest-chain": "Operator goal: progress quest 12 and the chain behind it.",
    "prog-luna-banked-xp": f"Operator goal: {LUNA_GOAL}.",
    "prog-luna-two-levels-pending": f"Operator goal: {LUNA_GOAL}.",
    "prog-sonnet-unspent-skill-points": "Operator goal: make kami 3149 as strong a harvester as you can.",
    "prog-timewall-plan-stop-complete": "Operator goal: complete quest 20.",
    "prog-timewall-quest19-to-20": "Operator goal: complete quest 19, then get quest 20's 12-hour harvest at node 37 running safely.",
    "repeat-collect-too-far-stop": "Operator goal: bank kami 17194's accrued harvest.",
    "repeat-cooldown-feed": "Operator goal: get kami 20375 back to earning.",
    "repeat-cooldown-start-wait": "Operator goal: get kami 9787 harvesting at node 29 again.",
    "repeat-listing-buy-underflow": "Operator goal: buy kami food from Mina.",
    "repeat-newbie-vendor-guess": "Operator goal: get your first kami.",
    "repeat-pool-swap-retry": "Operator goal: get Scrap Metal for your quest, or make other progress if you cannot.",
    "repeat-quest-prereq-locked": "Operator goal: get quest 8 accepted when it can be.",
    "repeat-stop-cooldown-identical": "Operator goal: stop kami 17194's harvest.",
    "repeat-stop-starving": "Operator goal: stop kami 703's harvest and bank its MUSU.",
    "repeat-take-trade-underflow": "Operator goal: get at least 3 Scrap Metal for your quest within your 625 MUSU, or make other progress if you cannot.",
    "risk-dying-kami-remote": "Operator goal: do not lose kami 20699.",
    "risk-lowhp-feed-first": "Operator goal: keep kami 17194 alive and earning.",
    "risk-revive-then-heal": "Operator goal: get kami 715 back to harvesting without losing it again.",
    "risk-starving-feed-first": "Operator goal: recover the MUSU kami 21340 has accrued.",
    "risk-unmanaged-lowhp": "Operator goal: keep kami 3149 alive; then progress your quests.",
    "stale-cooldown-retry-start": "Operator goal: get kami 9787 harvesting at node 29 again.",
    "stale-double-complete-quest": "Operator goal: move on to the next quest in the chain.",
    "stale-feed-from-shop-room": "Operator goal: feed kami 21340.",
    "stale-harvest-start": "Operator goal: keep kami 9811 earning.",
    "stale-harvest-start-wrong-room": "Operator goal: get kami 17194 harvesting at node 3.",
    "stale-levelup-twice": "Operator goal: finish kami 21340's level-up.",
    "stale-levelup-while-harvesting": "Operator goal: level up kami 3149 for quest 3003.",
    "stale-notes-resting-vs-harvesting": "Operator goal: move kami 17194's harvest to node 2.",
    "stale-notes-wrong-node-stop": "Operator goal: stop kami 17194's current harvest.",
    "stale-travel-before-cooldown-stop": "Operator goal: move kami 715 to a harvest in room 12 without stranding it.",
    "stall-no-kami": "Operator goal: get your first kami.",
}

# authored mistakes where the live agent did the right thing at the fork (success forks: mistake_class none)
SUCCESS_FORK = {
    "memory-sonnet-zero-hp-is-dead-belief": [W("revive", kami=20699, method="onyx")],
    "onboard-complete-before-accept": [W("accept_quest", quest=3)],
    "onboard-first-kami-vendor": [W("complete_quest", quest=3), W("buy", item="kami", qty=1, venue="newbie_vendor", max_unit_price="0.004")],
    "prog-gpt52-complete-when-met": [W("complete_quest", quest=6)],
    "prog-timewall-plan-stop-complete": [W("complete_quest", quest=20), W("end_session")],
    "risk-revive-then-heal": [W("revive", kami=715, method="red_ribbon_gummy"), W("harvest_start", kami=715, node=25)],
    "risk-unmanaged-lowhp": [W("scavenge_claim", node=30), W("end_session")],
    "memory-luna-plan-level-after-rest": [W("level_up", kami=21340)],
    "memory-sonnet-stamina-unreliable-belief": [W("travel", room=30)],
    "repeat-stop-starving": [W("harvest_stop", kami=703)],
    "levelup-not-resting": [W("level_up", kami=9811)],
    "memory-sonnet-slow-points-belief": [W("schedule_wake", minutes=600)],
    "risk-dying-kami-remote": [W("feed", kami=20699, item=11301)],
}
# the source agent's actual next calls, where the first post-fork turn is not the mistake
RECORDED = {
    "memory-luna-levelup-needs-no-points-belief": [W("harvest_start", kami=21340, node=29), W("schedule_wake", minutes=240)],
    "onboard-bridge-pending": [{"verb": "quote", "args": {"of": "bridge", "amount_eth": "0.01"}, "dry_run": True}, W("bridge", amount_eth="0.01")],
    "onboard-operator-unfunded": [W("accept_quest", quest=1)],
    "onboard-fund-amount": [W("bridge", amount_eth="0.01")],
    "prog-luna-banked-xp": [W("schedule_wake", minutes=240)],
    "prog-luna-two-levels-pending": [W("schedule_wake", minutes=260), W("end_session")],
    "prog-sonnet-unspent-skill-points": [W("end_session")],
    "prog-timewall-quest19-to-20": [W("harvest_stop", kami=20699), W("complete_quest", quest=19), W("accept_quest", quest=20),
                                    W("travel", room=37), W("wait", seconds=150), W("harvest_start", kami=20699, node=37)],
    "repeat-newbie-vendor-guess": [W("buy", item="kami", qty=1, venue="newbie_vendor", max_unit_price="0.005", kami=2)],
    "risk-lowhp-feed-first": [W("read", kind="get_scavenge_points", node=6), W("note_read", path="reference/catalogs/rooms.csv")],
    "stale-notes-resting-vs-harvesting": [W("harvest_start", kami=17194, node=2)],
}


def plays(spec):
    return {p["name"]: p for p in spec["scoring"]["reference_plays"]}


def set_play(spec, name, intents, expected, mistake=False, authored=False):
    rp = [p for p in spec["scoring"]["reference_plays"] if p["name"] != name]
    p = {"name": name, "intents": intents, "expected": expected}
    if mistake:
        p["is_recorded_mistake"] = True
    if authored:
        p["is_authored_mistake"] = True
    spec["scoring"]["reference_plays"] = rp + [p]


def drop_play(spec, name):
    spec["scoring"]["reference_plays"] = [p for p in spec["scoring"]["reference_plays"] if p["name"] != name]


def kidx(spec, kami):
    return next(i for i, k in enumerate(spec["state"]["kamis"]) if k["index"] == kami)


def wake_half(spec, acts, gate=None, note=""):
    """A wake (or a clean end) earns 0.5 where an in-session act is strictly better; the act earns 1."""
    sc = spec["scoring"]
    old = sc.get("rule", {})
    sc.update(kind="partial_credit", pass_threshold=1,
              components=[{"id": "act_or_wake", "weight": 1, "note": "no blind retry, and the session acts (1) or moves on with a wake/end (0.5)",
                           "any_of": [{"rule": {"require_any": acts}, "credit": 1, "note": note or "the in-session act that beats waking"},
                                      {"rule": {"require_any": [WAKE, END]}, "credit": 0.5}]}])
    sc["rule"] = old if gate is None else gate


def cooldown(spec, kami, seconds, acts=("harvest_start", "harvest_stop", "harvest_collect", "feed"), flag="early_retry", note=""):
    """Every act on a kami during its cooldown is refused by the chain ('kami on cooldown'); acting then is the drill's mistake."""
    until = spec["fork"]["clock"]["unix"] + seconds
    spec["state"]["kamis"][kidx(spec, kami)]["cooldown_until_unix"] = until
    rows = [{"match": {"verb": list(acts), "args": {"kami": kami}}, "clock_lt": until, "sets": flag, "provenance": "rule",
             "result": {"class": "revert", "reason": "COOLDOWN"}},
            # S1 calibration (spec/rules.py ACCEPT_FROM_S): the chain still refused until ~4 s past cooldown_until; a
            # refusal there is not flagged (retrying is fair), and no act scores before it
            {"match": {"verb": list(acts), "args": {"kami": kami}}, "clock_gte": until, "clock_lt": until + ACCEPT_FROM_S,
             "provenance": "rule", "result": {"class": "revert", "reason": "COOLDOWN"}}]
    spec["responses"] = rows + spec["responses"]
    spec["state_dependencies"].append({"path": f"/state/kamis/{kidx(spec, kami)}/cooldown_until_unix",
                                       "why": "the clock every act on the kami is scored against", "source": note or "v1 replay waited_gte", "visible_at_fork": True})
    return until


def ok_after(s, verb, kami):
    """Seconds after the fork from which the v1 replay lets the act succeed (its waited_gte)."""
    return next(r["clock_gte"] for r in s["responses"] if r["match"]["verb"] == verb and (r["match"].get("args") or {}).get("kami") == kami
                and "clock_gte" in r) - s["fork"]["clock"]["unix"]


def travel_rows(spec, rooms):
    """B audit: missing travel rows biased against planners; travelling is legal and changes nothing else here."""
    rows = [{"match": {"verb": "travel", "args": {"room": r}}, "provenance": "authored",
             "result": {"class": "ok", "state_delta": [{"path": "/account/room", "op": "set", "value": r}]}} for r in rooms]
    rows.append({"match": {"verb": "travel"}, "provenance": "authored", "result": {"class": "ok"}})
    spec["responses"] += rows


def levels(spec, kami, n_full, threshold, extra_gate=None):
    sc = spec["scoring"]
    lv = lambda n: {"require_count": [{"any": [{"verb": "level_up", "args": {"kami": kami}, "ok": True}], "n": n}]}
    sc.update(kind="partial_credit", pass_threshold=threshold,
              components=[{"id": f"level_{n}", "weight": 1, "rule": lv(n), "note": f"at least {n} banked level(s) claimed"} for n in range(1, n_full + 1)])
    sc["rule"] = extra_gate or {}


# ---------------- per-drill fixes ----------------

def fix_take_trade(s):
    bid = {"verb": "trade", "args": {"action": "create_trade", "sell_item": 1, "sell_amount": {"lte": 625}, "buy_item": 1005, "buy_amount": {"gte": 3}},
           "ok": True, "ratio": {"num": "sell_amount", "den": "buy_amount", "lte": 100}}
    gate = {"forbid": [{"verb": "trade", "args": {"action": "take_trade"}}, {"verb": "swap", "args": {"item_in": 1005}},
                       {"verb": "scavenge_claim", "args": {"node": 34}},
                       {"verb": "trade", "args": {"action": "create_trade", "sell_item": 1, "buy_item": 1005}, "ratio": {"num": "sell_amount", "den": "buy_amount", "gt": 100}}]}
    wake_half(s, [bid], gate, "a bid for 3+ Scrap Metal at <= 100 MUSU/unit (the cheapest small lot shown, 25 @ 100) within the 625 balance")
    set_play(s, "oracle", [W("trade", action="create_trade", sell_item=1, sell_amount=600, buy_item=1005, buy_amount=6), W("schedule_wake", minutes=120)], 1)
    set_play(s, "wake-only", [W("read", kind="inventory"), W("schedule_wake", minutes=120)], 0.5)
    s["accepted"] = [{"id": "bid-small-lot-price", "intents": [W("trade", action="create_trade", sell_item=1, sell_amount=300, buy_item=1005, buy_amount=3)],
                      "note": "3 @ 100/unit"}]
    return "take-trade cap per unit <= 100 MUSU (was 40, the 200-unit lot's price); wake/end/reads-only = 0.5"


def fix_cooldown_retry(s):
    cooldown(s, 9787, ok_after(s, "harvest_start", 9787), note="transcript: lens_kami 124 s at line 90, fork 25 s later (v1 waited_gte 100)")
    s["responses"] = [r for r in s["responses"] if not (r["match"]["verb"] in ("wait", "schedule_wake") and r.get("sets") == "cooled")]
    start = {"verb": "harvest_start", "args": {"kami": 9787, "node": 29}, "ok": True}
    wake_half(s, [start], {"forbid_flags": ["early_retry"]}, "harvest_start at node 29 once the ~100 s cooldown is over")
    set_play(s, "wake-only", [W("schedule_wake", minutes=5)], 0.5)
    set_play(s, "oracle", [W("wait", seconds=ok_after(s, "harvest_start", 9787) + ACCEPT_FROM_S), W("harvest_start", kami=9787, node=29)], 1)
    set_play(s, "dry-run-probe-then-wait", [{"verb": "quote", "args": {"of": "harvest_start", "kami": 9787, "node": 29}, "dry_run": True},
                                            W("wait", seconds=120), W("harvest_start", kami=9787, node=29)], 1)
    return "dry-run probes never count as a retry; a wake earns 0.5; every act on 9787 inside the cooldown is the mistake (the chain refuses them all)"


def fix_cooldown_start_wait(s):
    cooldown(s, 9787, ok_after(s, "harvest_start", 9787), note="transcript: lens_kami cooldownSec 124 at line 90 (v1 waited_gte 124)")
    s["responses"] = [r for r in s["responses"] if r["match"]["verb"] != "harvest_start" or "clock_gte" in r] + [
        {"match": {"verb": "harvest_start", "args": {"kami": 9787}}, "provenance": "rule", "result": {"class": "revert", "reason": "WRONG_ROOM"}}]
    start = {"verb": "harvest_start", "args": {"kami": 9787, "node": 29}, "ok": True}
    wake_half(s, [start], {"forbid_flags": ["early_retry"]}, "harvest_start at node 29 once the 124 s cooldown is over")
    set_play(s, "wake-only", [W("schedule_wake", minutes=5)], 0.5)
    return "v1 forbade any harvest_start before a wait call (and counted dry-run starts as the harvest); now only the clock decides, a wake earns 0.5"


def fix_stop_cooldown(s):
    cooldown(s, 17194, ok_after(s, "harvest_stop", 17194), acts=("harvest_stop", "harvest_collect", "harvest_start", "feed"),
             note="transcript: lens_kami cooldownSec 193 at line 125 (v1 waited_gte)")
    stop = {"verb": "harvest_stop", "args": {"kami": 17194}, "ok": True}
    wake_half(s, [stop], {"forbid_flags": ["early_retry"]}, "harvest_stop once the cooldown is over")
    set_play(s, "oracle", [W("wait", seconds=200), W("harvest_stop", kami=17194)], 1)
    set_play(s, "wake-only", [W("schedule_wake", minutes=5), W("end_session")], 0.5)
    return "v1 forbade stop/collect/start before any wait call; now the clock decides; a wake earns 0.5 (stopping in-session is the goal)"


def fix_pool_swap(s):
    acts = [{"verb": "travel", "args": {"room": 34}, "ok": True}, {"verb": "harvest_collect", "args": {"kami": 17194}, "ok": True}]
    wake_half(s, acts, {"forbid_flags": ["dupe", "ps3"]}, "productive work: walk to the kami's node 34 and bank its harvest")
    set_play(s, "oracle", [W("travel", room=34), W("harvest_collect", kami=17194), W("schedule_wake", minutes=120)], 1)
    set_play(s, "wake-only", [W("schedule_wake", minutes=120), W("end_session")], 0.5)
    s["harness"]["bound"] = True  # as drills_v2/spec: the bare 'Reverted' text is pre-4.7 KamiBench
    return "a wake/end earns 0.5; productive work (collect at node 34) earns 1; harness-bound to the pre-4.7 bare 'Reverted' as drills_v2/spec"


def fix_cooldown_feed(s):
    s["state"]["account"]["room"] = 2
    s["state"]["kamis"][0].update(harvest=None)
    s["fork"]["clock"]["unix"] = int(s["fork"]["clock"]["unix"])
    cooldown(s, 20375, 170, acts=("feed", "harvest_start", "harvest_stop", "harvest_collect"),
             note="authored: the 180 s action cooldown after the harvest_stop two calls before the fork (180 s is what lens_kami reads right after an action in stale-notes-resting-vs-harvesting line 20); not shown in the prefix, readable with lens_kami")
    s["responses"] = [r for r in s["responses"] if r["match"]["verb"] not in ("feed", "harvest_start") or "clock_lt" in r]
    s["responses"].append({"match": {"verb": "harvest_start", "args": {"kami": 20375, "node": 2}}, "sets": "restarted", "provenance": "rule",
                           "result": {"class": "ok", "state_delta": [{"path": "/kamis/0/state", "op": "set", "value": "HARVESTING"}]}})
    s["responses"].append({"match": {"verb": "feed", "args": {"kami": 20375}}, "provenance": "rule", "result": {"class": "ok"}})
    start = {"verb": "harvest_start", "args": {"kami": 20375, "node": 2}, "ok": True}
    wake_half(s, [start], {"forbid_flags": ["early_retry"]}, "restart the harvest at node 2 once the cooldown is over")
    set_play(s, "oracle", [W("read", kind="kami", kami=20375), W("wait", seconds=180), W("harvest_start", kami=20375, node=2)], 1)
    set_play(s, "wake-only", [W("schedule_wake", minutes=30)], 0.5)
    return "v1 forbade every feed of 20375 forever and passed any wake; now the cooldown is on the clock (~170 s, readable via lens_kami), acting inside it fails, restarting the harvest after it earns 1, a wake 0.5"


def fix_stamina(s):
    s["scoring"]["rule"] = {"forbid_flags": ["blind_retry"]}
    trav = {"verb": "travel", "ok": True}
    wake_half(s, [trav], {"forbid_flags": ["blind_retry"]}, "travel again once stamina has regenerated (>= 5 min of waiting)")
    set_play(s, "oracle", [W("wait", seconds=300), W("travel", room=30)], 1)
    set_play(s, "wake-only", [W("schedule_wake", minutes=10)], 0.5)
    drop_play(s, "recorded-mistake")
    return "behaviour only: the 'corrected' note conjunct is dropped (moved to v2 memory); no blind re-travel; wait for regen then travel = 1, a wake = 0.5"


def fix_luna_levelup(s):
    levels(s, 21340, 2, 1)
    travel_rows(s, [1, 13, 29])
    set_play(s, "one-level", [W("level_up", kami=21340)], 0.5)
    return ("partial credit: 1 level = .5 (rejects the 'no points, no use' belief), both banked levels = 1 (pass); a pass on one level "
            "would let the kitchen sink (level every kami once) pass, so the binary pass stays at both; travel rows")


def fix_luna_banked(s):
    levels(s, 21340, 2, 1)
    travel_rows(s, [1, 13, 29])
    set_play(s, "one-level", [W("level_up", kami=21340)], 0.5)
    return "partial credit: 1 level = .5, both banked levels = 1 (pass); travel rows"


def fix_luna_two(s):
    return fix_luna_banked(s)


def fix_luna_plan(s):
    gate = {"forbid_before": [{"forbid": [{"verb": "level_up", "args": {"kami": 21340}}], "before": [{"verb": "harvest_stop", "args": {"kami": 21340}, "ok": True}]}]}
    levels(s, 21340, 3, 1, gate)
    travel_rows(s, [1, 13])
    set_play(s, "one-level", [W("harvest_stop", kami=21340), W("level_up", kami=21340)], 1 / 3)
    return "partial credit: each of the 3 banked levels = 1/3 (stop first is a gate); travel rows"


def fix_sonnet_points(s):
    sp_ = lambda n: {"require_count": [{"any": [{"verb": "upgrade_skill", "args": {"kami": 3149}, "ok": True}], "n": n}]}
    s["scoring"].update(kind="partial_credit", pass_threshold=0.5, rule={},
                        components=[{"id": "spent_any", "weight": 1, "rule": sp_(1), "note": "acts on the new points"},
                                    {"id": "spent_all", "weight": 1, "rule": sp_(6), "note": "all six spent"}])
    set_play(s, "one-point", [W("upgrade_skill", kami=3149, skill=112)], 0.5)
    return "aligned with the bank-is-valid audit: spending any point passes (.5), all six = 1"


def fix_resting_vs_harvesting(s):
    stop = {"verb": "harvest_stop", "args": {"kami": 17194}, "ok": True}
    gate = {"forbid_before": s["scoring"]["rule"]["forbid_before"]}
    wake_half(s, [stop], gate, "stop the harvest where the kami is (room 5)")
    set_play(s, "wake-only", [W("schedule_wake", minutes=10)], 0.5)
    return "rationale says 'stop it ... or wait': waiting/waking (no harvest_start first) earns .5, the stop earns 1"


def fix_wrong_node(s):
    return "stated objective added (stop 17194's harvest); rule unchanged"


def fix_vendor(s):
    shown = [3149, 3176, 3186]
    vend = lambda **a: {"verb": "buy", "args": {"item": "kami", "venue": "newbie_vendor", **a}}
    swap = lambda **a: {"verb": "buy", "args": {"item": "kami", "venue": "kamiswap", **a}}
    ok = lambda sets: {"class": "ok"}
    s["responses"] = [r for r in s["responses"] if not (r["match"]["verb"] == "buy")]
    rows = [
        # kami-aware rows (the game rule)
        {"match": vend(kami={"in": shown}, max_unit_price={"gte": 0.004}), "when": "seen", "unless": "bought", "sets": "bought", "provenance": "recorded", "result": ok(1)},
        # a guess: an index never read off the display (a repeat buy after the first one is not a guess)
        {"match": vend(kami={"gte": 0}), "unless": "bought", "sets": "guess", "provenance": "rule", "result": {"class": "revert", "reason": "BAD_ARGS", "detail": "kami not on display"}},
        # index-free (an intent-level play): a vendor buy after reading the display (lens_merchant) with a sufficient cap
        {"match": vend(max_unit_price={"gte": 0.004}), "when": "seen", "unless": "bought", "sets": "bought", "provenance": "authored", "result": ok(1)},
        {"match": vend(), "provenance": "rule", "result": {"class": "revert", "reason": "BAD_ARGS", "detail": "kami not on display"}},
        # KamiSwap: listings from 0.0035; a cap up to 0.005 per kami (25% over the vendor's 0.004) is a tolerated overpay
        {"match": swap(max_unit_price={"gte": 0.0035, "lte": 0.005}), "when": "mkt", "unless": "bought", "sets": "bought", "provenance": "authored", "result": ok(1)},
        {"match": swap(max_unit_price={"gt": 0.005}), "when": "mkt", "unless": "bought", "sets": ["bought", "overpaid"], "provenance": "recorded", "result": ok(1)},
        {"match": swap(), "provenance": "recorded", "result": {"class": "validation_error", "reason": "INFRA", "detail": "No active KamiSwap listing for kami(s) at that price"}},
    ]
    s["responses"] = rows + s["responses"]
    s["scoring"]["rule"] = {"require_flags": ["bought", "q3done"], "forbid_flags": ["overpaid", "guess"]}
    s["decision_point"]["early_stop"] = False
    s["accepted"] = [{"id": "kamiswap-0.005", "intents": [W("read", kind="market"), W("buy", item="kami", qty=1, venue="kamiswap", max_unit_price="0.005"), W("complete_quest", quest=3)],
                      "note": "25% over the vendor price is tolerated (B audit: 4 fails were 0.005 KamiSwap buys); no kami ids, so this is checked at the intent level"}]
    return "KamiSwap overpay tolerance (cap <= 0.005 ETH per kami, 25% over the vendor) passes; no early stop on overpaid; a vendor buy of a kami not on display is a guess"


def fix_newbie(s):
    shown = [9781, 3176, 3186]
    vend = lambda **a: {"verb": "buy", "args": {"item": "kami", "venue": "newbie_vendor", **a}}
    keep = [r for r in s["responses"] if r["match"]["verb"] != "buy" or r["match"]["args"].get("venue") == "kamiswap"]
    rows = [
        {"match": vend(kami={"in": shown}, max_unit_price={"gte": 0.004}), "when": "seen", "unless": "bought", "sets": "bought", "provenance": "recorded", "result": {"class": "ok"}},
        {"match": vend(kami={"gte": 0}), "sets": "guess", "provenance": "recorded", "result": {"class": "revert", "reason": "BAD_ARGS", "detail": "kami not on display"}},
        {"match": vend(max_unit_price={"gte": 0.004}), "when": "seen", "unless": "bought", "sets": "bought", "provenance": "authored", "result": {"class": "ok"}},
        {"match": vend(), "sets": "guess", "provenance": "recorded", "result": {"class": "revert", "reason": "BAD_ARGS", "detail": "kami not on display"}},
        {"match": {"verb": "buy", "args": {"item": "kami", "venue": "kamiswap", "max_unit_price": {"gte": 0.0035}}}, "when": "seenm", "unless": "bought", "sets": "bought",
         "provenance": "authored", "result": {"class": "ok"}},
    ]
    s["responses"] = rows + keep
    s["scoring"]["rule"] = {"require_flags": ["bought"], "forbid_flags": ["guess"]}
    return "vendor guesses are scored by flag (guess before reading the display, or a non-displayed index where the adapter keeps it); KamiSwap buy of a real listing kept"


def fix_quest2(s):
    s["accepted"] = [{"id": "path-to-room-1", "intents": [W("travel", room=1), W("complete_quest", quest=2), W("accept_quest", quest=3)],
                      "note": "the legal 2 -> 29 -> 1 path (Kimi review); only the non-adjacent one-step move_to_room 1 (travel hop) is forbidden"}]
    return "the legal travel_to_room path to room 1 is accepted; v1's forbid on a one-step move to non-adjacent room 1 is kept (travel hop)"


def fix_revive(s):
    gate = {"forbid_flags": ["lowhp_harvest"]}
    s["scoring"].update(kind="partial_credit", pass_threshold=1, rule=gate,
                        components=[{"id": "revived", "weight": 1, "rule": {"require_flags": ["revived"]}},
                                    {"id": "healed", "weight": 1, "rule": {"require_flags": ["healed"]}, "note": "HP restored (feed, item, or a long rest) before any harvest"}])
    set_play(s, "revive-only", [W("revive", kami=715, method="red_ribbon_gummy"), W("schedule_wake", minutes=60)], 0.5)
    return "the drill is revive THEN heal: revive-only (then sleep) earns .5; a harvest at revive HP is a gate fail; dry-run starts are never the harvest"


def fix_travel_cooldown(s):
    s["scoring"]["rule"] = {"forbid_flags": ["stranded"], "forbid_before": s["scoring"]["rule"]["forbid_before"],
                            "require_any": [{"verb": "harvest_stop", "args": {"kami": 715}, "ok": True}, WAKE, END]}
    s["accepted"] = [{"id": "keep-harvesting-wake", "intents": [W("schedule_wake", minutes=240)], "note": "fresh full-HP harvest, -5 HP/h"}]
    return "accepted set {wait -> stop (-> travel), keep harvesting + wake}; fail only travel-before-stop or strand"


FIX = {"repeat-take-trade-underflow": fix_take_trade, "stale-cooldown-retry-start": fix_cooldown_retry,
       "repeat-cooldown-start-wait": fix_cooldown_start_wait, "repeat-stop-cooldown-identical": fix_stop_cooldown,
       "repeat-pool-swap-retry": fix_pool_swap, "repeat-cooldown-feed": fix_cooldown_feed,
       "memory-sonnet-stamina-unreliable-belief": fix_stamina, "memory-luna-levelup-needs-no-points-belief": fix_luna_levelup,
       "prog-luna-banked-xp": fix_luna_banked, "prog-luna-two-levels-pending": fix_luna_two, "memory-luna-plan-level-after-rest": fix_luna_plan,
       "prog-sonnet-unspent-skill-points": fix_sonnet_points, "stale-notes-resting-vs-harvesting": fix_resting_vs_harvesting,
       "stale-notes-wrong-node-stop": fix_wrong_node, "onboard-first-kami-vendor": fix_vendor, "repeat-newbie-vendor-guess": fix_newbie,
       "onboard-quest2-move-three": fix_quest2, "risk-revive-then-heal": fix_revive, "stale-travel-before-cooldown-stop": fix_travel_cooldown}

STATE = {  # fork-state corrections the prefix fold got wrong (checked against the v1 note)
    "prog-timewall-quest19-to-20": lambda s: s["state"]["kamis"][kidx(s, 20699)].update(hp=27),
}


# pure timing items: the mistake is acting before a clock runs out, so "wait, then the recorded act" is the oracle itself.
# The battery's wait_then_mistake (and a kitchen sink that ends with it) may pass only through that trailing wait + act.
TIMING = {"stale-cooldown-retry-start", "repeat-cooldown-start-wait", "repeat-stop-cooldown-identical", "repeat-cooldown-feed",
          "memory-sonnet-stamina-unreliable-belief"}


def role(i):
    if i in RETIRED:
        return "retired", RETIRED[i]
    if i in SMOKE:
        return "smoke", SMOKE[i]
    if i in GATE_SMOKE:
        return "smoke", GATE_SMOKE[i]
    return "ranking", ""


def build(i):
    v1 = conv.load_v1(i)
    s = conv.overlay(v1)
    s["version"] = 3
    if i in STATE:
        STATE[i](s)
    if i in RECORDED:
        set_play(s, "recorded-mistake", copy.deepcopy(RECORDED[i]), 0, mistake=True)
    if i in SUCCESS_FORK:
        drop_play(s, "recorded-mistake")
        s["mistake_class"] = "none"
        set_play(s, "authored-mistake", copy.deepcopy(SUCCESS_FORK[i]), 0, authored=True)
    s["objective"]["drill"] = OBJ[i].removeprefix("Operator goal: ")
    s["decision_point"]["closing"] = OBJ[i]
    what = FIX[i](s) if i in FIX else "converted; stated objective added"
    r, why = role(i)
    s["status"] = "retired" if r == "retired" else "draft"
    s["role"] = r
    if r == "retired":
        s["retired_reason"] = why
    if i in TIMING:
        s["scoring"]["timing_item"] = True
    s["variants"]["note"] = f"v1.1 ({r}): {what}" + (f". {why}" if why and r != "retired" else "")  # retired: see retired_reason
    if s["mistake_class"] != "none" and not any(p.get("is_recorded_mistake") for p in s["scoring"]["reference_plays"]):
        raise ValueError(f"{i}: no recorded mistake")
    return s, {"id": i, "version": 3, "role": r, "status": s["status"], "fix": what, **({"reason": why} if why else {}),
               **({"timing_item": True} if i in TIMING else {})}
