"""kami-rules: the shared game-rules library a spec opts into with `world.rules_library: "kami-rules@1.0.0"`.

It answers an act intent that no response row matched, from the simulated state, the way the chain (gdd) or the
harness's pre-transaction checks would: a refusal with a reason code, or success with its effects applied. Per-drill
`responses` always win over it (D-spec 1.1.2). A verb it has no rule for, or a state it cannot judge, is None: the
simulator then treats the act as a gap, as before.

Static world (node level caps, room graph, food and revive items) is kami_world.json, built from the gdd by
scripts/build_kami_world.py. Sources per rule are cited inline (gdd mechanics/...).
"""
import json
from collections import deque
from pathlib import Path

NAME = "kami-rules@1.0.0"
WORLD = json.loads((Path(__file__).parent / "kami_world.json").read_text())
ITEMS = {int(k): v for k, v in WORLD["items"].items()}
KAMI_FOODS = frozenset(i for i, v in ITEMS.items() if v["type"] == "FOOD" and v["for"] == "KAMI")
REVIVE_ITEMS = {"red_ribbon_gummy": 11001, "melkarth_spell_card": 11002, "djed_pillar": 11003, "pale_potion": 11004}
ONYX_ITEM, ONYX_COST, ONYX_HP = 100, 33, 33  # kami-harness revive_kami: 33 Onyx Shards (item 100) restore 33 HP
NODE_CAP = {int(k): v["level_cap"] for k, v in WORLD["nodes"].items()}
ADJ = {int(k): set(v) for k, v in WORLD["rooms"].items()}
KAMI_COOLDOWN_S = 180  # every harvest start/stop/collect receipt in Season 1: cooldown_until = receipt + 170..180 s
# Chain clock vs the agent's clock, fitted on S1 (scripts/fit_cooldown_margin.py): the earliest accepted gated act was
# 4.4 s past cooldown_until; refusals fall under 0.5% from 26 s past it. Acts in between are accepted with band credit.
ACCEPT_FROM_S, FULL_FROM_S = 4, 26
GATED = ("harvest_start", "harvest_stop", "harvest_collect", "feed", "liquidate")  # gdd utility/cooldowns.md
PER_KAMI = GATED + ("revive", "level_up", "upgrade_skill")


def _refuse(reason, detail, cls="revert"):
    return {"class": cls, "reason": reason, "detail": detail}


def path_len(a, b):
    """Hops on the room graph from a to b, or None if unreachable."""
    if a == b:
        return 0
    seen, q = {a}, deque([(a, 0)])
    while q:
        r, n = q.popleft()
        for x in ADJ.get(r, ()):
            if x == b:
                return n + 1
            if x not in seen:
                seen.add(x)
                q.append((x, n + 1))
    return None


class Rules:
    """Per-simulation rule state: constants from the spec and the post-fork cooldown evidence (dropped for a kami once
    the play sets a new cooldown on it)."""

    def __init__(self, sim):
        c = sim.spec.get("world", {}).get("constants", {})
        self.sim = sim
        self.accept_from = c.get("cooldown_accept_from_s", ACCEPT_FROM_S)
        self.full_from = c.get("cooldown_full_from_s", FULL_FROM_S)
        self.evidence = {int(k): v for k, v in (c.get("cooldown_evidence") or {}).items()}
        self.late_after = c.get("late_after_unix")
        self.inventory_known = c.get("inventory_known", True)
        self.tokens = c.get("portal_tokens") or {}  # item -> {"balance": pointer, "unit_eth": x}; drill data, not code
        self.native = sim.spec.get("world", {}).get("native_text") or {}

    # ---- helpers
    def held(self, item):
        return self.sim.state["inventory"].get(str(item), 0)

    def cooldown(self, k, t, flags):
        """None if the kami may act at t, else a refusal. Sets early_retry (acting on a cooldown the state shows),
        cooldown_band (accepted, but inside the band where S1 saw refusals) and late."""
        until = k.get("cooldown_until_unix")
        if until is None:
            return None
        if t < until:
            flags.add("early_retry")
            return _refuse("COOLDOWN", f"kami on cooldown for {until - t:.0f} s")
        ev = self.evidence.get(k["index"]) or {}
        lo = max(until + self.accept_from, (ev.get("refused_at") or -1) + 1)
        if t < lo:  # the chain still refused here in S1: not flagged, a retry is fair
            return _refuse("COOLDOWN", "kami on cooldown")
        hi = min(until + self.full_from, ev.get("accepted_at") or float("inf"))
        if t < hi:
            flags.add("cooldown_band")
        if self.late_after is not None and t > self.late_after:
            flags.add("late")  # waited out the cooldown, then idled: a lesser play where the drill declares one
        return None

    def objective_met(self, o):
        """ROOM ("Move to …") is met while the account is there; ITEM_BURN counts gives since acceptance; any other
        objective keeps the met flag the fork state recorded (the library cannot advance it)."""
        if o["type"] == "ROOM" and "index" in o:
            return self.sim.state["account"]["room"] == o["index"]
        if o["type"] == "ITEM_BURN" and "required" in o:
            return o.get("current", 0) >= o["required"]
        return bool(o.get("met"))

    # ---- the rules
    def act(self, intent):
        """(result, apply) or None. `apply()` commits the effects; a dry run never calls it."""
        sim, s = self.sim, self.sim.state
        v, a = intent["verb"], intent.get("args") or {}
        t, flags = sim.chain_clock(), set()
        k = sim.kami(a.get("kami")) if v in PER_KAMI else None
        if v in PER_KAMI and k is None:
            return (_refuse("BAD_ARGS", f"kami {a.get('kami')} is not on this account", "validation_error"), None, flags)
        room = s["account"]["room"]
        res, apply = None, None

        def set_cd():
            k["cooldown_until_unix"] = t + KAMI_COOLDOWN_S
            self.evidence.pop(k["index"], None)

        if v == "harvest_start":  # gdd economy/harvesting.md Start; world/nodes.md level caps
            node = a.get("node")
            if node is None:
                res = _refuse("BAD_ARGS", "node_index required", "validation_error")
            elif k["state"] != "RESTING":
                res = _refuse("KAMI_DEAD" if k["state"] == "DEAD" else "KAMI_NOT_RESTING", f"kami is {k['state']}")
            elif (cd := self.cooldown(k, t, flags)):
                res = cd
            elif node != room:
                res = _refuse("WRONG_ROOM", f"must be in same room (account in {room}, node {node})")
            elif k.get("level") is not None and NODE_CAP.get(node) is not None and k["level"] > NODE_CAP[node]:
                res = _refuse("LEVEL_REQS", f"node reqs not met (level {k['level']} > cap {NODE_CAP[node]})")
            else:
                def apply():
                    k.update(state="HARVESTING", harvest={"node": node, "room": node, "accrued_musu": 0})
                    set_cd()
        elif v in ("harvest_stop", "harvest_collect"):  # gdd economy/harvesting.md Collect / Stop
            h = k.get("harvest") or {}
            if k["state"] != "HARVESTING":
                res = _refuse("KAMI_DEAD" if k["state"] == "DEAD" else "KAMI_NOT_HARVESTING", f"kami is {k['state']}")
            elif k["hp"] == 0:
                res = _refuse("KAMI_STARVING", "kami starving")
            elif (cd := self.cooldown(k, t, flags)):
                res = cd
            elif h.get("room", h.get("node")) is not None and h.get("room", h.get("node")) != room:
                res = _refuse("KAMI_TOO_FAR", "kami too far")
            else:
                def apply():
                    if v == "harvest_stop":  # stop collects all accrued bounty and ends the harvest
                        k.update(state="RESTING")
                        k.pop("harvest", None)
                    elif "accrued_musu" in h:
                        h["accrued_musu"] = 0
                    set_cd()
        elif v == "feed":  # gdd core-kami/health-healing.md; item use is cooldown-gated (utility/cooldowns.md)
            item, h = a.get("item"), k.get("harvest") or {}
            if item not in KAMI_FOODS:
                res = _refuse("BAD_ARGS", f"item {item} is not kami food", "validation_error")
            elif self.inventory_known and self.held(item) < 1:
                res = _refuse("INSUFFICIENT_ITEM", f"holds 0 of item {item}", "validation_error")
            elif k["state"] == "DEAD":
                res = _refuse("KAMI_DEAD", "kami is dead")
            elif (cd := self.cooldown(k, t, flags)):
                res = cd
            elif k["state"] == "HARVESTING" and h.get("room", h.get("node")) not in (None, room):
                res = _refuse("KAMI_TOO_FAR", "kami too far")
            else:
                def apply():
                    k["hp"] = min(k["hp_max"], k["hp"] + ITEMS[item]["hp"])
                    if self.inventory_known:
                        s["inventory"][str(item)] = self.held(item) - 1
        elif v == "revive":  # kami-harness revive_kami methods; gdd core-kami/death-revival.md
            m = a.get("method", "onyx")
            item, n, hp = (ONYX_ITEM, ONYX_COST, ONYX_HP) if m == "onyx" else (REVIVE_ITEMS.get(m), 1, ITEMS.get(REVIVE_ITEMS.get(m), {}).get("hp"))
            if item is None:
                res = _refuse("BAD_ARGS", f"unknown revive method {m!r}", "validation_error")
            elif k["state"] != "DEAD":
                res = _refuse("KAMI_NOT_DEAD", "kami not dead")
            elif self.inventory_known and self.held(item) < n:
                res = _refuse("INSUFFICIENT_ITEM", f"holds {self.held(item)} of item {item}; revive_kami requires {n}", "validation_error")
            elif m != "onyx" and (cd := self.cooldown(k, t, flags)):
                res = cd
            else:
                def apply():
                    k.update(state="RESTING", hp=min(k["hp_max"], hp))
                    if self.inventory_known:
                        s["inventory"][str(item)] = self.held(item) - n
        elif v == "level_up":  # gdd core-kami/experience-leveling.md: RESTING is checked before XP
            if k["state"] != "RESTING":
                res = _refuse("KAMI_NOT_RESTING", f"kami not RESTING ({k['state']})")
            elif k.get("level_up_ready") is False:
                res = _refuse("LEVEL_REQS", "not enough experience")
            else:
                def apply():
                    if "level" in k:
                        k["level"] += 1
                    if "skill_points" in k:
                        k["skill_points"] += 1
                    k["level_up_ready"] = False
        elif v == "upgrade_skill":
            if k.get("skill_points") == 0:
                res = _refuse("NO_SKILL_POINTS", "SkillUpgrade: not enough points")
            else:
                def apply():
                    if "skill_points" in k:
                        k["skill_points"] -= 1
        elif v == "liquidate":
            if (cd := self.cooldown(k, t, flags)):
                res = cd
            else:
                return None  # the outcome depends on the victim: another player's state
        elif v in ("complete_quest", "accept_quest"):  # gdd progression/quests.md
            q = next((q for q in s.get("quests", []) if q["index"] == a.get("quest")), None)
            if v == "complete_quest":
                if q is None or q["status"] in ("available", "locked"):
                    res = _refuse("QUEST_NOT_ACCEPTED", f"quest {a.get('quest')} is not accepted by account", "validation_error")
                elif q["status"] == "complete":
                    res = _refuse("QUEST_ALREADY_COMPLETE", f"quest {q['index']} is already completed by account", "validation_error")
                elif q.get("requirements_met") is False:
                    res = _refuse("QUEST_REQS_NOT_MET", "reqs not met")
                elif q["status"] != "objectives_met" and not (q.get("objectives") is not None and all(self.objective_met(o) for o in q["objectives"])):
                    res = _refuse("QUEST_OBJS_NOT_MET", "quest objs not met")
                else:
                    def apply():  # gdd progression/quests.md: rewards are paid on completion (foods and revives modelled)
                        q["status"] = "complete"
                        if q.get("repeatable"):
                            q["repeat_available_unix"] = t + (WORLD["quests"].get(str(q["index"])) or {}).get("repeat_s", 0)
                        for item, n in (WORLD["quests"].get(str(q["index"])) or {}).get("rewards", {}).items():
                            s["inventory"][item] = self.held(item) + n
            else:
                if q is None:
                    return None  # a quest the fork state says nothing about
                if q["status"] in ("active", "objectives_met"):
                    res = _refuse("QUEST_ALREADY_COMPLETE", f"quest {q['index']} is already accepted by account", "validation_error")
                elif q["status"] == "complete" and not q.get("repeatable"):
                    res = _refuse("QUEST_ALREADY_COMPLETE", f"quest {q['index']} is already completed by account", "validation_error")
                elif q["status"] == "complete" and t < (q.get("repeat_available_unix") or 0):  # checkRepeat
                    res = _refuse("QUEST_REPEAT_NOT_READY", f"quest {q['index']} can repeat in {q['repeat_available_unix'] - t:.0f} s")
                elif q["status"] == "locked" or any(r["type"] == "ROOM" and r["index"] != room for r in q.get("requirements", [])):
                    res = _refuse("QUEST_REQS_NOT_MET", "reqs not met")
                else:
                    def apply():  # objectives count from acceptance; a quest with none is met at once
                        for o in q.get("objectives") or []:
                            o["met"] = False
                            if "current" in o:
                                o["current"] = 0
                        q["status"] = "objectives_met" if q.get("objectives") == [] else "active"
        elif v == "burn":  # kami-harness burn_items; counts toward an accepted quest's ITEM_BURN ("Give N") objective
            item, n = a.get("item"), int(a.get("amount") or 0)
            if n <= 0:
                res = _refuse("BAD_ARGS", "amounts must be positive", "validation_error")
            elif self.inventory_known and self.held(item) < n:
                res = _refuse("INSUFFICIENT_ITEM", f"holds {self.held(item)} of item {item}", "validation_error")
            else:
                def apply():
                    if self.inventory_known:
                        s["inventory"][str(item)] = self.held(item) - n
                    for q in s.get("quests", []):
                        for o in q.get("objectives") or [] if q["status"] == "active" else []:
                            if o["type"] == "ITEM_BURN" and o.get("index") == item:
                                o["current"] = o.get("current", 0) + n
        elif v == "travel":  # kami-harness move_to_room (one hop, adjacent only) / travel_to_room (BFS path)
            to = a.get("room")
            hops = path_len(room, to) if to in ADJ and room in ADJ else None
            st = s["account"].get("stamina")
            if to == room:
                res = _refuse("WRONG_ROOM", f"account is already in room {to}", "validation_error")
            elif hops is None or (a.get("hop") and hops != 1):
                res = _refuse("ROOM_UNREACHABLE", f"room {to} is not connected to the account's current room {room}", "validation_error")
            elif st is not None and st < 5:
                res = _refuse("NOT_ENOUGH_STAMINA", f"account stamina is {st}; a room move requires 5", "validation_error")
            else:
                def apply():
                    s["account"]["room"] = to
                    if st is not None:  # shortcut: travel_to_room's partial result on low stamina is not modelled
                        s["account"]["stamina"] = max(0, st - 5 * hops)
        elif v == "portal_deposit" and str(a.get("item")) in self.tokens:
            tok = self.tokens[str(a["item"])]
            from kami_drills.spec import get, pointer
            bal = float(get(s, tok["balance"]) or 0)
            cost = float(a.get("amount") or 0) * tok["unit_eth"]
            # the portal keeps an import tax in items: flat + basis points (S1 receipts: flat 1, 50 bps)
            tax = tok.get("tax_flat", 0) + int(float(a.get("amount") or 0) * tok.get("tax_bps", 0) / 1e4)
            if cost <= 0:
                res = _refuse("BAD_ARGS", "amount must be positive", "validation_error")
            elif float(a.get("amount") or 0) <= tax:
                res = _refuse("BAD_ARGS", f"import tax ({tax}) must be below the amount", "validation_error")
            elif cost > bal + 1e-12:
                res = _refuse("TOKEN_BALANCE", "token balance too low", "validation_error")
            else:
                def apply():
                    parent, key = pointer(s, tok["balance"])
                    parent[key] = f"{bal - cost:.18f}".rstrip("0").rstrip(".")
                    s["inventory"][str(a["item"])] = self.held(a["item"]) + int(a["amount"]) - tax
        elif v == "swap":
            return self.swap(a, flags)
        else:
            return None
        if res and res.get("reason") in self.native:
            res["native"] = self.native[res["reason"]]
        return res or {"class": "ok"}, apply, flags

    def swap(self, a, flags):
        """Constant-product pool from world.pools (fee from the pool); None when the drill declares no such pool."""
        s = self.sim.state
        for p in self.sim.spec.get("world", {}).get("pools", []):
            for x, y, rx, ry in ((p["item_a"], p["item_b"], p["reserve_a"], p["reserve_b"]), (p["item_b"], p["item_a"], p["reserve_b"], p["reserve_a"])):
                if (x, y) != (a.get("item_in"), a.get("item_out")):
                    continue
                n = int(a.get("amount_in") or 0)
                if p.get("disabled"):
                    return _refuse("POOL_DISABLED", "pool disabled"), None, flags
                if n <= 0 or (self.inventory_known and self.held(x) < n):
                    return _refuse("INSUFFICIENT_ITEM", f"holds {self.held(x)} of item {x}", "validation_error"), None, flags
                dx = n * (1 - p.get("fee_bps", 30) / 1e4)
                out = int(ry * dx / (rx + dx))
                if out < (a.get("min_out") or 0):
                    return _refuse("SLIPPAGE", f"returns {out} after fees, under min_out"), None, flags

                def apply():
                    s["inventory"][str(x)] = self.held(x) - n
                    s["inventory"][str(y)] = self.held(y) + out
                return {"class": "ok", "fields": {"amount_out": out}}, apply, flags
        return None
