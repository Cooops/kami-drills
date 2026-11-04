"""stats.md for a mining pass. Public: counts, shares, gate results and public examples only. No held-out
answer, situation or figure is printed (split.leak_check verifies it before anything is written)."""
from collections import Counter

from kami_drills import spec as sp
from kami_drills.spec import check as CK


def _table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def battery_table(items):
    """Per degenerate play: ungrouped items it passes, and groups where it passes more than half (both must be 0)."""
    if not items:
        return []
    plays = sorted({n for c in items for n in CK.battery(c["spec"])} | {n for c in items for n in c["checks"]["battery_passed"]})
    plays = [n for n in plays if not n.startswith("move_")] + ["move_<room>_then_mistake"]
    groups = {}
    for c in items:
        if c.get("group"):
            groups.setdefault(c["group"], []).append(c)
    passed = lambda c, n: any(x.startswith("move_") for x in c["checks"]["battery_passed"]) if n.startswith("move_") \
        else n in c["checks"]["battery_passed"]
    rows = []
    for n in plays:
        solo = [c for c in items if not c.get("group") and passed(c, n) and c["kind"] not in ("control", "trap_control")]
        over = [g for g, cs in groups.items() if 2 * sum(passed(c, n) for c in cs) > len(cs)]
        rows.append([n, f"{len(solo)}/{sum(not c.get('group') for c in items)}", f"{len(over)}/{len(groups)}"])
    return rows


def econ_pairs(result):
    """Mean score over (opportunity, trap) pairs per play: only reading the state should beat 0.5 (D-spec 3.3)."""
    traps = {t["group"]: t for t in result["traps"] if t.get("group")}
    pairs = [(c, traps[c["id"]]) for c in result["candidates"] if c["economic"] and c["id"] in traps]
    out = {}
    for name in ("no_calls", "kitchen_sink", "token_move", "always_revive", "always_buy", "buy_cheapest", "bulk_cheapest", "sticker_lo", "sticker_hi", "always_sell"):
        bats = [(c, t, CK.battery(c["spec"]), CK.battery(t["spec"])) for c, t in pairs]
        vals = [(sp.play(c["spec"], bc[name])["score"] + sp.play(t["spec"], bt[name])["score"]) / 2
                for c, t, bc, bt in bats if name in bc and name in bt]  # market plays exist on market pairs only
        out[name] = (sum(vals) / len(vals) if vals else None, len(vals))
    oracle = [(sp.play(c["spec"], next(p["intents"] for p in c["spec"]["scoring"]["reference_plays"] if p["name"] == "oracle"))["score"]
               + sp.play(t["spec"], [])["score"]) / 2 for c, t in pairs]
    out["state_reading (oracle; nothing on the trap)"] = (sum(oracle) / len(oracle) if oracle else None, len(oracle))
    return out


def node_pairs(result):
    """Mean score over (node-choice item, its control) per play: only comparing the nodes should beat 0.5."""
    ctl = {c["group"]: c for c in result["controls"] if c.get("group")}
    pairs = [(c, ctl[c["id"]]) for c in result["candidates"] if c["rule_id"].startswith("node-choice@") and c["id"] in ctl]
    out = {}
    for name in ("no_calls", "kitchen_sink", "start_here", "nearest_node", "highest_node", "highest_cap_node",
                 "body_match_node", "hand_match_node"):
        vals = []
        for c, t in pairs:
            bc, bt = CK.battery(c["spec"]), CK.battery(t["spec"])
            vals.append((sp.play(c["spec"], bc.get(name, bc["no_calls"]))["score"] + sp.play(t["spec"], bt.get(name, bt["no_calls"]))["score"]) / 2)
        out[name] = (sum(vals) / len(vals) if vals else None, len(vals))
    orc = [(sp.play(c["spec"], c["spec"]["accepted"][0]["intents"])["score"] + sp.play(t["spec"], t["spec"]["accepted"][0]["intents"])["score"]) / 2
           for c, t in pairs]
    out["comparing the nodes (each oracle)"] = (sum(orc) / len(orc) if orc else None, len(orc))
    return out


def _example(c):
    p = c["provenance"]
    return (f"**{c['id']}** ({c['rule_id']}, tier {c['tier']}, {', '.join(c['competency'])}; {p['source_model']}, "
            f"{p['source_file']} fork L{c['decision_point']['fork_line']})\n"
            + (f"- objective (agent's words, in the prefix): {c['objective']['game'][:160]!r}\n" if c["objective"].get("game") else "")
            + f"- situation: {c['situation']['text']}\n- expected: {c['expected']}\n- actual: {c['actual']}\n"
              f"- why: {c['why']}\n- accepted: {'; '.join(c['accepted_actions'])}\n"
              f"- agent play {c['checks']['agent_play']}; severity {c['severity']['value']} {c['severity']['unit']}; "
              f"family size {c.get('family_size', 1)}\n")


def render(result, written, examples=(), elapsed=None):
    cands, raw, ded, rej = result["candidates"], result["raw"], result["deduped"], result["rejections"]
    pub, private = written["rows"]
    public_ids = {r["id"] for r in pub if not r.get("redacted")}
    held_ids = {p["orig"]["id"] for p in private}
    sessions = result["sessions"]
    open_ = [c for c in cands if not c["economic"]]
    lines = ["# Miner v2 - Season 1 candidates\n",
             f"miner {result['params']['miner_version']}; {sum(sessions.values())} sessions, "
             f"{len({c['provenance']['run'] for c in raw} | {r['run'] for r in rej})} runs; caps per rule: "
             f"{result['params']['per_run']} per run, max(2, {result['params']['max_model_share']:.0%}) per source model. "
             f"Output sha256[:16] {written['sha256_16']}" + (f"; {elapsed:.1f} s" if elapsed else "") + ".\n",
             f"**{len(cands)} items** ({sum(c['tier'] == 'A' for c in open_)} tier A, {sum(c['tier'] == 'B' for c in open_)} tier B "
             f"non-economic, {sum(c['economic'] for c in cands)} economic) from {len(raw)} gated triggers ({len(ded)} after dedup), "
             f"with {len(result['controls'])} paired controls and {len(result['traps'])} trap controls. "
             + (f"Split: {len(public_ids)} public, {len(held_ids)} held out (private variants; public stubs only)." if written.get("split") else "No split key: nothing split, no public file.") + "\n",
             "One scorer: every item is a v2 spec gated by `spec.check` (kami-rules simulator): the oracle scores 1, the "
             "agent's recorded play fails (discoveries: passes), and every lazy and degenerate play fails the item, or at "
             "most half its group (item + control, opportunity + trap).\n"]
    rules = sorted({c["rule_id"] for c in raw} | {r["rule_id"] for r in rej if r["rule_id"] != "*"})
    cnt = lambda rows, r: sum(c["rule_id"] == r for c in rows)
    lines += ["## By rule\n", _table(
        ["rule", "class", "gated", "after dedup", "kept", "tier A", "tier B", "public", "held out"],
        [[r, next((c["mistake_class"] for c in raw if c["rule_id"] == r), "-"), cnt(raw, r), cnt(ded, r), cnt(cands, r),
          sum(c["tier"] == "A" for c in cands if c["rule_id"] == r), sum(c["tier"] == "B" for c in cands if c["rule_id"] == r),
          sum(c["id"] in public_ids for c in cands if c["rule_id"] == r), sum(c["id"] in held_ids for c in cands if c["rule_id"] == r)] for r in rules]), ""]
    comp = Counter(t for c in cands for t in c["competency"])
    lines += ["## By competency\n", _table(["competency", "kept"], sorted(comp.items(), key=lambda x: -x[1])), ""]
    models = sorted(sessions)
    lines += ["## By source model (selection bias)\n", _table(
        ["model", "sessions", "gated", "per 100 sessions", "kept", "kept non-economic"],
        [[m, sessions[m], sum(c["provenance"]["source_model"] == m for c in raw),
          f"{100 * sum(c['provenance']['source_model'] == m for c in raw) / max(1, sessions[m]):.1f}",
          sum(c["provenance"]["source_model"] == m for c in cands),
          sum(c["provenance"]["source_model"] == m for c in open_)] for m in models]), ""]
    lines += ["## Lazy and degenerate plays (items, controls and traps)\n",
              "Ungrouped items must fail every play; in a group (item + control, opportunity + trap) a play may pass at most "
              "half the members.\n",
              _table(["play", "ungrouped items passed", "groups over half"], battery_table(cands + result["controls"] + result["traps"])), ""]
    lines += ["## Economic pairs (opportunities with their trap controls)\n",
              "Mean over (opportunity, trap) pairs; D-spec 3.3 wants blind plays at or under 0.5.\n",
              _table(["policy", "mean score", "pairs"], [[n, "-" if v is None else f"{v:.2f}", k] for n, (v, k) in econ_pairs(result).items()]), ""]
    lines += ["## Node-choice pairs (items with their controls)\n",
              "Mean over (item, control) pairs; a play missing on a spec (no matching node) is scored as no calls.\n",
              _table(["policy", "mean score", "pairs"], [[n, "-" if v is None else f"{v:.2f}", k] for n, (v, k) in node_pairs(result).items()]), ""]
    rr = Counter((r["rule_id"], r["reason"]) for r in rej)
    lines += ["## Rejections\n", _table(["rule", "reason", "count"],
                                        [[a, b, n] for (a, b), n in sorted(rr.items(), key=lambda x: (x[0][0], -x[1]))]), ""]
    fam = Counter(r["reason"].split(":")[0] for r in rej)
    lines += ["By reason family: " + ", ".join(f"{k} {v}" for k, v in fam.most_common()) + "\n",
              f"Harness-bound refusals (kami-harness 4.7.0 refuses re-accepting daily quest 3900; the chain accepts): "
              f"{result['harness_bound']}, not counted as agent mistakes.\n"]
    unc = result.get("uncovered_reverts") or Counter()
    if unc:
        lines += ["## Refusals no rule covers (coverage gap, top 15)\n",
                  _table(["tool", "normalized reason", "count"], [[t, r, n] for (t, r), n in unc.most_common(15)]), ""]
    shown = {c["id"]: c for c in cands if c["id"] in public_ids}
    picks = [shown[i] for i in examples if i in shown]
    lines += [f"## {len(picks)} hand-picked examples (public only)\n"] + [_example(c) for c in picks]
    return "\n".join(lines) + "\n"
