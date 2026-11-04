"""kami-harness 4.7.0 + kami-agent 0.7.5 adapter for v2 drill specs (docs/v2-spec.md section "Adapter contract").

Renders a spec into the Season 1 tool surface, maps every tool call to abstract intents (state-aware: dry runs become
quotes, batches and act_sequence split per kami/step, use_item_batch with a revive item on a DEAD kami is a revive),
answers each call from the simulator (recorded native text first, then 4.7.0-worded templates, reads formatted from
state, recorded reads, v1 global reads) and reproduces the harness quirks the drills must know about. Pure code: no
network, no state kept between attempts. Run a spec bank through Inspect:

    uv run inspect eval kami_drills/adapters/harness_4_7.py -T paths=drills_v2/spec --model <model>
    uv run inspect eval kami_drills/adapters/harness_4_7.py -T paths=drills_v2/spec -T policy=oracle --model mockllm/model
"""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.model import (ChatMessageAssistant, ChatMessageSystem, ChatMessageTool, ChatMessageUser, GenerateConfig,
                              ModelOutput, execute_tools, get_model)
from inspect_ai.scorer import Score, mean, scorer, stderr
from inspect_ai.solver import solver
from inspect_ai.tool import ToolCall, ToolDef
from inspect_ai.tool._tool_params import ToolParams

from kami_drills import spec as sp
from kami_drills import task as kd

ROOT = Path(__file__).parents[2]
ID = "kh-4.7.0@1.0.0"
HARNESS = "kami-harness 4.7.0"  # native text keyed by any harness id starting with this is served verbatim
STACK = {"harness": "kami-harness 4.7.0 df7129f", "agent": "kami-agent 0.7.5 8e444bb", "lens": "kami-lens 1.0.5 e604290",
         "rung": "control"}
DENY = kd.OUTSOURCE | {"chat_send"}  # Season 1 live denylist (sessions.log): chat_send + the 9 OUTSOURCE tools
CAPS = {"session_tool_cap": 50, "max_turns": kd.MAX_TURNS, "max_consecutive_errors": 5, "wait_max_seconds": 300,
        "wake_min_minutes": 5, "wake_max_minutes": 1440, "wake_default_minutes": 60, "tool_result_max_bytes": 65536}
NULL_PLAY = "no calls; kami-agent 0.7.5 default wake 60 min"
REVIVE_ITEMS = {11001: "red_ribbon_gummy", 11002: "melkarth_spell_card", 11003: "djed_pillar", 11004: "pale_potion"}
REVIVE_METHODS = ("onyx", *REVIVE_ITEMS.values())
# v1 told models "not available in this drill" on a gap and 25% of tries said so (B audit). A gap now gets the
# harness's own transient RPC error, copied from Season 1 transcripts, so a missing answer looks like the live game.
GAP_TEXT = "Error executing tool {tool}: 503 Server Error: Service Unavailable for url: https://jsonrpc-yominet-1.anvil.initia.xyz/"

# Behaviour reproduced *because* 4.7.0 had it. A drill whose verdict a quirk changes is harness-bound on this adapter.
QUIRKS = {
    "accept-owned-repeatable": "accept_quest refuses any quest the account already owns, repeatables included "
        "(executor/server.py:11161); the chain accepts a repeat of 3900 (eth_call, 2026-11-03). S1: 37 refusals in telemetry.",
    "completable-ignores-room": "check_quest_completable reports objectives only and ignores room requirements "
        "(S1 sol-3 s4: true for 3039 while complete_quest reverted; that revert was chain lag right after the accept, and "
        "S1 completed 3039 from rooms 10, 33 and 62).",
    "quest-list-truncated": "tool results are cut at 65,536 bytes (tool_result_max_bytes) and a full lens_quests runs to "
        "~202 KB, so quest 10003 (SQ997, near byte 199,700) was never listed in S1; the docs named it. Not reproduced: "
        "revive-claim items carry the doc read in their native prefix and are harness-bound instead.",
}

READ_TOOLS = {"get_status", "workspace_list", "list_accounts", "get_gas_balance", "bridge_status", "get_item_orderbook", "check_quest_completable", "quest_state",
    "get_expected_objective", "get_scavenge_points", "get_scavenge_droptable"}
# harness arg name -> canonical intent arg name (the spec's game ontology)
CANON = {"kami_index": "kami", "kami_id": "kami", "node_index": "node", "quest_index": "quest", "item_index": "item",
         "room_index": "room", "target_room": "room", "npc_index": "merchant", "food_item_id": "item", "item_id": "item",
         "min_amount_out": "min_out", "skill_index": "skill", "recipe_index": "recipe", "victim_kami_id": "victim",
         "killer_kami_id": "kami", "minutes_from_now": "minutes", "target_kami_id": "kami"}
DROP = {"account", "dry_run", "at_least_block"}
# reason code -> the revert message 4.7.0 recovers from the chain (S1 telemetry wording)
REVERT_TEXT = {"COOLDOWN": "kami on cooldown", "KAMI_STARVING": "kami starving", "KAMI_TOO_FAR": "kami too far",
               "WRONG_ROOM": "must be in same room", "QUEST_OBJS_NOT_MET": "quest objs not met",
               "QUEST_REQS_NOT_MET": "reqs not met", "KAMI_NOT_RESTING": "kami not RESTING", "UNDERFLOW": "arithmetic underflow",
               "KAMI_NOT_HARVESTING": "kami not HARVESTING", "KAMI_DEAD": "kami is dead", "KAMI_NOT_DEAD": "kami not dead",
               "SLIPPAGE": "slippage", "POOL_DISABLED": "pool disabled", "LEVEL_REQS": "level requirements not met",
               "NO_SKILL_POINTS": "SkillUpgrade: not enough points", "INSUFFICIENT_MUSU": "arithmetic underflow"}
# harness error text -> reason code, first match wins. Coverage over S1 tool errors: tests/test_adapter_4_7.py.
REASON_RE = [(re.compile(p, re.I), c) for p, c in [
    (r"^(503|502)|LENS_UNAVAILABLE|KAMIDEN_UNAVAILABLE|NOT_APPLIED|NOT_FOUND|cache missing|timed out|rate limit", "INFRA"),
    (r"on cooldown", "COOLDOWN"), (r"starving", "KAMI_STARVING"), (r"too far", "KAMI_TOO_FAR"),
    (r"not in same room|must be in same room|not in room|already in room", "WRONG_ROOM"),
    (r"not connected to the account's current room|unreachable|mid-path", "ROOM_UNREACHABLE"),
    (r"stamina", "NOT_ENOUGH_STAMINA"), (r"quest objs not met", "QUEST_OBJS_NOT_MET"),
    (r"reqs not met|Quest does not|requirements not met", "QUEST_REQS_NOT_MET"),
    (r"is not accepted by account", "QUEST_NOT_ACCEPTED"), (r"already (completed|accepted) by account", "QUEST_ALREADY_COMPLETE"),
    (r"repeat", "QUEST_REPEAT_NOT_READY"), (r"is DEAD|kami is dead", "KAMI_DEAD"), (r"not dead|requires DEAD", "KAMI_NOT_DEAD"),
    (r"is HARVESTING|not RESTING|requires RESTING", "KAMI_NOT_RESTING"), (r"not HARVESTING|requires HARVESTING", "KAMI_NOT_HARVESTING"),
    (r"holds \d+ of item 1 \(MUSU\)|not enough musu", "INSUFFICIENT_MUSU"), (r"holds \d+ of (item|the token)|Item: requirements", "INSUFFICIENT_ITEM"),
    (r"slippage|returns \d+ .* after", "SLIPPAGE"), (r"no pool with liquidity|pool disabled", "POOL_DISABLED"),
    (r"underflow", "UNDERFLOW"), (r"SkillUpgrad|skill points?", "NO_SKILL_POINTS"), (r"level", "LEVEL_REQS"),
    (r"balance .* cannot cover|insufficient funds|OutOfFunds|gas", "INSUFFICIENT_GAS"), (r"scavenge points", "INSUFFICIENT_ITEM"),
    (r"not a trade|untradeable|order limit|purely MUSU|item-for-item|must be MUSU|does not exist|Unknown room|BAD_ARGS|"
     r"name taken|unknown op|NewbieVendor|above max_price", "BAD_ARGS"), (r"token|ERC20", "TOKEN_BALANCE"),
    (r"'message': 'Reverted'", "BARE_REVERT"), (r"No active", "INFRA")]]


def reason_code(text):
    msg = re.sub(r"^Error executing tool \w+: ", "", text or "")  # the tool name must not match (get_gas_balance, level_up_kami)
    return next((c for rx, c in REASON_RE if rx.search(msg)), "OTHER")


def surface():
    """(name, description, params) for the live-identical tool list: 99 harness tools plus the 8 scaffold tools."""
    out = [(t["name"], t["description"], kd.params(t["input_schema"])) for t in kd.SURFACE["tools"] if t["name"] not in DENY]
    for name, args in kd.SCAFFOLD.items():
        props = {k: {"type": v, "description": k} for k, v in args.items()}
        out.append((name, f"kami-agent scaffold tool: {name}", ToolParams(properties=props, required=list(props))))
    return out


SURFACE_NAMES = frozenset(n for n, _, _ in surface())
DRY_RUN_TOOLS = frozenset(t["name"] for t in kd.SURFACE["tools"] if "dry_run" in t["input_schema"].get("properties", {}))


def canon(args):
    return {CANON.get(k, k): v for k, v in (args or {}).items() if k not in DROP and v is not None}


def I(verb, **args):
    return {"verb": verb, "args": {k: v for k, v in args.items() if v is not None}}


def ids(x):
    return [x] if isinstance(x, int) else list(x or [])


def to_intents(tool, args, sim=None):
    """One tool call -> its intents. sim (a Simulator) makes the state-dependent mappings; None = state-free."""
    a, out = dict(args or {}), []
    if tool == "act_sequence":
        for step in a.get("steps") or []:
            t, sa = kd.sequence_step(step)
            out += to_intents(t, sa, sim) if t else [I("unknown", op=step.get("op"))]
        return out
    if tool in ("harvest_start", "harvest_stop", "harvest_collect"):
        out = [I(tool, kami=k, node=a.get("node_index") if tool == "harvest_start" else None) for k in ids(a.get("kami_ids"))]
    elif tool == "move_to_room":  # one room change: the target must be adjacent (kami-rules checks it)
        out = [I("travel", room=a.get("room_index"), hop=True)]
    elif tool == "travel_to_room":
        out = [I("travel", room=a.get("target_room"))]
    elif tool == "feed_kami":
        out = [I("feed", kami=a.get("kami_id"), item=a.get("food_item_id"))]
    elif tool == "revive_kami":
        out = [I("revive", kami=a.get("kami_id"), method=a.get("method", "onyx"))]
    elif tool == "use_item_batch":
        k, item, n = a.get("kami_id"), a.get("item_id"), int(a.get("count") or 0)
        kami = sim.kami(k) if sim else None
        if item in REVIVE_ITEMS and kami and kami["state"] == "DEAD":
            out = [I("revive", kami=k, method=REVIVE_ITEMS[item])] + [I("feed", kami=k, item=item)] * max(n - 1, 0)
        elif _item_type(sim, item) == "FOOD":
            out = [I("feed", kami=k, item=item)] * n
        else:
            out = [I("use_item", kami=k, item=item)] * n
    elif tool == "level_up_kami":
        out = [I("level_up", kami=a.get("kami_id"))]
    elif tool == "level_to":
        k = sim.kami(a.get("kami_id")) if sim else None
        n = max(int(a.get("target_level") or 0) - (k or {}).get("level", 0), 1) if k and "level" in k else 1
        out = [I("level_up", kami=a.get("kami_id"))] * n
    elif tool in ("level_and_allocate_batch", "feed_level_allocate_batch"):
        for t in a.get("targets") or []:  # an empty batch is no intent at all (and a harness validation error)
            if not isinstance(t, dict):
                continue
            k = t.get("kami_id")
            if tool == "feed_level_allocate_batch" and t.get("food_item_id"):
                out.append(I("feed", kami=k, item=t["food_item_id"]))
            if t.get("target_level") is not None or tool == "level_and_allocate_batch" and not t.get("skill_plan"):
                out += to_intents("level_to", {"kami_id": k, "target_level": t.get("target_level")}, sim)
            for s in t.get("skill_plan") or []:
                out += [I("upgrade_skill", kami=k, skill=s.get("skill_index"))] * int(s.get("points") or 1)
    elif tool == "upgrade_skill":
        out = [I("upgrade_skill", kami=a.get("kami_id"), skill=a.get("skill_index"))]
    elif tool == "allocate_skills":
        out = [I("upgrade_skill", kami=a.get("kami_id"), skill=s.get("skill_index"))
               for s in a.get("skill_plan") or [] if isinstance(s, dict) for _ in range(int(s.get("points") or 1))]
    elif tool == "skill_respec":
        out = [I("respec", kami=a.get("kami_id"))]
    elif tool in ("accept_quest", "complete_quest", "drop_quest"):
        out = [I(tool, quest=a.get("quest_index"))]
    elif tool == "listing_buy":
        amounts = list(a.get("amounts") or [])
        out = [I("buy", item=it, qty=amounts[i] if i < len(amounts) else None, venue=f"merchant:{a.get('merchant_index')}")
               for i, it in enumerate(a.get("item_indices") or [])]
    elif tool == "auction_buy":
        out = [I("buy", item=a.get("item_index"), qty=a.get("amount", 1), venue="auction")]
    elif tool == "newbie_vendor_buy":
        out = [I("buy", item="kami", qty=1, venue="newbie_vendor", max_unit_price=a.get("max_price_eth"), kami=a.get("kami_index"))]
    elif tool == "buy_kami":  # one buy of every listed kami: the cap is a total, so the per-unit price is total / qty
        ks = ids(a.get("kami_ids"))
        out = [I("buy", item="kami", qty=len(ks), venue="kamiswap", kami=ks[0] if len(ks) == 1 else ks or None,
                 max_total_price=a.get("max_total_eth"), max_unit_price=_per_unit(a.get("max_total_eth"), len(ks)))]
    elif tool == "pool_swap":
        out = [I("swap", item_in=a.get("item_in"), item_out=a.get("item_out"), amount_in=a.get("amount_in"), min_out=a.get("min_amount_out"))]
    elif tool == "pool_swap_quote":
        return [I("quote", of="swap", item_in=a.get("item_in"), item_out=a.get("item_out"), amount_in=a.get("amount_in"))]
    elif tool in ("portal_deposit", "portal_withdraw"):
        out = [I(tool, item=a.get("item"), amount=a.get("amount"), to=a.get("to") if tool == "portal_withdraw" else None)]
    elif tool == "bridge_eth_from_mainnet":
        out = [I("bridge", amount_eth=a.get("amount_eth"))]
    elif tool in ("fund_operator", "withdraw_operator"):
        out = [I(tool, amount_eth=a.get("amount_eth"))]
    elif tool in ("craft_item", "speed_craft_batch"):
        out = [I("craft", recipe=a.get("recipe_index"), amount=a.get("amount", a.get("count", 1)))]
    elif tool in ("scavenge_claim", "scavenge_claim_and_reveal"):
        out = [I("scavenge_claim", node=a.get("node_index"))]
    elif tool == "liquidate_kami":
        out = [I("liquidate", kami=a.get("killer_kami_id"), victim=a.get("victim_kami_id"))]
    elif tool in ("take_trade", "complete_trade", "create_trade", "cancel_trade", "complete_all_trades", "list_kami", "cancel_kami_listing"):
        out = [I("trade", action=tool, **canon(a))]
    elif tool in ("transfer_kami", "transfer_items"):
        out = [I("transfer", action=tool, **canon(a))]
    elif tool in ("gacha_use", "gacha_reroll", "gacha_reveal"):
        out = [I("gacha", action=tool, **canon(a))]
    elif tool in ("register_account", "create_operator_wallet"):
        out = [I("register", name=a.get("name"))]
    elif tool == "name_kami":
        out = [I("name", kami=a.get("kami_id"), name=a.get("name"))]
    elif tool in ("equip_item", "unequip_item"):
        out = [I(tool.split("_")[0], kami=a.get("kami_id"), item=a.get("item_index"), slot=a.get("slot_type"))]
    elif tool == "equip_all_batch":
        out = [I("equip", kami=e.get("kami_id"), item=e.get("item_index")) for e in a.get("equips") or [] if isinstance(e, dict)]
    elif tool == "unequip_all_batch":
        out = [I("unequip", kami=k, slot=a.get("slot_type")) for k in ids(a.get("kami_ids"))]
    elif tool in ("use_account_item", "cast_item"):
        out = [I("use_item", kami=a.get("target_kami_id"), item=a.get("item_id", a.get("item_index")), amount=a.get("amount"))]
    elif tool == "wait":
        return [I("wait", seconds=min(float(a.get("seconds") or 0), CAPS["wait_max_seconds"]))]
    elif tool == "set_next_wake":
        m = a.get("minutes_from_now")
        m = CAPS["wake_default_minutes"] if m is None else min(max(float(m), CAPS["wake_min_minutes"]), CAPS["wake_max_minutes"])
        return [I("schedule_wake", minutes=m)]
    elif tool == "end_session":
        return [I("end_session")]
    elif tool == "workspace_write":
        return [I("note_write", path=a.get("path"), content=a.get("content"))]
    elif tool == "workspace_delete":
        return [I("note_write", path=a.get("path"), content=None)]
    elif tool == "workspace_read":
        return [I("note_read", path=a.get("path"))]
    elif tool.startswith("lens_") or tool in READ_TOOLS:
        return [{"verb": "read", "args": {"kind": tool.removeprefix("lens_"), **canon(a)}}]
    elif tool in SURFACE_NAMES or tool in kd.OUTSOURCE:
        out = [I("other_act", tool=tool)]
    else:
        return [I("unknown", tool=tool)]
    if a.get("dry_run") is True:  # a simulated act is perception, never the act
        return [{"verb": "quote", "args": {"of": i["verb"], **i["args"]}, "dry_run": True} for i in out]
    return out


def _per_unit(total, n):
    try:
        return f"{float(total) / n:.10g}"
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _item_type(sim, item):
    return ((sim.spec.get("world", {}).get("catalog", {}).get("items", {}).get(str(item)) or {}).get("type") if sim else None)


def from_intent(intent):
    """Inverse map for reference plays: one intent -> (tool, args) on this surface, or None if not expressible."""
    v, a = intent["verb"], intent.get("args") or {}
    simple = {"harvest_stop": lambda: ("harvest_stop", {"kami_ids": [a["kami"]]}),
              "harvest_collect": lambda: ("harvest_collect", {"kami_ids": [a["kami"]]}),
              "harvest_start": lambda: ("harvest_start", {"kami_ids": [a["kami"]], "node_index": a.get("node")}),
              "travel": lambda: ("move_to_room", {"room_index": a["room"]}) if a.get("hop") else ("travel_to_room", {"target_room": a["room"]}),
              "feed": lambda: ("feed_kami", {"kami_id": a["kami"], "food_item_id": a["item"]}),
              "revive": lambda: ("revive_kami", {"kami_id": a["kami"], "method": a.get("method", "onyx")}),
              "level_up": lambda: ("level_up_kami", {"kami_id": a["kami"]}),
              "upgrade_skill": lambda: ("upgrade_skill", {"kami_id": a["kami"], "skill_index": a.get("skill")}),
              "respec": lambda: ("skill_respec", {"kami_id": a["kami"]}),
              "accept_quest": lambda: ("accept_quest", {"quest_index": a["quest"]}),
              "complete_quest": lambda: ("complete_quest", {"quest_index": a["quest"]}),
              "drop_quest": lambda: ("drop_quest", {"quest_index": a["quest"]}),
              "swap": lambda: ("pool_swap", {"item_in": a["item_in"], "item_out": a["item_out"], "amount_in": a.get("amount_in"),
                                             "min_amount_out": a.get("min_out")}),
              "portal_deposit": lambda: ("portal_deposit", {"item": a["item"], "amount": a.get("amount")}),
              "portal_withdraw": lambda: ("portal_withdraw", {"item": a["item"], "amount": a.get("amount"), "to": a.get("to", "owner")}),
              "bridge": lambda: ("bridge_eth_from_mainnet", {"amount_eth": a.get("amount_eth")}),
              "fund_operator": lambda: ("fund_operator", {"amount_eth": a.get("amount_eth")}),
              "withdraw_operator": lambda: ("withdraw_operator", {"amount_eth": a.get("amount_eth")}),
              "use_item": lambda: ("cast_item", {"target_kami_id": a["kami"], "item_index": a["item"]}) if a.get("kami") is not None
                  else ("use_account_item", {"item_id": a["item"], "amount": a.get("amount", 1)}),
              "scavenge_claim": lambda: ("scavenge_claim", {"node_index": a["node"]}),
              "liquidate": lambda: ("liquidate_kami", {"killer_kami_id": a["kami"], "victim_kami_id": a.get("victim")}),
              "wait": lambda: ("wait", {"seconds": a.get("seconds", 0)}),
              "schedule_wake": lambda: ("set_next_wake", {"minutes_from_now": a.get("minutes")}),
              "end_session": lambda: ("end_session", {"reason": a.get("reason", "")}),
              "note_write": lambda: ("workspace_write", {"path": a.get("path"), "content": a.get("content", "")}),
              "note_read": lambda: ("workspace_read", {"path": a.get("path")})}
    try:
        if v in simple:
            tool, args = simple[v]()
            return tool, {k: x for k, x in args.items() if x is not None}
        if v == "buy" and str(a.get("venue", "")).startswith("merchant:"):
            return "listing_buy", {"merchant_index": int(a["venue"].split(":")[1]), "item_indices": [a["item"]], "amounts": [a.get("qty", 1)]}
        if v == "buy" and a.get("venue") == "auction":
            return "auction_buy", {"item_index": a["item"], "amount": a.get("qty", 1)}
        if v == "buy" and a.get("venue") == "newbie_vendor":  # no kami index: the vendor's first slot
            return "newbie_vendor_buy", {"kami_index": a.get("kami", 0), "max_price_eth": a.get("max_unit_price")}
        if v == "buy" and a.get("venue") == "kamiswap" and a.get("kami") is not None:  # buy_kami needs the listed kami ids
            ks = ids(a["kami"])
            total = a.get("max_total_price") or (f"{float(a['max_unit_price']) * len(ks):.10g}" if a.get("max_unit_price") else None)
            return ("buy_kami", {"kami_ids": ks, "max_total_eth": total}) if total else None
        if v in ("trade", "transfer", "gacha") and a.get("action") in SURFACE_NAMES:  # args kept in harness names (canon is 1:1 here)
            inv = {c: h for h, c in CANON.items() if h in {p for t in kd.SURFACE["tools"] if t["name"] == a["action"]
                                                             for p in t["input_schema"].get("properties", {})}}
            return a["action"], {inv.get(k, k): x for k, x in a.items() if k != "action"}
        if v == "read":
            kind = a.get("kind", "")
            tool = kind if kind in SURFACE_NAMES else f"lens_{kind}"
            inv = {"kami": "kami_index", "node": "node_index", "quest": "quest_index", "item": "item_index", "room": "room_index",
                   "merchant": "npc_index"}
            return (tool, {inv.get(k, k): x for k, x in a.items() if k != "kind"}) if tool in SURFACE_NAMES else None
        if v == "quote":
            act = from_intent({"verb": a.get("of"), "args": {k: x for k, x in a.items() if k != "of"}})
            if act and act[0] == "pool_swap":
                return "pool_swap_quote", {k: act[1][k] for k in ("item_in", "item_out", "amount_in") if k in act[1]}
            if act and act[0] in DRY_RUN_TOOLS:
                return act[0], {**act[1], "dry_run": True}
            return None
    except (KeyError, ValueError):
        return None
    return None


def quirk(intent, sim):
    """A 4.7.0 pre-transaction refusal that wins over the spec's rows, or None. Returns (quirk id, result)."""
    if intent["verb"] == "accept_quest":
        q = next((q for q in sim.state.get("quests", []) if q["index"] == intent["args"].get("quest")), None)
        if q and q["status"] in ("active", "objectives_met", "complete"):
            word = "accepted" if q["status"] in ("active", "objectives_met") else "completed"
            return ("accept-owned-repeatable" if q.get("repeatable") else None,
                    {"class": "validation_error", "reason": "QUEST_ALREADY_COMPLETE",
                     "detail": f"quest {q['index']} is already {word} by account 'main'"})
    return None


# ---------- answers ----------

def _receipt(sim, tool, fields=None):
    h = hashlib.sha256(f"{tool}:{len(sim.trace)}:{sim.clock}".encode()).hexdigest()
    block = (sim.spec["fork"]["clock"].get("block") or 0) + len(sim.trace)
    return json.dumps({"tx_hash": "0x" + h, "status": "success", "block": block, "account": "main", **(fields or {})}, indent=2)


def act_text(tool, intent, result, sim):
    nat = result.get("native") or {}
    native = next((t for k, t in nat.items() if k.startswith(HARNESS)), None)
    if native is None and not sim.spec["harness"]["bound"]:
        native = nat.get(sp.NATIVE_ANY)
    if native is not None:
        return native
    cls, reason = result["class"], result.get("reason")
    if cls == "gap":
        return GAP_TEXT.format(tool=tool)
    if cls == "ok":
        if intent["verb"] == "quote":
            return json.dumps(quote_fields(intent, sim) if tool == "pool_swap_quote" else {"dry_run": True, "would_succeed": True})
        return _receipt(sim, tool, result.get("fields"))
    if cls == "validation_error":
        detail = result.get("detail") or (f"holds 0 of the required item; {tool} requires 1" if reason == "INSUFFICIENT_ITEM"
                                          else REVERT_TEXT.get(reason, "precondition failed"))
        return f"Error executing tool {tool}: validation failed; no transaction sent: {detail}"
    msg = "Reverted" if reason == "BARE_REVERT" else f"revert: {REVERT_TEXT.get(reason, result.get('detail') or 'execution reverted')}: Reverted"
    return f"Error executing tool {tool}: validation failed; no transaction sent: transaction dry-run reverted: {{'code': -32000, 'message': '{msg}'}}"


def quote_fields(intent, sim):
    """pool_swap_quote from world.pools: constant product, fee from the pool, default slippage 100 bps."""
    a = intent["args"]
    for p in sim.spec.get("world", {}).get("pools", []):
        for x, y, rx, ry in ((p["item_a"], p["item_b"], p["reserve_a"], p["reserve_b"]), (p["item_b"], p["item_a"], p["reserve_b"], p["reserve_a"])):
            if (x, y) == (a.get("item_in"), a.get("item_out")) and not p.get("disabled"):
                dx = float(a.get("amount_in") or 0) * (1 - p.get("fee_bps", 30) / 1e4)
                out = int(ry * dx / (rx + dx))
                return {"item_in": x, "item_out": y, "amount_in": a.get("amount_in"), "amount_out": out,
                        "min_amount_out": int(out * 0.99), "slippage_bps": 100, "price_impact_bps": int(1e4 * dx / (rx + dx))}
    return {"error": "no pool with liquidity for this pair"}


def _hp(k):
    return {"current": k["hp"], "total": k["hp_max"], "percent": round(100 * k["hp"] / k["hp_max"])}


def lens_kami(k, sim):
    d = {"index": k["index"], "name": f"Kamigotchi {k['index']}", "state": k["state"], "hp": _hp(k)}
    for src, dst in (("level", "level"), ("xp", "xp"), ("xp_required", "xpRequired"), ("level_up_ready", "levelUpReady"),
                     ("skill_points", "skillPoints")):
        if src in k:
            d[dst] = k[src]
    if "hp_rate_per_hr" in k:
        d["hpRatePerHr"] = f"{k['hp_rate_per_hr']:+.2f}"
    if "cooldown_until_unix" in k:
        d["cooldownSec"] = max(0, int(k["cooldown_until_unix"] - sim.chain_clock()))
        d["cooldownUntil"] = int(k["cooldown_until_unix"])
    d["account"] = {"index": sim.state["account"]["index"]}
    if h := k.get("harvest"):
        d["musu"] = {"accrued": h.get("accrued_musu"), "spotRatePerHr": h.get("rate_per_hr")}
        d["node"] = {"index": h.get("node")}
    return d


def _wrap(data, sim):
    return json.dumps({"data": data, "untrusted": [], "meta": {"blockNumber": sim.spec["fork"]["clock"].get("block"),
                                                                 "stale": False, "mode": "daemon"}}, indent=2)


def read_text(tool, intent, sim):
    """A read formatted from simulated state, or None if the state does not hold what it needs."""
    s, a = sim.state, intent["args"]
    acct = s["account"]
    items = sim.spec.get("world", {}).get("catalog", {}).get("items", {})
    if tool == "lens_kami":
        k = sim.kami(a.get("kami"))
        return _wrap(lens_kami(k, sim), sim) if k else None
    if tool in ("lens_party", "lens_roster"):
        return _wrap({"account": {"index": acct["index"], "roomIndex": acct["room"]}, "kamis": [lens_kami(k, sim) for k in s["kamis"]]}, sim)
    if tool == "lens_account":
        d = {"index": acct["index"], "roomIndex": acct["room"], "kamis": [{"index": k["index"], "state": k["state"]} for k in s["kamis"]]}
        if "musu" in acct:
            d["musu"] = acct["musu"]
        if "stamina" in acct:
            d["stamina"] = {"current": acct["stamina"], "total": acct.get("stamina_max", acct["stamina"])}
        return _wrap(d, sim)
    if tool == "lens_inventory":
        rows = [{"balance": n, "item": {"index": int(i), **{k: v for k, v in (items.get(i) or {}).items() if k in ("name", "type", "for")}}}
                for i, n in sorted(s["inventory"].items(), key=lambda x: int(x[0])) if n]
        return _wrap({"account": {"index": acct["index"]}, "items": rows}, sim)
    if tool == "get_gas_balance" and s.get("gas"):
        return json.dumps({"balances": {"main": s["gas"]}}, indent=2)
    quests = {q["index"]: q for q in s.get("quests", [])}
    if tool == "lens_quests" and "quests" in s:
        return _wrap({"quests": [{"index": q["index"], "status": q["status"], "repeatable": q.get("repeatable", False)} for q in quests.values()]}, sim)
    if tool in ("quest_state", "check_quest_completable") and a.get("quest") in quests:
        q = quests[a["quest"]]
        # quirk completable-ignores-room: objectives only
        ok = q["status"] == "objectives_met" or (q["status"] == "active" and all(o["met"] for o in q.get("objectives", [])) and bool(q.get("objectives")))
        return json.dumps({"quest_index": q["index"], "state": q["status"], "completable": ok})
    if tool in ("get_status",):  # the simulated clock, so a model can watch a wait elapse
        return json.dumps({"status": "ok", "now_unix": int(sim.chain_clock()),
                           "now": datetime.fromtimestamp(sim.chain_clock(), timezone.utc).isoformat()})
    if tool == "workspace_list":
        return json.dumps(sorted(sim.notes))
    return None


def recorded_read(tool, args, sim):
    """v1 replay machinery: the pre-fork recorded reads (only while nothing has changed), then v1's world reads."""
    native = sim.spec["fork"].get("native_prefix") or {}
    own = native.get("recorded_reads", {}).get(tool, {}) if not sim.changed and sim.clock == sim.spec["fork"]["clock"]["unix"] else {}
    key = json.dumps({k: v for k, v in args.items() if k != "account"}, sort_keys=True)
    if key in own:
        return own[key]
    same = [v for k, v in own.items() if kd.same_read(tool, json.loads(k), args, own)]
    if len(same) == 1:
        return same[0]
    world = kd.GLOBAL_READS.get(tool, {})
    if key in world:
        return world[key]
    if world and not any(k.endswith(("index", "_id")) for k in args):
        return next(iter(world.values()))
    return None


class Episode:
    """One attempt on this adapter: tool calls in, harness-shaped text out, intent trace and simulator kept for the
    scorer. Stops at the session tool cap or when the session ends."""

    def __init__(self, spec):
        self.spec, self.sim = spec, sp.Simulator(spec)
        self.calls, self.quirks, self.misses = 0, [], []

    @property
    def over(self):
        return self.sim.ended or self.calls >= CAPS["session_tool_cap"]

    def _do(self, intent):
        q = quirk(intent, self.sim)
        if q:
            qid, res = q
            if qid:
                self.quirks.append(qid)
            return self.sim._record(intent, res, True)
        return self.sim.step(intent)

    def call(self, tool, args):
        if self.over:
            return "Session ended."
        self.calls += 1
        args = args or {}
        if tool == "act_sequence":
            rows = []
            for step in args.get("steps") or []:
                t, sa = kd.sequence_step(step)
                text = self.call_one(t, sa) if t else f"unknown op {step.get('op')!r}"
                rows.append({"op": step.get("op"), "status": "reverted" if kd.is_error_text(text) else "success", "result": text})
            return json.dumps({"steps": rows})
        return self.call_one(tool, args)

    def call_one(self, tool, args):
        intents = to_intents(tool, args, self.sim)
        if not intents:  # an empty batch: the harness refuses it before signing, and it is no intent
            return f"Error executing tool {tool}: validation failed; no transaction sent: empty request; {tool} requires at least one target"
        texts = []
        for it in intents:
            res = self._do(it)
            if it["verb"] == "read" and not res.get("from_state"):  # a response row answered the read
                text = act_text(tool, it, res, self.sim) if res["class"] != "ok" or res.get("native") else json.dumps(res.get("fields", {}))
            elif it["verb"] == "read":
                text = read_text(tool, it, self.sim) or recorded_read(tool, args, self.sim)
                if text is None:
                    self.sim.trace[-1].update(ok=False, result={"class": "gap"})
                    self.sim.gaps.append("read")
                    res = {"class": "gap"}
                    text = GAP_TEXT.format(tool=tool)
            elif it["verb"] == "note_read":
                p = it["args"].get("path")
                text = self.sim.notes[p] if p in self.sim.notes else f"no such file: '{p}'"
                if p not in self.sim.notes:
                    self.sim.trace[-1]["ok"] = False
            elif it["verb"] in ("note_write",):
                text = "ok"
            elif it["verb"] == "wait":
                text = "Waited for the requested time."
            elif it["verb"] == "schedule_wake":
                text = "Next session scheduled."
            elif it["verb"] == "end_session":
                text = "Session ended."
            elif it["verb"] == "unknown":
                text = f"Error executing tool {tool}: unknown tool"
            else:
                text = act_text(tool, it, res, self.sim)
            if res["class"] == "gap":
                self.misses.append(tool)
            texts.append(text)
        failed = [t for t in texts if kd.is_error_text(t)]
        return failed[0] if failed else texts[-1] if texts else "ok"

    def result(self):
        return {**sp.score(self.spec, self.sim), "calls": self.calls, "quirks": sorted(set(self.quirks)), "misses": self.misses}


def play_tools(spec, calls):
    """Run recorded or scripted (tool, args) calls through the adapter and score them (offline, no Inspect)."""
    ep = Episode(spec)
    for tool, args in calls:
        if ep.over:
            break
        ep.call(tool, args)
    return ep


def play_intents(spec, intents):
    """Reference play through this adapter: intents -> tool calls -> intents. None if an act has no tool here; reads,
    notes and unmapped tools from a recorded play that cannot be re-expressed are dropped (no rule scores them)."""
    calls = []
    for i in intents:
        c = from_intent(i)
        if c is None and i["verb"] not in sp.PASSIVE | {"other_act"}:
            return None
        calls += [c] if c else []
    return play_tools(spec, calls)


def native_battery(spec):
    """Harness-level degenerate plays the intent battery cannot express: empty batches and dry-run-only acts."""
    kamis = [k["index"] for k in spec["state"]["kamis"]]
    acts = [c for c in (from_intent(i) for p in spec["scoring"].get("reference_plays", []) if p["name"] == "oracle"
                        for i in p["intents"]) if c and c[0] in DRY_RUN_TOOLS]
    return {"empty_batches": [("level_and_allocate_batch", {"targets": []}), ("feed_level_allocate_batch", {"targets": []}),
                              ("harvest_stop", {"kami_ids": []}), ("harvest_collect", {"kami_ids": []}),
                              ("act_sequence", {"steps": []})],
            "dry_run_native": [(t, {**a, "dry_run": True}) for t, a in acts] + [("end_session", {})],
            "batch_all_kamis": [("harvest_collect", {"kami_ids": kamis}), ("end_session", {})]}


# ---------- rendering + Inspect ----------

def render_mode(spec):
    """First allowed rendering this adapter can do. native_replay needs a prefix; a foreign-harness prefix is served
    only when the drill is not harness-bound (v1 practice: KamiBench prefixes on the 4.7.0 surface)."""
    native = spec["fork"].get("native_prefix")
    for mode in spec["fork"]["rendering"]:
        if mode == "native_replay" and native and native.get("messages") and (
                native["harness"].startswith(HARNESS) or not spec["harness"]["bound"]):
            return mode
        if mode == "rerendered_history" and spec["fork"].get("history"):
            return mode
        if mode == "snapshot_brief":
            return mode
    return None


def render_fork(spec, mode=None):
    """Inspect messages up to the fork."""
    mode = mode or render_mode(spec)
    if mode == "native_replay":
        msgs = kd.to_messages(spec["fork"]["native_prefix"]["messages"])
    else:
        msgs, ep = [ChatMessageSystem(content=kd.SYSTEM), ChatMessageUser(content="Session start.")], Episode(spec)
        if mode == "snapshot_brief":
            reads = [("lens_roster", {}), ("get_gas_balance", {})]
            reads += [("workspace_read", {"path": p}) for p in (spec["fork"].get("notes") or {}) if p.startswith("journal/")]
            for n, (tool, args) in enumerate(reads):
                tid = f"brief_{n + 1}"
                msgs += [ChatMessageAssistant(content="", tool_calls=[ToolCall(id=tid, function=tool, arguments=args)]),
                         ChatMessageTool(content=ep.call(tool, args), tool_call_id=tid, function=tool)]
        elif mode == "rerendered_history":
            for n, e in enumerate(spec["fork"]["history"]):
                if e["actor"] == "user" and e.get("text"):
                    msgs.append(ChatMessageUser(content=e["text"]))
                elif e.get("intent") and (c := from_intent(e["intent"])):
                    tid = f"h_{n}"
                    res = e.get("result") or {"class": "ok"}
                    text = act_text(c[0], e["intent"], res, ep.sim) if e["intent"]["verb"] != "read" else json.dumps(res.get("fields", {}))
                    msgs += [ChatMessageAssistant(content=e.get("text") or "", tool_calls=[ToolCall(id=tid, function=c[0], arguments=c[1])]),
                             ChatMessageTool(content=text, tool_call_id=tid, function=c[0])]
                elif e.get("text"):
                    msgs.append(ChatMessageAssistant(content=e["text"]))
        else:
            raise ValueError(f"{spec['id']}: no rendering this adapter can do among {spec['fork']['rendering']}")
    if spec["decision_point"].get("closing"):
        msgs.append(ChatMessageUser(content=spec["decision_point"]["closing"]))
    return msgs


def episode_tools(ep):
    def make(name, desc, prm):
        async def run(**kwargs):
            return ep.call(name, kwargs)
        return ToolDef(run, name=name, description=desc, parameters=prm).as_tool()
    return [make(*s) for s in surface()]


@solver
def spec_agent(policy: str = "model"):
    async def solve(state, generate):
        spec = state.metadata["spec"]
        ep = Episode(spec)
        tools = episode_tools(ep)
        mode = policy or "null"
        scripted = []
        if mode == "oracle":
            oracle = next(p for p in spec["scoring"]["reference_plays"] if p["name"] == "oracle")
            scripted = [from_intent(i) for i in oracle["intents"]]
        for turn in range(CAPS["max_turns"]):
            if mode == "null" or ep.over:
                break
            if mode == "oracle":
                if turn >= len(scripted) or scripted[turn] is None:
                    break
                out = ModelOutput.for_tool_call("oracle", *scripted[turn])
            else:
                out = await get_model().generate(state.messages, tools)
            state.messages.append(out.message)
            if not out.message.tool_calls:
                break
            state.messages.extend((await execute_tools(state.messages, tools)).messages)
        state.metadata["result"] = {k: v for k, v in ep.result().items()}
        state.metadata["trace"] = [{k: v for k, v in i.items() if k != "result"} for i in ep.sim.trace]
        return state
    return solve


@scorer(metrics=[mean(), stderr()])
def spec_score():
    """Graded score (0..1); `pass` in metadata is the binary verdict item_stats/pass@1 use."""
    async def score(state, target):
        r = state.metadata["result"]
        return Score(value=r["score"], answer=json.dumps([i["verb"] for i in state.metadata["trace"]]),
                     explanation="; ".join(r["why"]) or "pass",
                     metadata={"pass": r["pass"], "quirks": r["quirks"], "misses": r["misses"], "adapter": ID})
    return score


@task
def spec_v2(paths: str = "drills_v2/spec", policy: str = "model"):
    """v2 drill specs on kami-harness 4.7.0. Drills this adapter cannot render are left out (see `kami-drills spec check`)."""
    samples = []
    # inspect runs the task from this file's directory, so relative paths are taken from the repo root
    for s in sp.load([q if Path(q).is_absolute() else ROOT / q for q in paths.split(",")]):
        if sp.validate(s) or render_mode(s) is None or s["scoring"]["kind"] not in sp.SCORERS:
            continue
        samples.append(Sample(id=s["id"], input=render_fork(s), metadata={"spec": s, "family": s["family"]}))
    return Task(dataset=samples, solver=spec_agent(policy), scorer=spec_score(), fail_on_error=False,
                metadata={"adapter": ID, "stack": STACK}, config=GenerateConfig(max_tokens=4096))


# ---------- normalize (raw 4.7.0 / lens 1.0.5 reads -> spec state) and tool patterns -> intent patterns ----------

def normalize_snapshot(calls):
    """Season 1 session-start snapshot `calls` [{name, args, response}] -> (state, catalog items, recorded_reads)."""
    by = {c["name"]: c["response"] for c in calls if isinstance(c.get("response"), dict)}
    acct = (by.get("lens_account") or {}).get("data") or {}
    state = {"account": {"index": acct.get("index", 0), "room": acct.get("roomIndex", 0)}, "kamis": [], "inventory": {}}
    if "musu" in acct:
        state["account"]["musu"] = acct["musu"]
    if st := acct.get("stamina"):
        state["account"].update(stamina=st.get("current"), stamina_max=st.get("total"))
    gas = ((by.get("get_gas_balance") or {}).get("balances") or {}).get("main") or {}
    if gas:
        state["gas"] = {k: gas[k] for k in ("operator_eth", "owner_eth", "owner_mainnet_eth") if gas.get(k) is not None}
    for k in ((by.get("lens_party") or {}).get("data") or {}).get("kamis", []):
        kami = {"index": k["index"], "state": k["state"], "hp": k["hp"]["current"], "hp_max": k["hp"]["total"]}
        for src, dst in (("level", "level"), ("xp", "xp"), ("xpRequired", "xp_required"), ("skillPoints", "skill_points"),
                         ("levelUpReady", "level_up_ready"), ("cooldownUntil", "cooldown_until_unix")):
            if k.get(src) is not None:
                kami[dst] = k[src]
        if k.get("hpRatePerHr") is not None:
            kami["hp_rate_per_hr"] = float(k["hpRatePerHr"])
        if k["state"] == "HARVESTING" and k.get("node"):
            kami["harvest"] = {"node": k["node"]["index"], "accrued_musu": (k.get("musu") or {}).get("accrued", 0)}
            if (k.get("musu") or {}).get("spotRatePerHr"):
                kami["harvest"]["rate_per_hr"] = float(k["musu"]["spotRatePerHr"])
        state["kamis"].append(kami)
    items = {}
    for row in ((by.get("lens_inventory") or {}).get("data") or {}).get("items", []):
        state["inventory"][str(row["item"]["index"])] = row["balance"]
        items[str(row["item"]["index"])] = {k: row["item"][k] for k in ("name", "type", "for") if k in row["item"]}
    q = (by.get("lens_quests") or {}).get("data") or {}
    if q.get("accepted") is not None:
        rep = {r["index"]: r.get("repeatable", False) for r in q.get("registry", [])}
        state["quests"] = [{"index": a["index"], "status": "complete" if a["complete"] else "active", "repeatable": rep.get(a["index"], False)}
                           for a in q["accepted"]]
    reads = {}
    for c in calls:
        if "response" not in c:  # a snapshot read that failed (S1: lens outages) has no response to replay
            continue
        key = json.dumps({k: v for k, v in (c.get("args") or {}).items() if k != "account"}, sort_keys=True)
        reads.setdefault(c["name"], {})[key] = json.dumps(c["response"], indent=2)
    return state, items, reads


# tool -> verb for converting harness-level rules (v1 / miner candidates). Batch tools map to their per-kami verb.
TOOL_VERB = {"move_to_room": "travel", "travel_to_room": "travel", "feed_kami": "feed", "revive_kami": "revive",
             "level_up_kami": "level_up", "level_to": "level_up", "level_and_allocate_batch": "level_up",
             "feed_level_allocate_batch": "level_up", "allocate_skills": "upgrade_skill", "pool_swap": "swap",
             "set_next_wake": "schedule_wake", "scavenge_claim_and_reveal": "scavenge_claim", "liquidate_kami": "liquidate",
             "listing_buy": "buy", "auction_buy": "buy", "bridge_eth_from_mainnet": "bridge", "skill_respec": "respec",
             "workspace_write": "note_write", "workspace_read": "note_read", "pool_swap_quote": "quote"}


def tool_pattern_to_intent(p):
    """v1 tool pattern -> intent pattern(s). `dry_run: {not: true}` is dropped (dry runs are quotes, never acts); list
    args matched with `contains` become the per-kami scalar. Alternatives that collapse onto one verb keep the most
    constrained (a batch tool's pattern without a kami must not widen a per-kami rule)."""
    if isinstance(p, list):
        alts = [x for q in p for x in kd.as_list(tool_pattern_to_intent(q))]
        keep = [a for a in alts if not any(b is not a and b["verb"] == a["verb"] and set(a.get("args", {})) < set(b.get("args", {}))
                                           for b in alts)]
        return [k for i, k in enumerate(keep) if k not in keep[:i]]
    if isinstance(p, str):
        p = {"tool": p}
    out = []
    for tool in kd.as_list(p["tool"]):
        verb = TOOL_VERB.get(tool, "read" if tool.startswith("lens_") or tool in READ_TOOLS else tool)
        args = {}
        for k, v in (p.get("args") or {}).items():
            if k in DROP:
                continue
            if k == "kami_ids":
                k, v = "kami", v.get("contains") if isinstance(v, dict) and "contains" in v else v
            args[CANON.get(k, k)] = v
        if verb == "read":
            args = {"kind": tool.removeprefix("lens_"), **args}
        if verb == "quote":
            args = {"of": "swap", **args}
        pat = {"verb": verb, **({"args": args} if args else {})}
        if p.get("ok"):
            pat["ok"] = True
        out.append(pat)
    return out[0] if len(out) == 1 else out


def tool_rule_to_intent(rule):
    """v1/miner rule in tool patterns -> the same rule over intents."""
    r, conv = dict(rule), lambda ps: kd.as_list(tool_pattern_to_intent(ps))
    for k in ("require_any", "forbid"):
        if k in r:
            r[k] = conv(r[k])
    if "require_sequence" in r:
        r["require_sequence"] = [tool_pattern_to_intent(p) for p in r["require_sequence"]]
    if "require_all" in r:
        r["require_all"] = [conv(g) for g in r["require_all"]]
    if "forbid_before" in r:
        r["forbid_before"] = [{"forbid": conv(fb["forbid"]), "before": conv(fb["before"])} for fb in r["forbid_before"]]
    return r
