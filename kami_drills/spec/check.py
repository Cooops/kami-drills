"""Offline gate for v2 specs: schema, the three reference plays, the degenerate battery, and the harness-invariance
check per adapter (docs/v2-spec.md). No model, no network, no cost."""
from kami_drills import spec as sp

REVIVE_METHODS = ("onyx", "red_ribbon_gummy", "melkarth_spell_card", "djed_pillar", "pale_potion")


def _plays(spec):
    return {p["name"]: p for p in spec["scoring"].get("reference_plays", [])}


def mistake(spec):
    """The recorded mistake, else a success fork's authored one: the battery's mistake plays are built from it."""
    rp = spec["scoring"].get("reference_plays", [])
    return next((p["intents"] for k in ("is_recorded_mistake", "is_authored_mistake") for p in rp if p.get(k)), [])


def timing_tail(spec):
    """A timing item's allowed pass ends in this: wait 300, then the mistake (which, after the wait, is the oracle)."""
    return [{"verb": "wait", "args": {"seconds": 300}}, *mistake(spec)]


def timing_ok(spec, intents):
    """A timing item's battery pass is earned by its trailing wait + act alone: the play without that tail fails."""
    tail = timing_tail(spec)
    return (bool(spec["scoring"].get("timing_item")) and len(intents) >= len(tail) and intents[-len(tail):] == tail
            and not sp.play(spec, intents[:-len(tail)])["pass"])


def battery(spec):
    """Lazy and degenerate plays, as intents, built from what the fork shows (state-blind: no reasoning about it).
    Every one must fail, except time/read-only plays on a drill that declares waiting correct."""
    s = spec["state"]
    kamis = [k["index"] for k in s["kamis"]]
    held = [int(i) for i, n in s["inventory"].items() if n and i != "1"]
    quests = [q["index"] for q in s.get("quests", [])]
    rooms = sorted({s["account"]["room"], *((k.get("harvest") or {}).get("room") for k in s["kamis"]),
                    *map(int, spec.get("world", {}).get("rooms", {}))} - {None})
    oracle = _plays(spec).get("oracle", {}).get("intents", [])
    I = lambda verb, **a: {"verb": verb, "args": a}
    end = [I("end_session")]
    feed_all = [I("feed", kami=k, item=i) for k in kamis for i in held]
    revive_all = [I("revive", kami=k, method=m) for k in kamis for m in REVIVE_METHODS]
    level_all = [I("level_up", kami=k) for k in kamis]
    # one unit of every portal token the drill declares, deposited and sold for MUSU (item 1): a blind token move
    tokens = [x for tok in sorted(spec.get("world", {}).get("constants", {}).get("portal_tokens") or {}, key=int)
              for x in (I("portal_deposit", item=int(tok), amount=1), I("swap", item_in=int(tok), item_out=1, amount_in=1, min_out=1))]
    sink = ([x for q in quests for x in (I("accept_quest", quest=q), I("complete_quest", quest=q))]
            + [x for k in kamis for x in (I("level_up", kami=k), I("upgrade_skill", kami=k), I("harvest_stop", kami=k),
                                           I("harvest_collect", kami=k))]
            + feed_all + revive_all + tokens + [I("wait", seconds=300)] + mistake(spec))
    plays = {
        "no_calls": [], "end_session": end,
        "wake_5": [I("schedule_wake", minutes=5)], "wake_60": [I("schedule_wake", minutes=60)], "wake_1440": [I("schedule_wake", minutes=1440)],
        "wait_300_end": [I("wait", seconds=300), *end], "waits_3x_end": [I("wait", seconds=300)] * 3 + end,
        "read_then_end": [I("read", kind="account"), I("read", kind="party"), I("read", kind="inventory"), *end],
        "dry_run_only": [{"verb": "quote", "args": {"of": i["verb"], **i.get("args", {})}, "dry_run": True}
                         for i in oracle if i["verb"] not in sp.PASSIVE] + end,
        "kitchen_sink": sink, "always_feed": feed_all + end, "always_revive": revive_all + end,
        "always_level": level_all + end, "always_stop": [I("harvest_stop", kami=k) for k in kamis] + end,
        "collect_all": [I("harvest_collect", kami=k) for k in kamis] + end,
        "always_move": [I("travel", room=r) for r in rooms] + end,
        "start_then_level": [I("harvest_start", kami=k, node=s["account"]["room"]) for k in kamis] + level_all + end,
        "complete_all_quests": [I("complete_quest", quest=q) for q in quests] + end,
        "wait_then_mistake": [I("wait", seconds=300), *mistake(spec)],
        "mistake_then_wake": [*mistake(spec), I("schedule_wake", minutes=60)],
        "token_move": tokens + end,
    }
    for r in rooms:
        plays[f"move_{r}_then_mistake"] = [I("travel", room=r), *mistake(spec)]
    if any(k["state"] == "DEAD" for k in s["kamis"]):  # recovery drills: constant quest-claim policies (rc10 review M1)
        dead = [k["index"] for k in s["kamis"] if k["state"] == "DEAD"]
        avail = [q["index"] for q in s.get("quests", []) if q["status"] == "available"]
        claim = lambda qs: [x for q in qs for x in (I("accept_quest", quest=q), I("complete_quest", quest=q))]
        card = [I("revive", kami=k, method="melkarth_spell_card") for k in dead]
        plays["claim_all"] = claim(avail) + end  # accept and complete every quest on offer
        plays["claim_avail_revive_card"] = claim(avail) + card + end
        plays["sq997"] = claim([10003]) + card + end
    consts = spec.get("world", {}).get("constants", {})
    if nc := consts.get("node_choice"):  # node-choice drills: blind node pickers within the drill's reach
        from kami_drills.spec.rules import NODE_CAP, path_len
        here = s["account"]["room"]
        near = [r for r in sorted(R_ADJ()) if r != here and (path_len(here, r) or 99) <= nc.get("max_hops", 3)]
        resting = [k["index"] for k in s["kamis"] if k["state"] == "RESTING"]
        go = lambda r: [I("travel", room=r)] + [I("harvest_start", kami=k, node=r) for k in resting] + end
        plays["start_here"] = [I("harvest_start", kami=k, node=here) for k in resting] + end
        if near:
            plays["highest_node"] = go(max(near))
            plays["highest_cap_node"] = go(max(near, key=lambda r: (NODE_CAP.get(r) is None, NODE_CAP.get(r) or 0, r)))
            plays["nearest_node"] = go(min(near, key=lambda r: (path_len(here, r), r)))
        from kami_drills.spec.rules import WORLD
        nodes, gated = WORLD["nodes"], set(WORLD.get("gated_rooms", ()))
        for slot, name in ((0, "body_match_node"), (1, "hand_match_node")):  # review rc4 M4: affinity-matching, no comparison
            aff = {t[slot] for t in (nc.get("traits") or {}).values()} - {None, "NORMAL"}
            match = [r for r in near if r not in gated and (nodes.get(str(r)) or {}).get("yield_item") == 1
                     and aff & set((nodes.get(str(r)) or {}).get("affinity") or [])]
            if match:
                plays[name] = go(min(match, key=lambda r: (path_len(here, r), r)))
    if mk := consts.get("market"):  # market drills: buy everything listed, buy the cheapest listing, sell everything held
        listed = sorted(mk.get("listings", {}), key=int)
        buy = lambda i: I("buy", item=int(i), qty=1, venue=f"merchant:{mk.get('merchant')}")
        plays["always_buy"] = [buy(i) for i in listed] + end
        if listed:
            cheap = min(listed, key=lambda i: (mk["listings"][i], int(i)))
            plays["buy_cheapest"] = [buy(cheap)] + end
            plays["bulk_cheapest"] = [dict(buy(cheap), args={**buy(cheap)["args"], "qty": 60})] + end  # review rc4 M4
            for edge in ("lo", "hi"):  # the lowest sticker price, at a quantity inside the credited range (rc6 F2)
                if (n := (mk.get("credit") or {}).get(edge)):
                    plays[f"sticker_{edge}"] = [dict(buy(cheap), args={**buy(cheap)["args"], "qty": n})] + end
        plays["always_sell"] = [I("swap", item_in=i, item_out=1, amount_in=s["inventory"][str(i)], min_out=1) for i in held] + end
    return plays


def R_ADJ():
    from kami_drills.spec.rules import ADJ
    return ADJ


def lazy_allowed(spec, intents):
    return sp.waiting_is_right(spec) and all(i["verb"] in ("read", "wait", "schedule_wake", "end_session") for i in intents)


def invariance(spec, adapter, ref):
    """Status of this drill on one adapter, from the reference plays' verdicts there vs at the intent level (`ref`)."""
    out = {"adapter": adapter.ID, "status": "ok", "note": "", "quirks": []}
    native = spec["fork"].get("native_prefix") or {}
    if spec["harness"]["bound"] and not native.get("harness", "").startswith(adapter.HARNESS):
        return {**out, "status": "rendering_changed", "note": f"harness-bound to {native.get('harness')!r}; native_replay only there"}
    if adapter.render_mode(spec) is None:
        return {**out, "status": "rendering_changed", "note": f"cannot render any of {spec['fork']['rendering']}"}
    for name, p in _plays(spec).items():
        ep = adapter.play_intents(spec, p["intents"])
        if ep is None and p.get("is_recorded_mistake"):  # a lossy intent (e.g. a kamiswap buy), not the harness: skip it, say so
            out["note"] += f"play {name!r} not re-expressible on {adapter.ID}; checked at the intent level only. "
            continue
        if ep is None:
            return {**out, "status": "not_expressible", "note": f"play {name!r} has an act with no tool on {adapter.ID}"}
        r = ep.result()
        out["quirks"] = sorted(set(out["quirks"]) | set(r["quirks"]))
        if r["pass"] == ref[name]["pass"]:
            continue
        if p.get("is_recorded_mistake"):
            return {**out, "status": "solved_by_harness", "note": f"the recorded mistake passes on {adapter.ID}"}
        if r["quirks"]:
            return {**out, "status": "not_expressible", "note": f"play {name!r} blocked by harness quirk {r['quirks']}: harness_bound"}
        return {**out, "status": "verdict_changed", "note": f"play {name!r}: {ref[name]['pass']} -> {r['pass']} ({'; '.join(r['why'])})"}
    for name, calls in adapter.native_battery(spec).items():
        if adapter.play_tools(spec, calls).result()["pass"] and not sp.waiting_is_right(spec):
            out["note"] += f"native lazy pass: {name}. "
            out.setdefault("lazy", []).append(name)
    return out


def expected_failure(spec, failure):
    """A failure the suite declares: a lazy pass on a smoke or retired item, or on a verdict-equivalent v1 conversion
    (drills_v2/spec keeps v1's flaws on purpose). `spec check` reports these but exits 0 on them."""
    lazy = failure.startswith("lazy pass") or "native lazy pass" in failure
    inherited = spec.get("role") in ("smoke", "retired") or (spec.get("role") is None and
                                                             spec.get("variants", {}).get("origin") == "converted_v1")
    return lazy and inherited


def check_bank(specs, adapters=()):
    """Reports for a bank. Items paired by `variants.group` are gated together: a degenerate play may pass some members
    of a group (a blind act passes the opportunity, doing nothing passes its trap control) but at most half of them, so a
    constant policy averages <= 0.5 on the group. Ungrouped items must fail every play themselves."""
    reps = [check(s, adapters) for s in specs]
    groups = {}
    for s, r in zip(specs, reps):
        if g := s.get("variants", {}).get("group"):
            groups.setdefault(g, []).append(r)
    for g, rs in groups.items():
        for r in rs:
            r["failures"] = [f for f in r["failures"] if not f.startswith("lazy pass: ")]
        for name in rs[0]["battery"]:
            passed = [r for r in rs if r["battery"].get(name) and not r.get("lazy_ok", {}).get(name)]
            if 2 * len(passed) > len(rs):
                for r in passed:
                    r["failures"].append(f"lazy pass: {name} (passes {len(passed)}/{len(rs)} of group {g})")
    return reps


def check(spec, adapters=()):
    """Report for one spec. `failures` empty means it may enter a bank (a grouped item: see check_bank)."""
    rep = {"id": spec.get("id"), "failures": [], "plays": {}, "battery": {}, "adapters": []}
    rep["failures"] += [f"schema: {e}" for e in sp.validate(spec)]
    try:
        _plays_and_battery(spec, adapters, rep)
    except (KeyError, TypeError, ValueError, NotImplementedError, IndexError) as e:
        rep["failures"].append(f"could not run: {type(e).__name__}: {e}")
    return rep


def _plays_and_battery(spec, adapters, rep):
    sc, thr = spec["scoring"], sp.threshold(spec)
    null = sp.play(spec, [])
    rep["plays"]["null"] = {"score": null["score"], "pass": null["pass"], "expected": sc["null_play"]["expected"]}
    if abs(null["score"] - sc["null_play"]["expected"]) > 1e-9:
        rep["failures"].append(f"null play scores {null['score']:g}, declared {sc['null_play']['expected']:g}")
    if null["pass"] and not sp.waiting_is_right(spec):
        rep["failures"].append("null play passes")
    for name, p in _plays(spec).items():
        r = sp.play(spec, p["intents"])
        rep["plays"][name] = {"score": r["score"], "pass": r["pass"], "expected": p["expected"], "why": r["why"]}
        if abs(r["score"] - p["expected"]) > 1e-9:
            rep["failures"].append(f"play {name!r} scores {r['score']:g}, declared {p['expected']:g} ({'; '.join(r['why'])})")
    if not rep["plays"].get("oracle", {}).get("pass"):
        rep["failures"].append("oracle fails")
    if any(rep["plays"][n]["pass"] for n, p in _plays(spec).items() if p.get("is_recorded_mistake")):
        rep["failures"].append("recorded mistake passes")
    for a in spec.get("accepted", []):
        if not sp.play(spec, a["intents"])["pass"]:
            rep["failures"].append(f"accepted path {a['id']!r} fails the scoring rule")
    for name, intents in battery(spec).items():
        r = sp.play(spec, intents)
        rep["battery"][name] = r["pass"]
        if r["pass"] and (lazy_allowed(spec, intents) or timing_ok(spec, intents)):
            rep.setdefault("lazy_ok", {})[name] = True
        elif r["pass"] and spec.get("variants", {}).get("origin") != "trap_control":
            # a trap control passes by not making the act, so lazy plays pass it by design; its pair item gates them
            rep["failures"].append(f"lazy pass: {name}")
    ref = {n: rep["plays"][n] for n in _plays(spec)}
    for ad in adapters:
        inv = invariance(spec, ad, ref)
        rep["adapters"].append(inv)
        if inv["status"] == "verdict_changed":
            rep["failures"].append(f"{ad.ID}: verdict_changed: {inv['note']}")
        rep["failures"] += [f"{ad.ID}: native lazy pass: {n}" for n in inv.get("lazy", [])]
