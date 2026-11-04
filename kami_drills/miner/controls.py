"""Genuine controls for items a constant policy passes (F review B2, standard 2).

"Level every kami" is the right play on a session start where the only kami is ready, so the level-up item alone
cannot tell a model that reads levelUpReady from one that levels blindly. Each such item is paired (variants.group)
with a control from the same run where the same constant policy is wrong, and check_bank requires every degenerate play
to pass at most half the pair. A control is a real session start whose recorded play already passes it:

  always_level         a RESTING kami that is not ready (the chain refuses, XP below cost), or a ready kami at the
                       level cap of the node the agent harvests next (levelling locks it out: gpt-6.1-sol-3 s56)
  complete_all_quests  an accepted quest whose objectives are not met
  always_stop          a healthy HARVESTING kami (>= 50% HP, off cooldown) the agent kept harvesting

The control forbids that one act on that target; anything else passes it.
"""
import json
import math

from kami_drills.miner.rules import (NODE_CAP, SHARD, TOKENS, TRAP_OPERATOR, TRAP_OWNER, available_quests, deposit_cap, infra_session,
                                     pat, snap_kamis, wrote_kami)
from kami_drills.miner.trace import state_at
from kami_drills.spec.from_miner import _id, stamp

LEVEL_TOOLS = ("level_up_kami", "level_to", "level_and_allocate_batch", "feed_level_allocate_batch")


def _target(s, play):
    """(tool pattern, target description, dependencies) for a control at session s's start, or None."""
    if not s.snapshot or s.misaligned or infra_session(s):
        return None
    st = state_at(s, s.scaffold_end)
    ks = snap_kamis(s)
    if play == "always_level":
        for k, v in sorted(ks.items()):
            if v.get("state") != "RESTING" or any(wrote_kami(c, k, LEVEL_TOOLS) or (c.tool in LEVEL_TOOLS and k in c.kami_ids())
                                                   for c in s.calls):
                continue
            nodes = [c.args.get("node_index") for c in s.calls if c.ok and c.tool == "harvest_start" and k in c.kami_ids()]
            capped = v.get("levelUpReady") and v.get("level") is not None and any(
                NODE_CAP.get(n) is not None and v["level"] >= NODE_CAP[n] for n in nodes)
            if v.get("levelUpReady") is False or capped:
                why = (f"kami {k} is at level {v.get('level')}, the cap of node {nodes[0]} it harvests next: levelling locks it out"
                       if capped else f"kami {k} is not ready to level (the chain refuses)")
                return ([pat(t, kami=k) for t in LEVEL_TOOLS], why,
                        [st.kami_obs(k, "levelup_ready").dep(f"kami.{k}.levelUpReady"), st.kami_obs(k, "state").dep(f"kami.{k}.state")])
    if play == "complete_all_quests":
        said = {c["args"]["quest_index"]: bool(c["response"].get("completable")) for c in s.snap_calls("check_quest_completable")}
        for q, ok in sorted(said.items()):
            if not ok and not any(c.tool == "complete_quest" and c.args.get("quest_index") == q for c in s.calls):
                return [pat("complete_quest", quest_index=q)], f"accepted quest {q} is not completable", \
                    [st.quest[q]["completable"].dep(f"quest.{q}.completable")]
    if play == "always_stop":
        t0 = s.start.timestamp() if s.start else 0
        for k, v in sorted(ks.items()):
            hp = v.get("hp") or {}
            if v.get("state") == "HARVESTING" and hp.get("total") and hp.get("current", 0) >= 0.5 * hp["total"] \
                    and (v.get("cooldownUntil") or 0) < t0 and not any(c.tool == "harvest_stop" and k in c.kami_ids() for c in s.calls):
                return [pat("harvest_stop", kami=k)], f"kami {k} is harvesting at {hp['current']}/{hp['total']} HP: stopping forfeits the harvest", \
                    [st.kami_obs(k, "state").dep(f"kami.{k}.state"), st.kami_obs(k, "hp").dep(f"kami.{k}.hp")]
    return None


def control_for(run, item, play, used):
    """The nearest session start (by session number, then earlier first) in the item's run that controls `play`."""
    from kami_drills.miner.pipeline import gate_candidate, candidate, fork_state
    n0 = item["decision_point"]["session"]
    for s in sorted(run.sessions, key=lambda x: (abs(x.n - n0), x.n)):
        if (s.n, play) in used:
            continue
        tgt = _target(s, play)
        if tgt is None:
            continue
        pats, why, deps = tgt
        raw = {"fork_line": s.scaffold_end, "fork_kind": "session_start", "kind": "control", "mistake_class": "none",
               "objective": {"game": None, "source": "control", "shown_to_model": False},
               "agent_action": {"summary": "control: the recorded session did not make the forbidden act"},
               "situation": f"Session {s.n} start: {why}.", "expected": "anything but that act", "actual": "the agent did not",
               "why": f"control for {item['id']} against the constant policy {play}",
               "scoring": {"options": [{"credit": 1.0, "steps": []}], "zero_if": [{"any": pats, "why": why}]},
               "oracle": [], "accepted_actions": ["anything but the forbidden act"], "dependencies": deps,
               "severity": {"value": 0, "unit": "control", "score": 0, "detail": {}},
               "signature": ("control", play), "group": item["id"], "id_suffix": "ctl"}
        meta = {"id": "control@1", "kind": "control", "mistake_class": "none", "competency": item["competency"], "economic": False}
        st = state_at(s, s.scaffold_end)
        t = s.start
        cand = candidate(meta, run, s, raw, fork_state(st, t) | {"available": available_quests(run, s, s.scaffold_end)}, "A",
                         "control: the chain rule or the node cap decides it")
        cand["id"] = f"{item['id']}-ctl"
        gated, why_not = gate_candidate(cand, s)
        if gated:
            used.add((s.n, play))
            return gated
    return None


def control_pool(run, cands):
    """{item id: control} for this run's candidates that need one (only one constant play per item is pairable)."""
    used, out = set(), {}
    for c in sorted(cands, key=lambda c: (c["tier"] != "A", c["decision_point"]["time"] or "", c["id"])):
        if len(c["needs_control"]) == 1 and (ctl := control_for(run, c, c["needs_control"][0], used)):
            out[c["id"]] = ctl
    return out


def pair(kept, pools):
    """(items, controls, [(item, reason)]): items that need a control keep it and share its group id."""
    items, controls, dropped = [], [], []
    for c in kept:
        if not c["needs_control"]:
            items.append(c)
            continue
        ctl = pools.get(c["provenance"]["run"], {}).get(c["id"])
        if ctl is None:
            dropped.append((c, f"no_control:{'+'.join(c['needs_control'])}"))
            continue
        items.append(pair_with(c, c["id"]))
        controls.append(ctl)
    return items, controls, dropped


def pair_with(c, group):
    """The item with its group id set (candidate and spec), hashes restamped."""
    c = dict(c, group=group)
    c["spec"] = stamp(dict(c["spec"], variants={**c["spec"]["variants"], "group": _id(group)}))
    return c


# ------------------------------------------------------------------ trap controls for the economic families (F review M3)
# The opportunity's session start with every gas read rewritten to balances whose cap is 0 (owner 0.0006, operator
# 0.0004): converting there would strand gas.

def _rewrite_gas(text, owner, operator):
    """A get_gas_balance result with the trap balances (every gas read in the prefix is rewritten, F review M3)."""
    try:
        d = json.loads(text)
    except (TypeError, ValueError):
        return text
    main = (d.get("balances") or {}).get("main")
    if not isinstance(main, dict):
        return text
    main.update(owner_eth=f"{owner:.18f}", owner_wei=str(int(owner * 1e18)), operator_eth=f"{operator:.18f}",
                operator_wei=str(int(operator * 1e18)))
    return json.dumps(d, indent=2)


def trap_for(run, item):
    """The opportunity's session start with trap balances (cap 0), gated through the one scorer. None if not buildable."""
    from kami_drills.miner.pipeline import candidate, fork_state, gate_candidate
    s = next((x for x in run.sessions if x.n == item["decision_point"]["session"]), None)
    if s is None or not s.snapshot:
        return None
    snap = json.loads(json.dumps(s.snapshot))
    for c in snap["calls"]:
        if c["name"] == "get_gas_balance" and "response" in c:
            c["response"] = json.loads(_rewrite_gas(json.dumps(c["response"]), TRAP_OWNER, TRAP_OPERATOR))
    raw = {"family": item.get("family") or "economic_insight", "kind": "trap_control", "world_constants": TOKENS,
           "fork_line": s.scaffold_end, "fork_kind": "session_start", "group": item["id"],
           "objective": {"game": None, "source": "trap control", "shown_to_model": False},
           "agent_action": {"summary": "synthetic"}, "situation": "the paired item's session start with ~0.001 ETH liquid in all",
           "expected": "do not convert: the gas would be stranded", "actual": "synthetic", "why": "trap control",
           "scoring": {"options": [{"credit": 1.0, "steps": []}],
                       "zero_if": [{"any": [pat("portal_deposit", item=SHARD, amount={"gt": deposit_cap(TRAP_OWNER, TRAP_OPERATOR)})],
                                    "why": "converted the gas reserve"}]},
           "oracle": [], "accepted_actions": ["no deposit, or one within the state's own cap (0 here)"], "dependencies": [],
           "severity": {"value": 0, "unit": "control", "score": 0, "detail": {}}, "signature": ("trap", item["id"]),
           "variants": [], "tier_hint": "B", "tier_reason": "synthetic parameter variant", "id_suffix": "trap"}
    meta = {"id": item["rule_id"], "kind": "trap_control", "mistake_class": "none", "competency": item["competency"], "economic": True}
    st = state_at(s, s.scaffold_end)
    cand = candidate(meta, run, s, raw, fork_state(st, s.start) | {"available": available_quests(run, s, s.scaffold_end)}, "B", raw["tier_reason"])
    cand["id"] = item["id"] + "-trap"
    cand["variant"] = {"origin": "synthetic_param", "group": item["id"], "changes": {"owner_eth": TRAP_OWNER, "operator_eth": TRAP_OPERATOR}}

    class _S:  # the session with its snapshot's gas reads rewritten, and the transcript prefix to match
        snapshot, path = snap, s.path
    gated, _ = gate_candidate(cand, _S)
    if gated:
        for m in gated["spec"]["fork"].get("native_prefix", {}).get("messages", []):
            if m.get("role") == "tool_result" and "owner_eth" in str(m.get("content")):
                m["content"] = _rewrite_gas(m["content"], TRAP_OWNER, TRAP_OPERATOR)
        from kami_drills.spec.from_miner import stamp
        stamp(gated["spec"])
    return gated


# ------------------------------------------------------------------ trap controls for market-price
# The item's fork with the merchant read rewritten so the agent's food is the cheap one: every food that was the answer
# now costs twice the agent's food per HP, so buying it is the overpay and the agent's own purchase is right.

def _rewrite_prices(text, prices):
    try:
        d = json.loads(text)
    except (TypeError, ValueError):
        return text
    for l in ((d.get("data") or {}).get("listings") or []):
        if isinstance(l, dict) and str((l.get("item") or {}).get("index")) in prices:
            l["buyPrice"] = prices[str(l["item"]["index"])]
    return json.dumps(d, indent=2)


def market_trap_for(run, item):
    from kami_drills.miner.pipeline import _lines, candidate, fork_state, gate_candidate
    from kami_drills.miner.rules import FOOD_MARGIN, KAMI_FOOD_HP, batch_cost, food_alternatives, market_rows
    from kami_drills.miner.trace import time_at
    m = item.get("market") or {}
    s = next((x for x in run.sessions if x.n == item["decision_point"]["session"]), None)
    if s is None or not m:
        return None
    x, alts = m["item"], m["alts"]
    prices = {int(k): v for k, v in m["prices"].items()}
    caps = m.get("caps") or [math.inf]
    use = lambda f, cap: min(KAMI_FOOD_HP[f], cap)
    gda = {int(k): g for k, g in (m.get("gda") or {}).items()}
    qty = next(a for i, a in zip(item["agent_action"]["args"]["item_indices"], item["agent_action"]["args"]["amounts"]) if i == x)
    # the margin is measured at the right-sized heal: the x units that fill the kami missing most HP (rc7 cleanup)
    if len(caps) > 1:
        qty = min(qty, math.ceil(caps[0] / use(x, caps[0])))
    cost_x = batch_cost(prices[x], qty, gda.get(x))
    # what buying f instead of x costs for x's usable HP, under the dearest cap (the GDA batch from the read)
    sub = lambda f, p: max(batch_cost(p, math.ceil(qty * use(x, c) / use(f, c)), gda.get(f)) for c in caps)
    over = lambda f, p: all(batch_cost(p, math.ceil(qty * use(x, c) / use(f, c)), gda.get(f)) >= FOOD_MARGIN * cost_x for c in caps)
    trap = {**prices, **{y: 2 * math.ceil(prices[x] * max(use(y, c) / use(x, c) for c in caps)) for y in alts}}  # x right everywhere
    foods = {f: v for f, v in prices.items() if f in KAMI_FOOD_HP}
    small = None
    if min(foods, key=lambda f: (foods[f], f)) in alts:
        # the item's lowest sticker is its answer, so a sticker-price buyer passes the item: in the trap the food with
        # the least usable HP keeps the LOWEST sticker but costs >= 1.5x the MUSU per usable HP (review rc6 F2)
        small = min((f for f in foods if f != x), key=lambda f: (max(use(f, c) for c in caps), f), default=None)
        if small is None or max(use(small, c) for c in caps) >= min(use(x, c) for c in caps):
            return None  # only a smaller food can be the lowest sticker and an overpay
        lo = math.ceil(FOOD_MARGIN * cost_x / math.ceil(qty * use(x, caps[0]) / use(small, caps[0])))
        p = next((p for p in range(max(1, lo // 2), lo * 4) if over(small, p)), None)
        if p is None or p >= min(v for f, v in trap.items() if f != small and f in KAMI_FOOD_HP):
            return None  # no price is both the lowest sticker and an overpay per usable HP
        trap[small] = p
    if food_alternatives(x, qty, trap, gda, caps):
        return None  # the rewrite did not make the agent's food right
    # the overpay: every food that costs >= 1.5x the agent's for the same usable HP (the item's own margin)
    worse = sorted(y for y in trap if y != x and y in KAMI_FOOD_HP and over(y, trap[y]))
    lines = json.loads(json.dumps(_lines(str(s.path))))
    msg = lines[m["read_line"] - 1]
    msg["content"] = _rewrite_prices(msg["content"] if isinstance(msg["content"], str) else json.dumps(msg["content"]),
                                     {str(k): v for k, v in trap.items()})
    fork = item["decision_point"]["fork_line"]
    raw = {"family": "economic_insight", "kind": "trap_control", "fork_line": fork, "fork_kind": "before_call", "group": item["id"],
           "responses": market_rows(trap),
           "world_constants": {"market": {**item["world_constants"]["market"],
                                          "listings": {str(i): v for i, v in sorted(trap.items()) if i in KAMI_FOOD_HP}}},
           "objective": item["objective"], "agent_action": {"summary": "synthetic"},
           "situation": "the paired item's fork with the listing read rewritten: the agent's food is now the cheap one per HP, "
                        + (f"and food {small} still has the lowest sticker price" if small else "and no cheaper sticker"),
           "expected": f"not a food that costs >= {FOOD_MARGIN}x the MUSU for the same usable HP: {worse}", "actual": "synthetic", "why": "trap control",
           "scoring": {"options": [{"credit": 1.0, "steps": []}],
                       "zero_if": [{"any": [pat("listing_buy", item_index=y, venue=f"merchant:{item['world_constants']['market']['merchant']}")
                                            for y in worse], "why": "bought a food dearer per usable HP at the merchant that was read"}]},
           "oracle": [], "accepted_actions": [f"anything but buying {worse} (each >= {FOOD_MARGIN}x the MUSU per usable HP)"], "dependencies": [],
           "severity": {"value": 0, "unit": "control", "score": 0, "detail": {}}, "signature": ("trap", item["id"]),
           "variants": [], "tier_hint": "B", "tier_reason": "synthetic parameter variant", "id_suffix": "trap"}
    meta = {"id": item["rule_id"], "kind": "trap_control", "mistake_class": "none", "competency": item["competency"], "economic": True}
    st = state_at(s, fork)
    cand = candidate(meta, run, s, raw, fork_state(st, time_at(s, fork)) | {"available": available_quests(run, s, fork)}, "B", raw["tier_reason"])
    cand["id"] = item["id"] + "-trap"
    cand["variant"] = {"origin": "synthetic_param", "group": item["id"],
                       "changes": {f"/fork/native_prefix/lens_merchant/{y}/buyPrice": trap[y] for y in sorted(set(alts) | ({small} if small else set()))}}
    cand["market"] = dict(m, trap_prices={str(k): v for k, v in trap.items()}, forbidden=worse)
    gated, _ = gate_candidate(cand, s, lines=lines)
    return gated


# ------------------------------------------------------------------ controls for node-choice (review rc4 M4)
# "Travel to a node that matches the kami, then start" passes a node-choice item without comparing nodes. Each item
# pairs with a real harvest start from its run where moving is wrong: every node in reach pays <= 1/1.2 of the one the
# agent started at. The control is passed by starting there; check_bank holds every blind policy to half the pair.

CONTROL_MARGIN = 1.2


def node_control_for(run, item, used):
    from kami_drills.miner import rules as R
    from kami_drills.miner.pipeline import candidate, fork_state, gate_candidate
    from kami_drills.miner.trace import time_at
    n0 = item["decision_point"]["session"]
    for s in sorted(run.sessions, key=lambda x: (abs(x.n - n0), x.n)):
        if s.misaligned or R.infra_session(s):
            continue
        for c in s.calls:
            node, kamis, fork = c.args.get("node_index"), c.kami_ids(), c.line - 1
            if not (c.ok and c.write and c.tool == "harvest_start") or c.initiator == "scaffold" or (s.n, c.line) in used \
                    or (s.n == n0 and fork == item["decision_point"]["fork_line"]) or node not in R.NODES or not kamis \
                    or R.NODES[node]["yield_item"] != 1 or len(R.NODES[node]["affinity"]) > 1:
                continue
            stats = R.kami_stats(s, fork)
            st, t = state_at(s, fork), time_at(s, fork)
            room = st.account.get("room")
            stamina, _ = R.stamina_at(s, fork)
            if any(k not in stats for k in kamis) or not st.fresh(room) or room.value != node or stamina is None:
                continue
            here = sum(R.harvest_rate(stats[k], node) for k in kamis)
            alts = [sum(R.harvest_rate(stats[k], n) for k in kamis) for n, h in R.rooms_within(node, R.NODE_HOPS_MAX).items()
                    if h and n in R.NODES and R.NODES[n]["yield_item"] == 1 and R.HOP_STAMINA * h <= stamina]
            if not alts or max(alts) * CONTROL_MARGIN > here:
                continue
            cds = {k: o.value for k in kamis if (o := st.kami_obs(k, "cooldown_until")) and st.fresh(o) and o.value}
            late = max(cds.values(), default=0)
            wait = R.full_credit_wait(s, fork, cds, t) if late > t.timestamp() else max(0, math.ceil(late + R.FULL_FROM_S - t.timestamp()))
            if wait > R.WAIT_MAX_S:
                continue
            raw = {"fork_line": fork, "fork_kind": "before_call", "kind": "control", "mistake_class": "none",
                   "objective": {"game": None, "source": "control", "shown_to_model": False},
                   "agent_action": {"tool": c.tool, "args": c.args, "line": c.line},
                   "situation": f"Kami(s) {kamis} are about to start at node {node}; every node in reach pays <= 1/{CONTROL_MARGIN} of it.",
                   "expected": f"start here (node {node})", "actual": "the agent did",
                   "why": f"control for {item['id']}: moving to a matching-looking node is wrong here",
                   "scoring": {"options": [{"credit": 1.0, "ordered": False, "steps": [
                       {"any": [pat("harvest_start", ok=True, kami=k, node_index=node)]} for k in kamis]}]},
                   "oracle": ([["wait", {"seconds": wait}]] if wait else []) + [["harvest_start", {"kami_ids": kamis, "node_index": node}]],
                   "accepted_actions": [f"harvest_start at node {node}"],
                   "dependencies": [room.dep("account.roomIndex")] + [
                       {"field": f"kami.{k}.affinities+stats", "value": [stats[k]["body"], stats[k]["hand"], stats[k]["power"],
                        stats[k]["violence"]], "source": stats[k]["src"], "as_of": None} for k in kamis],
                   "world_constants": {"node_choice": {"max_hops": R.NODE_HOPS_MAX, "margin": R.NODE_MARGIN,
                                                       "traits": {str(k): [stats[k]["body"], stats[k]["hand"]] for k in kamis}}},
                   "fork_state_overrides": {"stamina": stamina},
                   "severity": {"value": 0, "unit": "control", "score": 0, "detail": {}}, "signature": ("control", "node"),
                   "group": item["id"], "id_suffix": "ctl"}
            meta = {"id": "control@1", "kind": "control", "mistake_class": "none", "competency": item["competency"], "economic": False}
            cand = candidate(meta, run, s, raw, fork_state(st, t) | {"stamina": stamina}, "A", "control: the gdd yield formula decides it")
            cand["id"] = f"{item['id']}-ctl"
            gated, _ = gate_candidate(cand, s)
            if gated:
                used.add((s.n, c.line))
                return gated
    return None


def traps_for(run, cands):
    """{opportunity id: trap control} for this run's economic opportunities (not the genuine traps)."""
    out, used = {}, set()
    for c in sorted(cands, key=lambda c: c["id"]):
        if c["rule_id"].startswith("node-choice@") and (ctl := node_control_for(run, c, used)):
            out[c["id"]] = ctl
        if c["economic"] and c["kind"] != "trap_control":
            make = market_trap_for if c["rule_id"].startswith("market-price@") else trap_for
            if t := make(run, c):
                out[c["id"]] = t
    return out
