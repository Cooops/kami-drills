"""Miner pipeline: run every rule over every session, gate each trigger into a candidate or a counted rejection, then
dedup, cap, pair controls and traps, then split. Deterministic: runs are processed in sorted order and every choice breaks
ties on tier, time and id, never on what other rules produced.

One scorer. Every candidate is converted to a v2 spec (spec/from_miner.py) and gated by `spec.check`, the same
simulator, rules library and scorer a model's trace gets:
  replay-gap guard   every state field the verdict depends on comes from the snapshot or the prefix, and is fresh
  oracle             the scripted correct play scores 1 against the fork state (no refusal, no band, no late flag)
  agent play         the agent's own recorded continuation fails (mistakes) or passes (discoveries)
  null + battery     no-calls, lazy and degenerate plays fail (spec/check.py battery; kitchen sink included)
  adapter            the kami-harness 4.7.0 adapter gives the same verdicts as the intent level
A candidate that fails only a constant-policy play (level every kami, stop every kami, complete every quest) is kept if
a genuine control from the same run pairs with it: check_bank then requires every play to pass at most half the pair.
"""
import functools
import json
import math
import re
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from kami_drills.adapters import harness_4_7 as h47
from kami_drills.miner import MINER_VERSION, SCHEMA
from kami_drills.miner.rules import COOLDOWN, ROOM, RULES, STARVING, available_quests, snapshot_reads
from kami_drills.miner.trace import REVIVES, TRANSIENT, block_at, load_run, state_at, time_at
from kami_drills import spec as sp
from kami_drills.spec import check as CK
from kami_drills.spec import from_miner

COVERED = re.compile("|".join(r.pattern for r in (COOLDOWN, ROOM, STARVING)), re.I)
HARNESS_BOUND = re.compile(r"quest 3900 is already completed", re.I)  # kami-harness 4.7.0 refuses the daily re-accept
STATIC_SOURCES = ("reference/", "game_rule", "telemetry", "call_argument")
# blind plays the node-choice, market and recovery drills meet with their pair (control / trap): the item may pass them, and
# check_bank holds them to at most half the pair (an unpaired item is dropped there)
PAIR_GATED = {"start_here", "nearest_node", "highest_node", "highest_cap_node", "body_match_node", "hand_match_node",
              "buy_cheapest", "bulk_cheapest", "sticker_lo", "sticker_hi",
              "claim_all", "claim_avail_revive_card", "sq997"}  # recovery: the trap has the revive quests claimed
CONSTANT_PLAYS = {"always_level": "level_up", "always_stop": "harvest_stop", "complete_all_quests": "complete_quest"}
GATED_TOOLS = ("harvest_start", "harvest_stop", "harvest_collect", "feed_kami", "liquidate_kami", "use_item_batch")
ADAPTERS = (h47,)
LAZY = ("no_calls", "end_session", "read_then_end", "wake_5", "wake_60", "wake_1440", "wait_300_end", "waits_3x_end")


def replay_gap(deps, fork_line):
    """Reasons the verdict's dependencies are not all present at the fork (empty = gap-free)."""
    bad = []
    for d in deps:
        src = d.get("source") or ""
        if d.get("value") is None and not d.get("nullable"):
            bad.append(f"missing:{d['field']}")
        elif src.startswith("transcript:L"):
            if int(src.split(":")[1][1:]) > fork_line:
                bad.append(f"after_fork:{d['field']}")
        elif not (src.startswith("snapshot:") or src.startswith("recorded_outcome:") or src.startswith(STATIC_SOURCES)):
            bad.append(f"unknown_source:{d['field']}")
    return bad


def fork_state(st, t):
    """The fork-time overlay the converter applies to the snapshot state, from fresh values only (unknown stays None:
    the rules library predicts no refusal from it)."""
    kamis = {}
    for k, d in st.kami.items():
        # stale values are unknown, except the latest in-prefix HP and level, which beat the session-start snapshot
        # (a fork after a revive, a stop or a level-up; review rc4 minor 1)
        v = lambda f: d[f].value if f in d and (not d[f].stale or (f in ("hp", "hp_total", "level") and d[f].src.startswith("transcript:"))) else None
        hp = st.hp_at(k, t)
        kamis[str(k)] = {"state": v("state"), "hp": round(hp) if hp is not None else v("hp"), "hp_total": v("hp_total"),
                         "cooldown_until": v("cooldown_until"), "levelup_ready": v("levelup_ready"),
                         "skill_points": v("skill_points"), "node": v("node"), "level": v("level"), "accrued": v("accrued")}
    room = st.account.get("room")
    qs = st.quest
    known = bool(qs) and all(not q["completable"].stale for q in qs.values() if "completable" in q)
    as_str = lambda d: None if d is None else {str(i): n for i, n in d.items()}
    musu = st.account.get("musu")
    return {"t0": t.timestamp(), "room": room.value if room and not room.stale else None, "kamis": kamis,
            # the latest in-prefix MUSU beats the session-start snapshot (review rc5: a swap had spent 5k MUSU). Foods stay
            # fresh-only: a stale read misses foods bought since, and made a valid cooldown-repeat oracle refuse a feed
            "musu": musu.value if musu and (not musu.stale or musu.src.startswith("transcript:")) else None,
            "foods": as_str(st.foods()), "revives": as_str(st.held(REVIVES)),
            "completable": sorted(q for q, d in qs.items() if "completable" in d and d["completable"].value) if known else None,
            "accepted_open": sorted(q for q, d in qs.items() if "complete" in d and d["complete"].value is False
                                    and not d["complete"].stale)}


def cooldown_evidence(s, fork, fs):
    """Post-fork chain evidence per kami on cooldown at the fork (F review M2): the last 'kami on cooldown' refusal
    before its first accepted gated act, and that acceptance. The rules library refuses until just after the refusal and
    gives full credit from the acceptance, so the item's own record never contradicts the scorer."""
    out = {}
    for k, v in fs["kamis"].items():
        if not v.get("cooldown_until") or v["cooldown_until"] <= fs["t0"]:
            continue
        ev = {}
        for c in s.calls:
            if c.line <= fork or c.tool not in GATED_TOOLS or c.args.get("dry_run") or not c.t or int(k) not in c.kami_ids():
                continue
            if c.ok:
                ev["accepted_at"] = c.t.timestamp()
                break
            if COOLDOWN.search(c.text):
                ev["refused_at"] = c.t.timestamp()
        if ev:
            out[k] = ev
    return out


@functools.lru_cache(maxsize=64)
def _lines(path):
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()]


def classify(rep, kind):
    """(rejection reason or None, constant plays the item needs a paired control for) from a spec.check report."""
    plays, constant = rep["plays"], []
    for f in rep["failures"]:
        if f.startswith("schema"):
            return "spec_invalid:" + f.split(":", 2)[1].strip()[:40], []
        if f.startswith("could not run"):
            return "spec_error", []
    oracle = plays.get("oracle", {})
    if not oracle.get("pass") or abs(oracle.get("score", 0) - 1) > 1e-9:
        why = "; ".join(oracle.get("why", [])) or "fails"
        return "oracle_invalid:" + re.sub(r"\d+(\.\d+)?", "N", why)[:70], []
    if "recorded mistake passes" in rep["failures"]:
        return "agent_play_passes", []
    if kind == "discovery" and not plays.get("recorded-play", {}).get("pass"):
        return "agent_play_fails_own_discovery", []
    if plays.get("null", {}).get("pass") and kind not in ("control", "trap_control"):
        return "passes_lazy_play:null", []
    for f in rep["failures"]:
        if f.startswith("lazy pass: "):
            name = f.removeprefix("lazy pass: ").split(" ")[0]
            if name in PAIR_GATED:
                continue
            if name in CONSTANT_PLAYS:
                constant.append(name)
            else:
                return ("passes_lazy_play:" if name in LAZY else "passes_degenerate:") + name, []
        elif "verdict_changed" in f:
            return "adapter:verdict_changed", []
        elif "native lazy pass" in f:
            return "passes_degenerate:" + f.rsplit(":", 1)[1].strip(), []
        elif not (f.startswith("play ") or f.startswith("null play") or f in ("recorded mistake passes", "oracle fails")):
            return "check:" + f[:60], []
    return None, constant


def candidate(meta, run, s, raw, fs, tier, tier_reason):
    fork = raw["fork_line"]
    if fs.get("stamina") is None:  # the latest in-prefix stamina (a read or a travel's stamina_remaining), not the snapshot's
        from kami_drills.miner.rules import stamina_at
        stam, _ = stamina_at(s, fork)  # a lower bound: the latest read minus 5 per later move
        if stam is not None:
            fs = dict(fs, stamina=stam)
    t = time_at(s, fork)
    rid = f"{meta['id'].split('@')[0]}-{run.name.removeprefix('s1-')}-s{s.n:04d}-L{fork}" \
        + (f"-{raw['id_suffix']}" if raw.get("id_suffix") else "")
    agent = raw["agent_action"]
    return {
        "id": rid, "schema": SCHEMA, "kind": raw.get("kind", meta["kind"]), "rule_id": meta["id"],
        "mistake_class": raw.get("mistake_class", meta["mistake_class"]), "family": raw.get("family", meta.get("family")),
        "competency": meta["competency"], "tier": tier, "tier_reason": tier_reason, "economic": meta["economic"],
        "objective": raw["objective"], "group": raw.get("group"), "responses": raw.get("responses"),
        "genuine_trap": raw.get("genuine_trap", False),
        "situation": {"text": raw["situation"], "session_start_snapshot": bool(s.snapshot)},
        "decision_point": {"session": s.n, "fork_line": fork, "fork_kind": raw["fork_kind"],
                           "prefix": f"transcript lines 1..{fork}; the model acts in place of line {fork + 1}",
                           "time": t.isoformat() if t else None, **block_at(s, fork)},
        "agent_action": agent, "expected": raw["expected"], "actual": raw["actual"], "why": raw["why"],
        "scoring": raw["scoring"], "oracle": raw["oracle"], "accepted_actions": raw["accepted_actions"],
        "accepted_paths": raw.get("accepted_paths", []), **{k: raw[k] for k in ("market", "node", "harness_bound", "fork_notes", "world_pools", "tags") if raw.get(k)},
        "fork_state": fs, "world_constants": raw.get("world_constants"), "dependencies": raw["dependencies"],
        "depends_on_non_snapshot": sorted({d["field"] for d in raw["dependencies"]
                                           if not str(d.get("source")).startswith("snapshot:")}),
        "replay_answers": raw.get("replay_answers", []), "available_from_snapshot": snapshot_reads(s),
        "severity": raw["severity"], "variants": raw.get("variants", []), "signature": "|".join(map(str, raw["signature"])),
        "competing_due": raw.get("competing_due", []),
        "provenance": {"run": run.name, "run_id": run.run_id, "source_model": run.model, "session": s.n,
                       "source_file": s.src, "fork_line": fork, "agent_line": agent.get("line"), "pins": run.pins,
                       "miner_version": MINER_VERSION, "rule_id": meta["id"], "session_end_reason": s.end_reason,
                       "credit": "Palaestra Season 1 runs (Coopes), CC-BY-4.0"},
    }


def summary(rep):
    return {"oracle": rep["plays"].get("oracle", {}).get("score"),
            "agent_play": next((v["score"] for k, v in rep["plays"].items() if k.startswith("recorded")), None),
            "null": rep["plays"].get("null", {}).get("score"), "battery_passed": sorted(n for n, ok in rep["battery"].items() if ok),
            "adapter": [a.get("status") for a in rep["adapters"]]}


def gate_candidate(cand, s, lines=None):
    """Convert one built candidate and run spec.check on it: (gated candidate, None) or (None, rejection reason).
    `lines` replaces the session's transcript (a trap control's rewritten prefix)."""
    try:
        spec, notes = from_miner.convert(cand, snapshot=s.snapshot, lines=lines if lines is not None else _lines(str(s.path)))
    except ValueError as e:
        return None, "spec_error:" + str(e)[:40]
    rep = CK.check(spec, ADAPTERS)
    why, constant = classify(rep, cand["kind"])
    if why:
        return None, why
    if cand["kind"] == "mistake":  # the agent's play must break the rule itself, not only collect refusal penalties
        bare = dict(spec, scoring={k: v for k, v in spec["scoring"].items() if k != "refusal_penalty"})
        mistake = next(p for p in spec["scoring"]["reference_plays"] if p.get("is_recorded_mistake"))
        if sp.play(bare, mistake["intents"])["pass"]:
            return None, "agent_play_passes_rule"
    return dict(cand, spec=spec, notes=notes, checks=summary(rep), needs_control=constant), None


def gate(meta, run, s, raw):
    """Turn a rule's raw candidate into a gated one (with its spec), or return a rejection reason."""
    fork = raw["fork_line"]
    gaps = replay_gap(raw["dependencies"], fork)
    if gaps:
        return None, "replay_gap:" + gaps[0].split(":")[0]
    st, t = state_at(s, fork), time_at(s, fork)
    fs = fork_state(st, t) | {"available": available_quests(run, s, fork)} | raw.get("fork_state_overrides", {})
    if ev := cooldown_evidence(s, fork, fs):
        fs["cooldown_evidence"] = ev
    tier = raw.get("tier_hint", "A")
    tier_reason = raw.get("tier_reason") or "one decision on the agent's own state; log replay answers it"
    if tier == "A" and raw.get("competing_due"):
        tier, tier_reason = "B", f"other due work at the fork ({raw['competing_due']}): order-dependent"
    return gate_candidate(candidate(meta, run, s, raw, fs, tier, tier_reason), s)


def normalize_reason(text):
    m = re.search(r"revert: ([^:'\"]+)", text) or re.search(r"no transaction sent: (.{0,60})", text)
    r = m.group(1) if m else ("semantic failure (reached_target/success false)" if text.lstrip().startswith("{") else text[:60])
    return re.sub(r"\s+", " ", re.sub(r"\d+(\.\d+)?", "N", re.sub(r"0x[0-9a-fA-F]+", "0x", r))).strip()


def completed_in(path):
    """Quest ids a run completed successfully (the lag rule needs a completion somewhere in the season)."""
    from kami_drills.miner.rules import completed_quest
    return {c.args.get("quest_index") for s in load_run(path).sessions for c in s.calls
            if c.tool == "complete_quest" and completed_quest(c, c.args.get("quest_index"))}


def mine_run(path, completed=frozenset(), experimental=frozenset()):
    from kami_drills.miner import rules
    from kami_drills.miner.controls import control_pool, traps_for
    rules.COMPLETED_ANYWHERE = set(completed)
    run = load_run(path)
    cands, rejects, unc, harness = [], [], Counter(), 0
    rej = lambda rid, why, s, line: rejects.append({"rule_id": rid, "reason": why, "run": run.name, "model": run.model,
                                                     "session": s.n, "line": line})
    for s in run.sessions:
        if s.misaligned:
            rej("*", "misaligned_telemetry", s, 0)
            continue
        for meta in RULES.values():
            if not meta.get("default", True) and meta["id"].split("@")[0] not in experimental:
                continue  # researched, not shipped (node-choice): only on request
            for f in meta["fn"](run, s):
                if f[0] == "reject":
                    rej(meta["id"], f[1], s, f[2])
                    continue
                cand, why = gate(meta, run, s, f[1])
                if why:
                    rej(meta["id"], why, s, f[1]["fork_line"])
                else:
                    cands.append(cand)
        for c in s.calls:
            if c.ok or not c.write or c.args.get("dry_run") or TRANSIENT.search(c.text):
                continue
            if HARNESS_BOUND.search(c.text):
                harness += 1  # a harness refusal (kami-harness@4.7.0 quest 3900 re-accept), not an agent mistake
            elif not COVERED.search(c.text):
                unc[(c.tool, normalize_reason(c.text))] += 1
    pool = {"controls": control_pool(run, [c for c in cands if c["needs_control"]]), "traps": traps_for(run, cands)}
    return run.name, run.model, len(run.sessions), cands, rejects, unc, harness, pool


def dedup(cands):
    """One candidate per signature (rule + decision-relevant state, threshold-free). The representative is a tier-A
    member when there is one (F review M5), then the earliest decision, then id: independent of severity, of other
    rules and of which other runs are mined."""
    groups = defaultdict(list)
    for c in cands:
        groups[(c["rule_id"], c["signature"])].append(c)
    out = []
    for key in sorted(groups):
        g = sorted(groups[key], key=lambda c: (c["tier"] != "A", c["decision_point"]["time"] or "", c["id"]))
        out.append(dict(g[0], family_size=len(g), also_seen=[
            {"run": c["provenance"]["run"], "session": c["provenance"]["session"],
             "line": c["decision_point"]["fork_line"]} for c in g[1:]]))
    return out


def cap(cands, per_run, per_model_share):
    """Per rule (so one rule's thresholds cannot move another's items): at most per_run from a source run and
    max(2, ceil(share x rule total)) from a source model; tier A first, then earliest."""
    keep, dropped = [], []
    for r in sorted({c["rule_id"] for c in cands}):
        rows = sorted((c for c in cands if c["rule_id"] == r),
                      key=lambda c: (c["tier"] != "A", c["decision_point"]["time"] or "", c["id"]))
        limit = max(2, math.ceil(per_model_share * len(rows)))
        by_run, by_model = Counter(), Counter()
        for c in rows:
            run_, m = c["provenance"]["run"], c["provenance"]["source_model"]
            if by_run[run_] >= per_run:
                dropped.append((c, "cap:per_run"))
            elif by_model[m] >= limit:
                dropped.append((c, "cap:per_model"))
            else:
                keep.append(c)
                by_run[run_] += 1
                by_model[m] += 1
    return sorted(keep, key=lambda c: c["id"]), dropped


def bank_gate(items):
    """check_bank over the kept specs: grouped items (item + control, opportunity + trap) are gated together, and a
    failing member takes its whole group out. Returns (kept, [(item, reason)])."""
    reps = CK.check_bank([c["spec"] for c in items], ADAPTERS)
    lazy = [[f for f in r["failures"] if f.startswith("lazy pass")] for r in reps]
    bad = {c["group"] for c, lz in zip(items, lazy) if lz and c.get("group")}
    out, dropped = [], []
    for c, lz in zip(items, lazy):
        name = lz[0].removeprefix("lazy pass: ").split(" ")[0] if lz else "pair"
        if c.get("group") in bad:
            dropped.append((c, "group_battery:" + name))
        elif lz:
            dropped.append((c, "passes_degenerate:" + name))
        else:
            out.append(c)
    return out, dropped


def mine(paths, jobs=1, per_run=3, max_model_share=0.3, experimental=()):
    from kami_drills.miner.controls import pair, pair_with
    paths = sorted(map(str, paths))
    from kami_drills.spec import pseudonymize as ps
    agents = {a.lower() for a in json.loads(ps.ALLOWLIST.read_text())["palaestra_s1_agents"]}
    ps.SEASON.update(ps.harvest_players(paths, agents))  # free-text names for the split; in memory only (rc6 F4)
    if jobs > 1:
        with ProcessPoolExecutor(jobs) as ex:
            done = frozenset().union(*ex.map(completed_in, paths))
            results = list(ex.map(functools.partial(mine_run, completed=done, experimental=frozenset(experimental)), paths))
    else:
        done = frozenset().union(*map(completed_in, paths))
        results = [mine_run(p, done, frozenset(experimental)) for p in paths]
    raw, rejects, sessions, unc, harness, pools = [], [], Counter(), Counter(), 0, {}
    for name, model, n, cands, rej, u, h, pool in results:
        raw += cands
        rejects += rej
        sessions[model] += n
        unc += u
        harness += h
        pools[name] = pool
    deduped = dedup(raw)
    keep_ids = {c["id"] for c in deduped}
    row = lambda c, why: {"rule_id": c["rule_id"], "reason": why, "run": c["provenance"]["run"],
                          "model": c["provenance"]["source_model"], "session": c["decision_point"]["session"],
                          "line": c["decision_point"]["fork_line"]}
    rejects += [row(c, "dedup:signature") for c in raw if c["id"] not in keep_ids]
    kept, dropped = cap(deduped, per_run, max_model_share)
    rejects += [row(c, why) for c, why in dropped]
    kept, controls, unpaired = pair(kept, {r: p["controls"] for r, p in pools.items()})
    rejects += [row(c, why) for c, why in unpaired]
    traps = [t for c in kept for t in [pools[c["provenance"]["run"]]["traps"].get(c["id"])] if t]
    paired = {t["group"] for t in traps}
    rejects += [row(c, "no_control:node_choice") for c in kept if c["rule_id"].startswith("node-choice@") and c["id"] not in paired]
    kept = [c for c in kept if not (c["rule_id"].startswith("node-choice@") and c["id"] not in paired)]
    kept = [pair_with(c, c["id"]) if c["id"] in paired else c for c in kept]
    kept_all, dropped = bank_gate(kept + controls + traps)
    rejects += [row(c, why) for c, why in dropped if c["kind"] not in ("control", "trap_control")]
    ids = {c["id"] for c in kept_all}
    return {"candidates": [c for c in kept_all if c["kind"] not in ("control", "trap_control")],
            "controls": [c for c in kept_all if c["kind"] == "control"],
            "traps": [c for c in kept_all if c["kind"] == "trap_control"],
            "raw": raw, "deduped": deduped, "rejections": rejects, "sessions": sessions, "uncovered_reverts": unc,
            "harness_bound": harness,
            "params": {"per_run": per_run, "max_model_share": max_model_share, "miner_version": MINER_VERSION,
                       **({"experimental": sorted(experimental)} if experimental else {})}}
