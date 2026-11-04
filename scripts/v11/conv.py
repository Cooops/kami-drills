"""Mechanical v1 drill -> v2 spec overlay (the game half), verdict-equivalent target. Scratch tool for kami-drills v1.1.
Run with the kami-drills-v11 venv: .venv/bin/python -I conv.py (imports kami_drills from the worktree)."""
import copy
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

WT = Path(__file__).resolve().parents[2]  # repo root
sys.path.insert(0, str(WT))
from kami_drills import spec as sp  # noqa: E402
from kami_drills import task as kd  # noqa: E402
from kami_drills.adapters import harness_4_7 as h  # noqa: E402

DATA = Path(os.environ.get("KAMIARENA_DATA", "../kamiarena/data")).expanduser()  # recorded v1 runs, outside this repo
NATIVE = sp.NATIVE_ANY  # v1 replay text: verbatim on any adapter unless the drill is harness-bound (docs/v1.1.md)
BATCH = {"level_to", "allocate_skills", "level_and_allocate_batch", "feed_level_allocate_batch"}  # covered by per-intent rows
FOODS = {11301, 11303, 11304, 11305, 11311, 11312, 11313, 11314, 11315, 11316, 11317, 11318, 11319, 11320, 11321, 11322,
         11323, 11324, 11325}
NAMES = {}


def _catalog():
    g = json.loads((WT / "drills/_global_reads.json").read_text())
    for v in g.get("lens_items", {}).values():  # the recording is truncated: pull rows by regex
        for m in re.finditer(r'"index": (\d+),\s*"name": "([^"]*)",\s*"type": "([A-Z_]+)"', v):
            NAMES[int(m.group(1))] = {"name": m.group(2), "type": m.group(3)}


_catalog()


def item_type(i):
    try:
        return NAMES.get(int(i), {}).get("type")
    except (TypeError, ValueError):
        return None


def kami_arg(v):
    if isinstance(v, dict) and "contains" in v:
        return v["contains"]
    if isinstance(v, list) and len(v) == 1:
        return v[0]
    if isinstance(v, dict) and "in" in v and all(isinstance(x, list) and len(x) == 1 for x in v["in"]):
        return {"in": [x[0] for x in v["in"]]}
    return v


def canon(args, keep=None):
    out = {}
    for k, v in (args or {}).items():
        if k in h.DROP or (keep is not None and k not in keep):
            continue
        out[h.CANON.get(k, k)] = v
    return out


def pat1(tool, args, ok=None):
    """One v1 tool pattern -> intent pattern, or None if it only matched a dry run (dry runs are quotes, answered by the
    act's own rows). Mirrors h.to_intents."""
    a = dict(args or {})
    if a.get("dry_run") is True:
        return None
    a.pop("dry_run", None)
    P = lambda verb, **x: {"verb": verb, **({"args": {k: v for k, v in x.items() if v is not None}} if any(v is not None for v in x.values()) else {})}
    if tool in ("harvest_start", "harvest_stop", "harvest_collect"):
        p = P(tool, kami=kami_arg(a["kami_ids"]) if "kami_ids" in a else None, node=a.get("node_index") if tool == "harvest_start" else None)
    elif tool == "move_to_room":  # one adjacent step (hop); travel_to_room pathfinds
        p = P("travel", room=a.get("room_index"), hop=True)
    elif tool == "travel_to_room":
        p = P("travel", room=a.get("target_room"), hop={"not": True})
    elif tool == "feed_kami":
        p = P("feed", kami=a.get("kami_id"), item=a.get("food_item_id"))
    elif tool == "revive_kami":
        p = P("revive", kami=a.get("kami_id"), method=a.get("method"))
    elif tool == "use_item_batch":
        it = a.get("item_id")
        verb = "revive" if it in h.REVIVE_ITEMS else "feed" if (item_type(it) == "FOOD" or it in FOODS) else "use_item"
        p = P(verb, kami=a.get("kami_id"), **({"method": h.REVIVE_ITEMS[it]} if verb == "revive" else {"item": it}))
    elif tool in ("level_up_kami", "level_to", "level_and_allocate_batch", "feed_level_allocate_batch"):
        p = P("level_up", kami=a.get("kami_id"))
    elif tool in ("upgrade_skill", "allocate_skills"):
        p = P("upgrade_skill", kami=a.get("kami_id"), skill=a.get("skill_index"))
    elif tool == "skill_respec":
        p = P("respec", kami=a.get("kami_id"))
    elif tool in ("accept_quest", "complete_quest", "drop_quest"):
        p = P(tool, quest=a.get("quest_index"))
    elif tool == "listing_buy":
        ii = a.get("item_indices")
        p = P("buy", item=ii.get("contains") if isinstance(ii, dict) and "contains" in ii else kami_arg(ii) if ii is not None else None,
              venue=f"merchant:{a['merchant_index']}" if "merchant_index" in a else {"regex": "^merchant:"})
    elif tool == "auction_buy":
        p = P("buy", item=a.get("item_index"), venue="auction")
    elif tool == "newbie_vendor_buy":
        p = P("buy", item="kami", venue="newbie_vendor", max_unit_price=a.get("max_price_eth"), kami=a.get("kami_index"))
    elif tool == "buy_kami":
        p = P("buy", item="kami", venue="kamiswap", max_total_price=a.get("max_total_eth"), kami=kami_arg(a["kami_ids"]) if "kami_ids" in a else None)
    elif tool == "pool_swap":
        p = P("swap", item_in=a.get("item_in"), item_out=a.get("item_out"), amount_in=a.get("amount_in"), min_out=a.get("min_amount_out"))
    elif tool == "pool_swap_quote":
        p = {"verb": "quote", "args": {"of": "swap", **canon(a)}}
    elif tool in ("portal_deposit", "portal_withdraw"):
        p = P(tool, item=a.get("item"), amount=a.get("amount"))
    elif tool == "bridge_eth_from_mainnet":
        p = P("bridge", amount_eth=a.get("amount_eth"))
    elif tool in ("fund_operator", "withdraw_operator"):
        p = P(tool, amount_eth=a.get("amount_eth"))
    elif tool in ("craft_item", "speed_craft_batch"):
        p = P("craft", recipe=a.get("recipe_index"))
    elif tool in ("scavenge_claim", "scavenge_claim_and_reveal"):
        p = P("scavenge_claim", node=a.get("node_index"))
    elif tool == "liquidate_kami":
        p = P("liquidate", kami=a.get("killer_kami_id"), victim=a.get("victim_kami_id"))
    elif tool in ("take_trade", "complete_trade", "create_trade", "cancel_trade", "complete_all_trades", "list_kami", "cancel_kami_listing"):
        p = {"verb": "trade", "args": {"action": tool, **canon(a)}}
    elif tool in ("transfer_kami", "transfer_items"):
        p = {"verb": "transfer", "args": {"action": tool, **canon(a)}}
    elif tool in ("gacha_use", "gacha_reroll", "gacha_reveal"):
        p = {"verb": "gacha", "args": {"action": tool, **canon(a)}}
    elif tool in ("register_account", "create_operator_wallet"):
        p = P("register")
    elif tool == "name_kami":
        p = P("name", kami=a.get("kami_id"))
    elif tool in ("equip_item", "unequip_item"):
        p = P(tool.split("_")[0], kami=a.get("kami_id"), item=a.get("item_index"))
    elif tool in ("use_account_item", "cast_item"):
        p = P("use_item", kami=a.get("target_kami_id"), item=a.get("item_id", a.get("item_index")))
    elif tool == "wait":
        p = P("wait", seconds=a.get("seconds"))
    elif tool == "set_next_wake":
        p = P("schedule_wake", minutes=a.get("minutes_from_now"))
    elif tool == "end_session":
        p = P("end_session")
    elif tool in ("workspace_write", "workspace_delete"):
        p = P("note_write", path=a.get("path"), content=a.get("content"))
    elif tool == "workspace_read":
        p = P("note_read", path=a.get("path"))
    elif tool.startswith("lens_") or tool in h.READ_TOOLS:
        p = {"verb": "read", "args": {"kind": tool.removeprefix("lens_"), **canon(a)}}
    elif tool in h.SURFACE_NAMES or tool in kd.OUTSOURCE:
        p = P("other_act", tool=tool)
    else:
        p = P("unknown", tool=tool)
    if ok:
        p["ok"] = True
    return p


def pat(p):
    """v1 pattern (str, dict with tool str|list, or list) -> intent pattern(s) as a flat list of alternatives."""
    if isinstance(p, list):
        out = []
        for q in p:
            out += pat(q)
        return [x for i, x in enumerate(out) if x not in out[:i]]
    if isinstance(p, str):
        p = {"tool": p}
    out = []
    for t in kd.as_list(p["tool"]):
        q = pat1(t, p.get("args"), p.get("ok"))
        if q is None:
            continue
        if "ratio" in p:
            q["ratio"] = {**p["ratio"], "num": h.CANON.get(p["ratio"]["num"], p["ratio"]["num"]),
                          "den": h.CANON.get(p["ratio"]["den"], p["ratio"]["den"])}
        out.append(q)
    return [x for i, x in enumerate(out) if x not in out[:i]]


def one(p):
    ps = pat(p)
    return ps[0] if len(ps) == 1 else ps


def rule(r):
    out = {}
    for k, v in r.items():
        if k in ("require_any", "forbid"):
            out[k] = pat(v)
        elif k == "require_all":
            out[k] = [pat(g) for g in v]
        elif k == "require_sequence":
            out[k] = [one(s) for s in v]
        elif k == "forbid_before":
            out[k] = [{"forbid": pat(x["forbid"]), "before": pat(x["before"])} for x in v]
        elif k == "forbid_before_any":
            out.setdefault("forbid_before", []).append({"forbid": pat(v), "before": pat(r["before"])})
        elif k == "before":
            continue
        else:
            out[k] = v
    return out


# ---------- state from the prefix ----------

def _j(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        m = re.search(r"\{.*\}", text or "", re.S)
        try:
            return json.loads(m.group(0)) if m else None
        except ValueError:
            return None


def prefix_calls(prefix):
    res = {m.get("tool_call_id"): m for m in prefix if m.get("role") == "tool_result"}
    out = []
    for m in prefix:
        for tc in m.get("tool_calls") or []:
            r = res.get(tc["id"])
            if r is not None:
                out.append((tc["name"], tc.get("args") or {}, r.get("content") or "", bool(r.get("is_error"))))
    return out


def hp_of(x):
    if isinstance(x, list):
        return x[0], x[1]
    if isinstance(x, dict):
        return x.get("current"), x.get("total")
    return None, None


def iso(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def state_from_prefix(v1):
    st = {"account": {"index": 0, "room": 0}, "kamis": [], "inventory": {}}
    kamis, last_ts, last_block, quests = {}, None, None, {}
    for tool, args, text, err in prefix_calls(v1["prefix"]):
        d = _j(text) if not err else None
        meta = (d or {}).get("meta") if isinstance(d, dict) else None
        if meta:
            last_ts = iso(meta.get("servedAt")) or last_ts
            last_block = meta.get("blockNumber") or last_block
        data = (d or {}).get("data") if isinstance(d, dict) else None
        if tool in ("lens_roster", "lens_party") and isinstance(data, dict):
            acct = data.get("account") or {}
            if acct.get("index"):
                st["account"]["index"] = acct["index"]
            if acct.get("roomIndex") is not None:
                st["account"]["room"] = acct["roomIndex"]
            for k in data.get("kamis") or []:
                cur, tot = hp_of(k.get("hp"))
                kk = kamis.setdefault(k["index"], {"index": k["index"]})
                kk.update(state=k.get("state", kk.get("state")), **({"hp": cur, "hp_max": tot} if tot else {}))
                if k.get("level") is not None:
                    kk["level"] = k["level"]
        elif tool == "lens_kami" and isinstance(data, dict) and data.get("index"):
            kk = kamis.setdefault(data["index"], {"index": data["index"]})
            cur, tot = hp_of(data.get("hp"))
            kk.update(state=data.get("state", kk.get("state")), **({"hp": cur, "hp_max": tot} if tot else {}))
            for s, t in (("level", "level"), ("xp", "xp"), ("xpRequired", "xp_required"), ("levelUpReady", "level_up_ready"),
                         ("skillPoints", "skill_points")):
                if isinstance(data.get(s), (int, bool)):
                    kk[t] = data[s]
            if isinstance(data.get("xp"), dict):
                for s, t in (("current", "xp"), ("required", "xp_required")):
                    if isinstance(data["xp"].get(s), int):
                        kk[t] = data["xp"][s]
            try:
                kk["hp_rate_per_hr"] = float(data.get("hpRatePerHr"))
            except (TypeError, ValueError):
                pass
            if data.get("cooldownSec") and last_ts:
                kk["cooldown_until_unix"] = last_ts + data["cooldownSec"]
            node = data.get("node") or (data.get("harvest") or {}).get("node")
            if data.get("state") == "HARVESTING" and isinstance(node, dict) and node.get("index") is not None:
                kk["harvest"] = {"node": node["index"]}
            acct = data.get("account") or {}
            if acct.get("index"):
                st["account"]["index"] = acct["index"]
        elif tool == "lens_account" and isinstance(data, dict):
            if data.get("index"):
                st["account"]["index"] = data["index"]
            if data.get("roomIndex") is not None:
                st["account"]["room"] = data["roomIndex"]
            stam = data.get("stamina")
            if isinstance(stam, dict) and isinstance(stam.get("current"), int):
                st["account"]["stamina"] = stam["current"]
                if isinstance(stam.get("total"), int):
                    st["account"]["stamina_max"] = stam["total"]
        elif tool == "lens_inventory" and isinstance(data, dict):
            st["inventory"] = {}
            for row in data.get("items") or []:
                it = row.get("item") or {}
                if it.get("index") is not None and isinstance(row.get("balance"), int):
                    st["inventory"][str(it["index"])] = row["balance"]
                    NAMES.setdefault(it["index"], {k: it[k] for k in ("name", "type") if k in it})
        elif not err and tool in ("travel_to_room", "move_to_room") and not args.get("dry_run"):
            room = args.get("target_room", args.get("room_index"))
            dd = d if isinstance(d, dict) else {}
            room = dd.get("final_room", dd.get("room", dd.get("current_room", room)))
            if isinstance(room, int):
                st["account"]["room"] = room
        elif not err and tool in ("harvest_start", "harvest_stop") and not args.get("dry_run"):
            for k in args.get("kami_ids") or []:
                kk = kamis.setdefault(k, {"index": k})
                kk["state"] = "HARVESTING" if tool == "harvest_start" else "RESTING"
                if tool == "harvest_start":
                    kk["harvest"] = {"node": args.get("node_index")}
                else:
                    kk.pop("harvest", None)
        elif not err and tool in ("level_up_kami",) and not args.get("dry_run"):
            kk = kamis.setdefault(args.get("kami_id"), {"index": args.get("kami_id")})
            if "level" in kk:
                kk["level"] += 1
        elif not err and tool in ("accept_quest", "complete_quest") and not args.get("dry_run"):
            quests[args.get("quest_index")] = "active" if tool == "accept_quest" else "complete"
    for k in kamis.values():
        k.setdefault("state", "RESTING")
        if k.get("hp") is None or not k.get("hp_max"):
            k["hp"], k["hp_max"] = k.get("hp") or 0, k.get("hp_max") or max(k.get("hp") or 0, 1)
        if k["state"] not in ("RESTING", "HARVESTING", "DEAD", "LISTED", "EXTERNAL"):
            k["state"] = "RESTING"
        k["hp"] = max(0, int(k["hp"]))
        if k.get("harvest") and k["harvest"].get("node") is None:
            k.pop("harvest")
    st["kamis"] = list(kamis.values())
    if quests:
        st["quests"] = [{"index": q, "status": s} for q, s in quests.items() if isinstance(q, int)]
    return st, last_ts, last_block


# ---------- rows ----------

def result_of(row):
    text = row["response"]
    if row.get("is_error") or kd.is_error_text(text):
        cls = "revert" if "reverted" in text.lower() or "revert:" in text.lower() else "validation_error"
        r = {"class": cls, "reason": h.reason_code(text)}
    else:
        r = {"class": "ok"}
    r["native"] = {NATIVE: text}
    return r


def deltas(p, st):
    """Default state edits for an ok act so state-formatted reads stay consistent."""
    a, v, out = p.get("args") or {}, p["verb"], []
    idx = {k["index"]: i for i, k in enumerate(st["kamis"])}
    kami = a.get("kami") if isinstance(a.get("kami"), int) else None
    if v == "travel" and isinstance(a.get("room"), int):
        out.append({"path": "/account/room", "op": "set", "value": a["room"]})
    if kami in idx:
        i = idx[kami]
        if v == "harvest_start":
            out.append({"path": f"/kamis/{i}/state", "op": "set", "value": "HARVESTING"})
            if isinstance(a.get("node"), int):
                out.append({"path": f"/kamis/{i}/harvest", "op": "set", "value": {"node": a["node"]}})
        elif v == "harvest_stop":
            out += [{"path": f"/kamis/{i}/state", "op": "set", "value": "RESTING"}, {"path": f"/kamis/{i}/harvest", "op": "set", "value": None}]
        elif v == "level_up" and "level" in st["kamis"][i]:
            out.append({"path": f"/kamis/{i}/level", "op": "add", "value": 1})
        elif v == "revive":
            out.append({"path": f"/kamis/{i}/state", "op": "set", "value": "RESTING"})
    return out


def rows(v1, st, clock):
    out = []
    for r in v1["replay"]:
        if r["tool"] in BATCH or r["tool"] == "act_sequence":
            continue
        p = pat1(r["tool"], r.get("args"))
        if p is None:
            continue
        row = {"match": p}
        for k in ("when", "unless", "sets", "clears"):
            if r.get(k) not in (None, [], ""):
                row[k] = r[k]
        if r.get("waited_gte"):
            row["clock_gte"] = clock + r["waited_gte"]
        if r.get("waited_lt") is not None:
            row["clock_lt"] = clock + r["waited_lt"]
        row["provenance"] = "authored" if r.get("src") else "recorded"
        res = result_of(r)
        if res["class"] == "ok" and p["verb"] not in sp.PASSIVE:
            if dl := deltas(p, st):
                res["state_delta"] = dl
        row["result"] = res
        out.append(row)
    # collapsed tools (move/travel, batch) share a verb: specific rows before catch-alls, else stable (v1 first-match)
    catch = lambda r: not set(r["match"].get("args") or {}) - {"hop"} and not any(k in r for k in ("when", "unless", "clock_gte", "clock_lt"))
    return _stable(out, catch)


def _stable(out, catch):
    """Within each verb, keep v1 order but move catch-all rows after every non-catch-all row of that verb."""
    res, tail = [], {}
    for r in out:
        v = json.dumps(r["match"].get("verb")) + (r["match"].get("args") or {}).get("kind", "") if r["match"]["verb"] == "read" else json.dumps(r["match"].get("verb"))
        if catch(r):
            tail.setdefault(v, []).append(r)
        else:
            res.append(r)
    for v in tail:
        res += tail[v]
    return res


# ---------- plays ----------

def intents_of(calls, spec):
    sim = sp.Simulator(spec)
    out = []
    for tool, args in calls:
        for i in h.to_intents(tool, args, sim):
            out.append(i)
            sim.step(copy.deepcopy(i))
    return out


def source_lines(v1):
    src = v1["src"]
    base = DATA / "hosted" if src.startswith("hosted/") else DATA / "kamibench"
    p = base / src.removeprefix("hosted/") if src.startswith("hosted/") else base / src
    if not p.exists():
        p = DATA / "kamibench" / src
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def recorded_next(v1):
    """The source agent's calls after the fork: the next assistant turn(s) with tool calls, up to 3 calls."""
    try:
        L = source_lines(v1)
    except OSError:
        return []
    calls = []
    for m in L[v1["fork"]:]:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            calls += [(tc["name"], tc.get("args") or {}) for tc in m["tool_calls"]]
            break
    return calls


MISTAKE = {"onboarding": "wrong_order", "progression": "missed_opportunity", "levelup": "wrong_order", "memory": "belief_error",
           "repeat_failed": "repeat_failed", "risk": "unmanaged_risk", "stale_state": "stale_state", "stalling": "poverty_stall"}
COMP = {"onboarding": ["quest_planning", "tool_use"], "progression": ["quest_planning"], "levelup": ["state_tracking"],
        "memory": ["memory", "state_tracking"], "repeat_failed": ["error_recovery"], "risk": ["risk_survival"],
        "stale_state": ["state_tracking"], "stalling": ["persistence"]}


def harness_name(v1h):
    if "Luna" in v1h or v1h.startswith("4."):
        return f"kami-harness 4.7.0 df7129f ({v1h})" if "Luna" in v1h else f"kami-harness {v1h}"
    return v1h


def overlay(v1):
    st, ts, block = state_from_prefix(v1)
    clock = int(ts or 1787000000) + 5
    spec = {
        "spec_version": "2.0.0", "id": v1["id"], "version": 2, "status": "draft", "family": "decision",
        "category": v1["category"], "competency": COMP.get(v1["category"], ["state_tracking"]),
        "mistake_class": MISTAKE.get(v1["category"], "stale_state"), "difficulty": v1["difficulty"], "tier": "A",
        "situation": v1["note"], "summary": {k: v for k, v in (v1.get("summary") or {}).items() if k in ("situation", "mistake", "correct", "pass")},
        "objective": {"game": "complete as many quests as possible", "drill": v1["summary"].get("correct") or v1["note"]},
        "fork": {"kind": "mid_session", "rendering": ["native_replay"], "clock": {"unix": clock, **({"block": block} if block else {})},
                 "native_prefix": {"harness": harness_name(v1["harness"]), "messages": [], "recorded_reads": {}}},
        "state": st, "decision_point": {"budget": {"tool_calls": 50, "turns": 30}},
    }
    foods = {int(i) for i in st["inventory"] if (NAMES.get(int(i)) or {}).get("type") == "FOOD"}
    items = {str(i): NAMES[i] for i in sorted(foods | {i for i in map(int, st["inventory"]) if i in NAMES}) if i in NAMES}
    spec["world"] = {"catalog": {"items": items}} if items else {}
    spec["responses"] = rows(v1, st, clock)
    spec["scoring"] = {"kind": "rule", "rule": rule(v1["rule"]), "null_play": {"policy": h.NULL_PLAY, "expected": 0}, "reference_plays": []}
    spec["scoring"]["reference_plays"].append({"name": "oracle", "intents": intents_of(v1["oracle"], spec), "expected": 1})
    nxt = recorded_next(v1)
    if nxt:
        spec["scoring"]["reference_plays"].append({"name": "recorded-mistake", "is_recorded_mistake": True,
                                                   "intents": intents_of(nxt, spec), "expected": 0})
    spec["state_dependencies"] = [{"path": "/responses", "why": "v1 replay rows carry the verdict flags", "source": "v1 drills/" + v1["id"] + ".json", "visible_at_fork": True}]
    spec["variants"] = {"origin": "converted_v1"}
    spec["harness"] = {"bound": False, "recorded_on": {"harness": v1["harness"]}}
    src = v1["src"]
    m = re.search(r"session-(\d+)", src)
    spec["provenance"] = {"credit": v1["provenance"].get("credit", "KamiBench"), "license": "CC-BY-4.0",
                          "source": {"dataset": ("kamibench/" + src.split("/")[0]) if not src.startswith("hosted/") else "hosted/" + src.split("/")[1],
                                     "run": src.split("/")[1] if not src.startswith("hosted/") else src.split("/")[1],
                                     "session": int(m.group(1)) if m else None, "fork_line": v1["fork"], "v1_id": v1["id"]},
                          "chain_block": block}
    if spec["provenance"]["source"]["session"] is None:
        del spec["provenance"]["source"]["session"]
    spec["heldout"] = {"flag": False, "method": "hash_split"}
    return spec


def load_v1(i):
    return json.loads((WT / "drills" / f"{i}.json").read_text())


def all_ids():
    return sorted(p.stem for p in (WT / "drills").glob("*.json") if not p.stem.startswith("_"))
