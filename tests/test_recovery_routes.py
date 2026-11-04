"""Exhaustive route search over every recovery item and trap in the latest bank (rc10 final-pass review): a bounded
breadth-first search over the rules library's acts finds every way to revive a dead kami from the fork state, and the
key must credit each one that keeps the gas reserve on the item, and fail it on the trap. No private data needed; the
held-out variants are searched too when `private/` sits next to the bank."""
import copy
import json
from pathlib import Path

import pytest

from kami_drills import spec as sp
from kami_drills.spec.check import REVIVE_METHODS
from kami_drills.spec.rules import REVIVE_ITEMS as _RM, WORLD

REVIVE_ITEMS = set(_RM.values())

ROOT = Path(__file__).parent.parent
BANK = max((ROOT / "bank").glob("v2.0-rc*"), key=lambda p: int(p.name.rsplit("rc", 1)[1]))
DEPTH, FRONTIER = 10, 4000
SQ997 = 10003
# The search is complete under the rules library: a revive needs its item; items come from a merchant gummy (MUSU), a
# pool (MUSU) or a quest reward. So it accepts and completes only quests that pay a revive item, and buys only revive
# items and those quests' gives; other acts change nothing a revive depends on.
REVIVE_QUESTS = {int(q) for q, v in WORLD["quests"].items() if any(int(i) in REVIVE_ITEMS for i in v.get("rewards", {}))}
BUYABLE = {100} | {o["index"] for q in REVIVE_QUESTS for o in WORLD["quests"][str(q)].get("objectives", []) if o["type"] == "ITEM_BURN"}


def recovery_specs():
    pub = sp.load([BANK])
    priv = BANK / "private" / "heldout.jsonl"
    held = [json.loads(x) for x in priv.read_text().splitlines()] if priv.exists() else []
    return [s for s in pub + held if s.get("family") == "poverty_recovery"]


def deposit_cap(spec):
    """The item's cap, from its rule's over-cap forbid (the trap's is 0)."""
    for f in spec["scoring"]["rule"].get("forbid", []):
        if f.get("verb") == "portal_deposit" and isinstance((f.get("args") or {}).get("amount"), dict):
            return f["args"]["amount"]["gt"]
    return 0


def acts(spec, sim, cap, deposited):
    """Every act worth trying from this state; the search keeps only those the simulator accepts."""
    s, I = sim.state, lambda v, **a: {"verb": v, "args": a}
    inv = s["inventory"]
    held = lambda i: inv.get(str(i), 0)
    # deposits: the least that funds a gummy (after the portal tax and the pool), and the whole cap
    pool = next((p for p in spec["world"].get("pools", []) if p["item_a"] == 103), None)
    tok = (spec["world"]["constants"].get("portal_tokens") or {}).get("103") or {}
    gain = lambda n: 0 if not pool else int(pool["reserve_b"] * (m := (n - tok.get("tax_flat", 0) - n * tok.get("tax_bps", 0) // 10 ** 4)
                                                                     * (1 - pool.get("fee_bps", 30) / 1e4)) / (pool["reserve_a"] + m))
    least = next((n for n in range(2, cap - deposited + 1) if held(1) + gain(n) >= 499), None)
    out = [I("portal_deposit", item=103, amount=n) for n in sorted({least or 0, cap}) if 0 < n <= cap and not deposited]  # one deposit
    for p in spec["world"].get("pools", []):
        x = p["item_a"]
        if held(x):  # sell all of it
            out.append(I("swap", item_in=x, item_out=1, amount_in=held(x), min_out=1))
        if x not in BUYABLE:
            continue  # buying an item that neither revives nor is a revive quest's give cannot lead to a revive
        for units in (3, 33):  # buy what a give or an onyx revive needs with MUSU
            ra, rb, fee = p["reserve_a"], p["reserve_b"], p.get("fee_bps", 30)
            a = max(1, int(units * rb / max(ra - units, 1) / (1 - fee / 1e4)))
            while int(ra * a * (1 - fee / 1e4) / (rb + a * (1 - fee / 1e4))) < units:
                a += 1
            if a <= held(1):
                out.append(I("swap", item_in=1, item_out=x, amount_in=a, min_out=units))
    out.append(I("buy", item=11001, qty=1, venue="merchant:1"))
    for q in [x["index"] for x in s.get("quests", []) if x["index"] in REVIVE_QUESTS] + [SQ997]:  # others pay no revive
        out += [I("accept_quest", quest=q), I("complete_quest", quest=q)]
    for q in s.get("quests", []):
        for o in q.get("objectives") or []:
            if q["status"] == "active" and o["type"] == "ITEM_BURN":
                out.append(I("burn", item=o["index"], amount=o.get("required", 1)))
            if q["status"] == "active" and o["type"] == "ROOM" and o["index"] != s["account"]["room"]:
                out.append(I("travel", room=o["index"]))
    need = {"onyx": ("100", 33), "red_ribbon_gummy": ("11001", 1), "melkarth_spell_card": ("11002", 1), "djed_pillar": ("11003", 1),
            "pale_potion": ("11004", 1)}
    out += [I("revive", kami=k["index"], method=m) for k in s["kamis"] if k["state"] == "DEAD" for m in REVIVE_METHODS
            if inv.get(need[m][0], 0) >= need[m][1]]  # a revive without its item is refused anyway
    return out


def clone(sim):
    """A copy of a simulation that shares the (large, read-only) spec."""
    c = copy.copy(sim)
    c.state, c.flags, c.trace, c.gaps, c.notes = copy.deepcopy(sim.state), set(sim.flags), list(sim.trace), list(sim.gaps), dict(sim.notes)
    if sim.rules:
        c.rules = copy.copy(sim.rules)
        c.rules.sim, c.rules.evidence = c, dict(sim.rules.evidence)
    return c


def search(spec):
    """Every minimal route: a path of accepted acts (BFS to DEPTH, FRONTIER states per level, each state once) that
    ends in a successful revive, with deposits within the cap. A state from which a revive succeeds is not expanded
    further (a longer route through it only adds spending before the same revive)."""
    cap = deposit_cap(spec)
    key = lambda sim: json.dumps([sim.state, sorted(sim.flags)], sort_keys=True)
    root = sp.Simulator(spec)
    level, seen, found = [(root, [], 0)], {key(root)}, []
    for _ in range(DEPTH):
        nxt = []
        for sim, path, dep in level:
            todo = acts(spec, sim, cap, dep)
            revives = [a for a in todo if a["verb"] == "revive"]
            for a in revives:
                child = clone(sim)
                child.step(copy.deepcopy(a))
                if child.trace[-1]["ok"]:
                    found.append(path + [a])
            if any(p[:-1] == path for p in found[-len(revives):] if revives):
                continue
            for a in todo:
                if a["verb"] == "revive" or a in path:  # an act repeated on a path only spends more
                    continue
                child = clone(sim)
                child.step(copy.deepcopy(a))
                if not child.trace[-1]["ok"]:
                    continue
                p = path + [a]
                k = key(child)
                if k not in seen:
                    seen.add(k)
                    nxt.append((child, p, dep + (a["args"]["amount"] if a["verb"] == "portal_deposit" else 0)))
        level = nxt[:FRONTIER]
        if not level:
            break
    return found


SPECS = recovery_specs()
ITEMS = [s for s in SPECS if s["variants"]["origin"] != "trap_control"]
TRAPS = {s["variants"].get("group"): s for s in SPECS if s["variants"]["origin"] == "trap_control"}


def test_the_bank_has_recovery_items_and_traps():
    assert ITEMS and len(TRAPS) == len(ITEMS)


@pytest.mark.parametrize("item", ITEMS, ids=lambda s: s["id"])
def test_every_route_to_a_revive_passes_the_item_and_fails_its_trap(item):
    routes = search(item)
    assert routes, "the item's own state must have a route (its oracle is one)"
    refused = [r for r in routes if not sp.play(item, r + [{"verb": "end_session", "args": {}}])["pass"]]
    assert refused == [], f"{len(refused)} of {len(routes)} routes refused, e.g. {refused[0]}"
    trap = TRAPS[item["variants"]["group"]]
    assert not [r for r in routes if sp.play(trap, r)["pass"]]
    assert search(trap) == [], "the trap must leave no way to a revive"
