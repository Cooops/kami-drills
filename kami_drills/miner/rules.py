"""Trigger rules: each finds decision points where the agent's choice lost value against an action that was available
(or, for discoveries, gained value), and names the accepted plays as a `score.py` scoring.

A rule yields ("cand", dict) for a candidate or ("reject", reason, line) for a trigger it could not turn into one.
Gates shared by every rule (replay-gap guard, oracle checked against the fork state, the agent's play, the lazy and
degenerate plays, tiering, dedup, split) live in pipeline.py.

Thresholds are constants here; signatures never use them (pipeline dedup must not move when one changes).
"""
import csv
import json
import math
import re
from datetime import timedelta

from kami_drills.miner.trace import ITEMS, REVIVES, TRANSIENT, state_at, time_at
from kami_drills.spec.rules import ACCEPT_FROM_S, FULL_FROM_S, NODE_CAP, REVIVE_ITEMS as REVIVE_METHOD_ITEMS, WORLD, path_len

RULES = {}
WAIT_MAX_S = 300          # kami-agent wait cap (config.yaml caps.wait_max_seconds)
COOLDOWN_MIN_S = 10       # under this, the loss is a few seconds and inside the fork clock's precision
WAKE_MIN_MIN = 5          # wake.min_minutes
STARVE_MARGIN_H = 0.5     # HP must hit 0 at least this long before the chosen wake (projection error margin)
LIQ_HP_FRAC = 0.35        # a liquidation is a candidate only if HP at wake was this low (else not foreseeable)
REPEATS_MIN = 1           # a free (validation-only) refusal is mined only once the agent repeated it after being told
RECOLLECT_S = 1800        # a harvest_collect within this of a collect/start on the kami is pointless, not a drill
NO_LEVEL = re.compile(r"(do not|don'?t|never|avoid|no)\s+level(ling|ing)?\b|level cap|stay at (level|lv)", re.I)
HAS_OBJECTIVES = {int(k): v["has_objectives"] for k, v in WORLD["quests"].items()}
LEVEL_TOOLS = ("level_up_kami", "level_to", "level_and_allocate_batch", "feed_level_allocate_batch")
GATED = ("harvest_stop", "harvest_collect", "harvest_start", "feed_kami", "liquidate_kami")  # refused on cooldown
# Quests whose accept requires a room. 3039 "Danger Lies Ahead…": In Room 4 (Vending Machine); reward Neith's River of
# Life (item 21204, +80 operator stamina). S1: accepted only in room 4 (refused from room 3), and completed from rooms
# 4, 10, 33 and 62, so completion itself needs no room: completing from anywhere is accepted, travelling first too.
QUEST_ROOM = {3039: 4}
HOP_STAMINA = 5             # stamina per room move (kami-rules travel)


def rule(rule_id, mistake_class, competency, kind="mistake", economic=False, default=True):
    """default=False: researched, not shipped; the rule runs only when asked for (pipeline.mine experimental=...)."""
    def deco(fn):
        RULES[rule_id] = {"fn": fn, "id": rule_id, "mistake_class": mistake_class, "competency": competency,
                          "kind": kind, "economic": economic, "default": default}
        return fn
    return deco


def pat(tool, ok=False, kami=None, **args):
    p = {"tool": tool, "args": args}
    if kami is not None:
        p["kami"] = kami
    if ok:
        p["ok"] = True
    return p


def call(tool, **args):
    return [tool, args]


def end_fork(s):
    """Line before the agent's final end decision (its first set_next_wake/end_session after its last write)."""
    last_write = max((c.line for c in s.calls if c.ok and c.write), default=0)
    ends = [c for c in s.calls if c.tool in ("set_next_wake", "end_session") and c.line > last_write and not c.initiator]
    return (min(c.line for c in ends) - 1, ends) if ends else (None, [])


def agent_calls_before(s, line):
    return sum(1 for c in s.calls if c.line <= line and c.initiator != "scaffold")


def next_session(run, s):
    i = run.sessions.index(s)
    nxt = run.sessions[i + 1] if i + 1 < len(run.sessions) else None
    return nxt if nxt is not None and nxt.n == s.n + 1 else None


CRIPPLING = re.compile(r"LENS_UNAVAILABLE|NOT_APPLIED|state not ready|socket error", re.I)


def infra_session(s):
    """The game-state service was down for much of the session (most failures are lens/RPC outages). One broken tool
    (get_item_orderbook's missing cache) does not count: the agent can route around it."""
    bad = [c for c in s.calls if not c.ok]
    return len(bad) >= 3 and sum(bool(CRIPPLING.search(c.text)) for c in bad) >= len(bad) / 2


def snap_kamis(s):
    out = {}
    for c in s.snap_calls("lens_party"):
        for k in (c["response"].get("data") or {}).get("kamis") or []:
            out[k["index"]] = k
    return out


def snapshot_reads(s):
    return [{"tool": c["name"], "args": c.get("args") or {}} for c in s.snap_calls()]


def completed_quest(c, q):
    if not (c.ok and c.write):
        return False
    if c.tool == "complete_quest":
        return c.args.get("quest_index") == q
    return c.tool == "act_sequence" and any(isinstance(x, dict) and x.get("op") in ("complete_quest", "complete")
                                            and x.get("quest_index") == q for x in c.args.get("steps") or [])


def wrote_kami(c, k, tools):
    """A successful write on kami k by one of `tools`, directly or as an act_sequence step."""
    if not (c.ok and c.write and k in c.kami_ids()):
        return False
    if c.tool == "act_sequence":
        from kami_drills.task import sequence_step
        return any(isinstance(x, dict) and x.get("kami_id") == k and sequence_step(x)[0] in tools for x in c.args.get("steps") or [])
    return c.tool in tools


def bucket(x, edges):
    for e in edges:
        if x < e:
            return f"<{e}"
    return f">={edges[-1]}"


def active_hours(run, a, b):
    """Hours from a to b, minus intervals the run was paused: any wait for a session more than 30 min past its
    scheduled wake (the operator pause 2026-10-23 -> 2026-11-02 is one; infra holds are others)."""
    total = (b - a).total_seconds()
    for prev, nxt in zip(run.sessions, run.sessions[1:]):
        if prev.next_wake and nxt.start and nxt.start > prev.next_wake + timedelta(minutes=30):
            lo, hi = max(a, prev.next_wake), min(b, nxt.start)
            total -= max(0.0, (hi - lo).total_seconds())
    return max(0.0, total / 3600)


def first_later(run, s, done):
    for later in run.sessions[run.sessions.index(s) + 1:]:
        if any(done(c) for c in later.calls):
            return later
    return None


def run_end(run):
    return run.sessions[-1].end or run.sessions[-1].start


def stated_intent(s, line):
    """The agent's own last prose before `line` in this session (verbatim, untrusted), or None."""
    t = [(n, x) for n, x in s.texts if n < line and len(x) >= 15]
    return {"line": t[-1][0], "text": t[-1][1][:400]} if t else None


SESSION_GOAL = {"game": None, "source": "session start: the agent's standing goal from the system prompt",
                "shown_to_model": False}


# ------------------------------------------------------------------ progression: work left at session start

@rule("quest-left@1", "left_completable_quest", ["quest_progression"])
def quest_left(run, s):
    if not s.snapshot:
        return
    qs = sorted({c["args"]["quest_index"] for c in s.snap_calls("check_quest_completable")
                 if c["response"].get("completable")})
    if not qs:
        return
    if infra_session(s):
        yield ("reject", "infra_session", s.scaffold_end)
        return
    due = []
    for q in qs:
        if any(completed_quest(c, q) for c in s.calls):
            continue  # fine: no swing
        if contradicted(run, s, q):
            yield ("reject", "oracle_invalid:complete_refused_while_check_said_completable", s.scaffold_end)
            continue
        due.append(q)
    fork = s.scaffold_end
    st = state_at(s, fork)
    competing = [k for k, v in snap_kamis(s).items() if v.get("levelUpReady") or (v.get("state") == "HARVESTING"
                 and (v.get("hp") or {}).get("percent", 100) < 25)]
    for q in due:  # one item per quest: a quest is a situation, so it can never sit on both sides of the split
        where = quest_room_plan(s, st, q)
        if isinstance(where, str):
            yield ("reject", where, fork)
            continue
        later = first_later(run, s, lambda c, q=q: completed_quest(c, q))
        delay = active_hours(run, s.start, later.start if later else run_end(run))
        others = [x for x in due if x != q and HAS_OBJECTIVES.get(x, True)]  # objective-less quests: order irrelevant
        yield ("cand", {
            "fork_line": fork, "fork_kind": "session_start", "objective": SESSION_GOAL,
            "agent_action": {"summary": f"session ended ({s.end_reason}) without completing quest {q}"},
            "situation": f"Session {s.n} starts with accepted quest {q} completable (snapshot check_quest_completable).",
            "expected": f"complete_quest({q}) during the session",
            "actual": f"no complete_quest({q}); session ended by {s.end_reason}",
            "why": f"a completable quest gives its rewards and unlocks the next; left open {delay:.1f} active h"
                   + ("" if later else " (never completed in the data)"),
            "scoring": {"options": [{"credit": 1.0, "steps": [{"any": [pat("complete_quest", ok=True, quest_index=q)]}]}]},
            "oracle": where["oracle"] + [call("complete_quest", quest_index=q)],
            "accepted_paths": where["also"],
            "accepted_actions": [f"complete_quest(quest_index={q})"] + where["names"],
            "dependencies": [st.quest[q]["completable"].dep(f"quest.{q}.completable")] + where["deps"],
            "replay_answers": [{"call": f"complete_quest(quest_index={q})", "answer": "success (snapshot dry run: completable)",
                                "valid_while": "no inventory-changing write precedes it"}],
            "competing_due": competing + [f"quest {x}" for x in others],
            "severity": {"value": round(delay, 2), "unit": "active hours left completable",
                         "score": min(1.0, delay / 48), "detail": {"never_completed": later is None}},
            "signature": ("quest-left", q) + where["sig"], "id_suffix": f"q{q}",
            "variants": [{"path": "account.index", "constraint": "any registered account id (consistent everywhere)"},
                         {"path": "kami.*.hp", "constraint": "any value; the answer does not depend on kami HP"}],
        })


def quest_room_plan(s, st, q):
    """Room-aware answer for a quest whose accept needs a room (QUEST_ROOM): {oracle prefix, extra accepted paths, names,
    deps, signature part}. Completion needs no room (S1 completed 3039 from rooms 10, 33 and 62), so the oracle completes
    in place everywhere; outside the room, travelling there first is accepted too. Completing is one free call, so
    nothing here defers it (competing due work still makes the item tier B)."""
    if q not in QUEST_ROOM:
        return {"oracle": [], "also": [], "names": [], "deps": [], "sig": ()}
    room, target = st.account.get("room"), QUEST_ROOM[q]
    if not st.fresh(room) or room.value == target or path_len(room.value, target) is None:
        return {"oracle": [], "also": [], "names": [], "deps": [], "sig": ()}
    return {"oracle": [], "also": [[call("travel_to_room", target_room=target), call("complete_quest", quest_index=q)]],
            "names": [f"or travel_to_room({target}) then complete_quest({q})"], "deps": [room.dep("account.roomIndex")], "sig": ()}


COMPLETED_ANYWHERE = set()  # quests some S1 run completed (pipeline.mine fills it before the rules run)


def _lag_after_accept(x, i, q):
    """A refused complete_quest(q) at x.calls[i] that came in the very next call after a successful accept_quest(q), with
    a later check in the session saying completable: the dry run ran before the chain caught up with the accept
    (gpt-6.1-sol-3 s4 quest 3039; opus-5.5-1 s27 completed on its retry). Transient, not a contradiction, provided some
    S1 run completed the quest."""
    prev = x.calls[i - 1] if i else None
    if not (prev and prev.ok and prev.tool == "accept_quest" and prev.args.get("quest_index") == q):
        return False
    if q not in COMPLETED_ANYWHERE and not any(completed_quest(c, q) for y in x.run.sessions for c in y.calls):
        return False  # no run ever completed it: the refusal may be real
    return any(c.tool in ("check_quest_completable", "quest_state") and c.ok and isinstance(c.data, dict)
               and c.data.get("quest_index") == q and (c.data.get("completable") or c.data.get("completable_now"))
               for c in x.calls[i + 1:])


def contradicted(run, s, q):
    """True when, in some session up to this one, complete_quest(q) was refused although that session's
    completability evidence (snapshot or an in-session check, before or after the refusal) said yes, and no completion
    succeeded since. The dry-run check is then not evidence enough for the answer. A refusal in the very next call after
    the accept, with a later check saying completable, is chain lag (_lag_after_accept), not a contradiction."""
    last = None
    for x in run.sessions[:run.sessions.index(s) + 1]:
        said = any(c["args"]["quest_index"] == q and c["response"].get("completable")
                   for c in x.snap_calls("check_quest_completable")) or any(
            c.tool in ("check_quest_completable", "quest_state") and c.ok and isinstance(c.data, dict)
            and c.data.get("quest_index") == q and (c.data.get("completable") or c.data.get("completable_now"))
            for c in x.calls)
        for i, c in enumerate(x.calls):
            if c.tool == "complete_quest" and c.args.get("quest_index") == q and not c.args.get("dry_run"):
                if c.ok:
                    last = None
                elif said and not TRANSIENT.search(c.text) and not _lag_after_accept(x, i, q):
                    last = c
    return last is not None


def harvest_nodes(run, s, k, ahead=3):
    """Nodes kami k harvests from this session through the next `ahead` sessions, and the node it last harvested."""
    i = run.sessions.index(s)
    nodes = [c.args.get("node_index") for x in run.sessions[i:i + 1 + ahead] for c in x.calls
             if c.ok and c.tool == "harvest_start" and k in c.kami_ids()]
    for x in reversed(run.sessions[:i + 1]):
        v = snap_kamis(x).get(k) or {}
        if isinstance(v.get("node"), dict):
            nodes.append(v["node"].get("index"))
            break
    return [n for n in nodes if n is not None]


def notes_text(s):
    """What the agent wrote or read about its plan in this session: workspace reads (scaffold briefing included) and prose."""
    return "\n".join([c.text for c in s.calls if c.tool == "workspace_read" and c.ok] + [x for _, x in s.texts])


def competing_work(s, exclude_quest=None):
    """Other due work at a session start (order may matter): completable quests that have objectives, low-HP harvesters."""
    qs = sorted({c["args"]["quest_index"] for c in s.snap_calls("check_quest_completable") if c["response"].get("completable")})
    out = [f"quest {q}" for q in qs if q != exclude_quest and HAS_OBJECTIVES.get(q, True)
           and not any(completed_quest(c, q) for c in s.calls)]
    return out + [f"kami {k} at low HP" for k, v in snap_kamis(s).items()
                  if v.get("state") == "HARVESTING" and (v.get("hp") or {}).get("percent", 100) < 25]


@rule("levelup-left@1", "left_levelup", ["quest_progression", "resource_budgeting"])
def levelup_left(run, s):
    ready = [k for k, v in snap_kamis(s).items() if v.get("levelUpReady") and v.get("state") == "RESTING"]
    if not ready or infra_session(s):
        return
    due = [k for k in ready if not any(wrote_kami(c, k, LEVEL_TOOLS) for c in s.calls)]
    if not due:
        return
    fork = s.scaffold_end
    st = state_at(s, fork)
    if NO_LEVEL.search(notes_text(s)):
        yield ("reject", "stated_plan:do_not_level", fork)  # the agent's own plan says not to (gpt-6.1-sol-3 s54-s60)
        return
    capped = [k for k in due if st.kami_obs(k, "level") and any(
        NODE_CAP.get(n) is not None and st.kami_obs(k, "level").value + 1 > NODE_CAP[n] for n in harvest_nodes(run, s, k))]
    if capped:
        yield ("reject", "correct_play:level_cap_of_its_node", fork)  # levelling locks it out of its node (gdd nodes.md)
        return
    delay = []
    for k in due:
        later = first_later(run, s, lambda c, k=k: wrote_kami(c, k, LEVEL_TOOLS))
        delay.append(active_hours(run, s.start, later.start if later else run_end(run)))
    lv = {k: st.kami_obs(k, "level").value for k in due}
    yield ("cand", {
        "fork_line": fork, "fork_kind": "session_start", "objective": SESSION_GOAL,
        "agent_action": {"summary": f"session ended ({s.end_reason}) without levelling kami(s) {due}"},
        "situation": f"Session {s.n} starts with resting kami(s) {due} ready to level up (levels {lv}).",
        "expected": f"level up {due}",
        "actual": "no level-up this session",
        "why": f"a ready level-up costs nothing and grants a skill point and stats; banked {max(delay):.1f} active h",
        "scoring": {"options": [{"credit": 1.0, "partial": True, "ordered": False, "steps": [
            {"any": [pat(t, ok=True, kami=k) for t in LEVEL_TOOLS]} for k in due]}]},
        "oracle": [call("level_up_kami", kami_id=k) for k in due],
        "accepted_actions": [f"level_up_kami / level_to / a level batch targeting kami {k}" for k in due],
        "dependencies": [st.kami_obs(k, "levelup_ready").dep(f"kami.{k}.levelUpReady") for k in due]
                        + [st.kami_obs(k, "state").dep(f"kami.{k}.state") for k in due],
        "replay_answers": [{"call": f"level_up_kami(kami_id={k})", "answer": "success while RESTING"} for k in due],
        "competing_due": competing_work(s),
        "severity": {"value": round(sum(delay), 2), "unit": "active kami-hours with a level-up banked",
                     "score": min(1.0, sum(delay) / 48), "detail": {"delay_h": [round(d, 2) for d in delay]}},
        "signature": ("levelup-left", len(due), bucket(min(lv.values()) or 0, [5, 10, 20])),
        "variants": [{"path": f"kami.{k}.index", "constraint": "any id, renamed consistently"} for k in due],
    })


# ------------------------------------------------------------------ survival

@rule("hp-starve@1", "unattended_starvation", ["survival_hp", "cadence"])
def hp_starve(run, s):
    yield from _hp_loss(run, s, liquidation=False)


@rule("hp-liquidated@1", "unattended_low_hp_liquidated", ["survival_hp"])
def hp_liquidated(run, s):
    yield from _hp_loss(run, s, liquidation=True)


def _hp_loss(run, s, liquidation):
    nxt = next_session(run, s)
    if not nxt or not nxt.snapshot:
        return
    is_lost = lambda v: v.get("state") == "DEAD" or (v.get("state") == "HARVESTING" and (v.get("hp") or {}).get("current") == 0)
    before = snap_kamis(s)
    lost = {k: v for k, v in snap_kamis(nxt).items() if is_lost(v) and not (k in before and is_lost(before[k]))}
    fork, ends = end_fork(s)
    for k, after in lost.items():
        rej = None
        if fork is None:
            rej = "no_end_decision"
        else:
            st = state_at(s, fork)
            state = st.kami_obs(k, "state")
            if not st.fresh(state):
                rej = "replay_gap:kami_state"
            elif state.value != "HARVESTING":
                rej = "doomed_or_not_harvesting_at_fork"
            else:
                t = time_at(s, fork)
                hp, rate = st.hp_at(k, t), st.kami_obs(k, "hp_rate")
                rej = "replay_gap:hp_or_rate" if hp is None else None
        if rej:
            if not liquidation:  # count each trigger once
                yield ("reject", rej, fork if fork is not None else s.scaffold_end)
            continue
        total = (st.kami_obs(k, "hp_total").value if st.kami_obs(k, "hp_total") else None) or 1
        wake = s.next_wake or nxt.start
        to_zero_h = hp / -rate.value if rate.value < 0 else math.inf
        wake_h = (wake - t).total_seconds() / 3600
        starves = to_zero_h <= wake_h
        if starves == liquidation:
            continue  # the other rule's case
        if not liquidation and to_zero_h > wake_h - STARVE_MARGIN_H:
            yield ("reject", "no_margin:zero_within_30min_of_wake", fork)
            continue
        if hp >= 0.9 * total:
            yield ("reject", "no_margin:full_hp_at_fork", fork)  # leaving a full kami out is a judgement call
            continue
        hp_wake = max(0.0, hp + (rate.value or 0) * wake_h)
        if liquidation and hp_wake / total > LIQ_HP_FRAC:
            yield ("reject", "no_margin:healthy_at_wake", fork)
            continue
        cd, room, node = st.kami_obs(k, "cooldown_until"), st.account.get("room"), st.kami_obs(k, "node")
        if not st.fresh(cd):
            yield ("reject", "replay_gap:cooldown_until", fork)  # stop and feed are refused on cooldown
            continue
        if not (st.fresh(room) and st.fresh(node)):
            yield ("reject", "replay_gap:room", fork)  # stop and feed need the account in the kami's room
            continue
        if agent_calls_before(s, fork) >= 46:
            yield ("reject", "no_budget_left", fork)
            continue
        wait_s = full_credit_wait(s, fork, {k: cd.value}, t) if (cd.value or 0) > t.timestamp() else 0
        if wait_s > WAIT_MAX_S:
            yield ("reject", "doomed:cooldown_past_wait_cap", fork)
            continue
        foods = st.foods()
        floor = LIQ_HP_FRAC * total if liquidation else 0
        # food that keeps HP above the danger line at the chosen wake
        good_food = sorted(f for f in foods or {} if hp + ITEMS.get(f, {}).get("hp", 0) + (rate.value or 0) * wake_h > floor)
        prep = ([call("travel_to_room", target_room=node.value)] if room.value != node.value else []) \
            + ([call("wait", seconds=wait_s)] if wait_s else [])
        options = [{"credit": 1.0, "steps": [{"any": [pat("harvest_stop", ok=True, kami=k)]}]}]
        names = [f"harvest_stop(kami {k})"]
        if good_food:
            options.append({"credit": 1.0, "steps": [{"any": [pat("feed_kami", ok=True, kami=k, food_item_id=f)
                                                               for f in good_food]}]})
            names.append(f"feed_kami(kami {k}, one of {good_food})")
        safe_wake = math.floor(((hp - floor) / -rate.value if rate.value < 0 else 1e9) * 60) - WAKE_MIN_MIN
        if safe_wake >= WAKE_MIN_MIN:
            # waking before the danger point answers a liquidation risk; for certain starvation it is half credit
            # waking before the danger point is half credit: acting now in the session is better (B audit)
            options.append({"credit": 0.5, "steps": [{"any": [pat("set_next_wake", minutes_from_now={"lte": safe_wake})]}]})
            names.append(f"set_next_wake(<= {safe_wake} min) (partial)")
        dead = after.get("state") == "DEAD"
        sched = round(wake_h * 60)
        deps = [state.dep(f"kami.{k}.state"), st.kami_obs(k, "hp").dep(f"kami.{k}.hp"), rate.dep(f"kami.{k}.hpRatePerHr"),
                cd.dep(f"kami.{k}.cooldown_until"), room.dep("account.roomIndex"), node.dep(f"kami.{k}.node")]
        if foods is not None:
            deps.append(st.inv["__complete__"].dep("inventory"))
        sev_h = max(0.0, active_hours(run, t, nxt.start) - to_zero_h) if not liquidation else 0.0
        yield ("cand", {
            "fork_line": fork, "fork_kind": "before_end", "objective": SESSION_GOAL,
            "agent_action": {"tool": ends[0].tool, "args": ends[0].args, "line": ends[0].line},
            "situation": f"Kami {k} is HARVESTING at ~{hp:.0f}/{total} HP, changing {rate.value:+.2f}/h "
                         f"(zero in {to_zero_h:.1f} h); the agent is about to end the session (wake in ~{sched} min).",
            "expected": "; or ".join(names),
            "actual": f"{ends[0].tool}; kami {k} was {'DEAD' if dead else 'at 0 HP'} at the next session start",
            "why": ("HP reaches 0 before the chosen wake: the kami starves and anyone can liquidate it" if not liquidation
                    else f"left harvesting at ~{100 * hp_wake / total:.0f}% HP at the wake; it was liquidated (another "
                         "player's action, so the loss is not certain from the log)" if dead
                    else f"left harvesting at ~{100 * hp_wake / total:.0f}% HP at the wake; it starved to 0 HP before the "
                         "next session (no kill: the drain outran the recorded rate, so the loss is not certain from the log)"),
            "scoring": {"options": options},
            "oracle": prep + [call("harvest_stop", kami_ids=[k])],
            "accepted_actions": names,
            "dependencies": deps,
            "replay_answers": [{"call": f"harvest_stop(kami_ids=[{k}])", "answer": "success once off cooldown and in the "
                                "kami's room"}],
            "competing_due": [],
            "severity": {"value": round(sev_h, 2) if not liquidation else 1,
                         "unit": "active kami-hours at 0 HP" if not liquidation else "kami deaths",
                         "score": 1.0 if dead else min(1.0, sev_h / 12),
                         "detail": {"dead_next_session": dead, "hp_at_fork": round(hp, 1), "hp_total": total,
                                    "to_zero_h": round(to_zero_h, 2), "scheduled_wake_min": sched,
                                    "hp_at_wake": round(hp_wake, 1), "cooldown_wait_s": wait_s}},
            "tier_hint": "B" if liquidation else "A",
            "tier_reason": ("liquidation is another player's action (reactive)" if dead else
                            "0 HP before the projected time, without a kill (the drain is not certain from the log)") if liquidation else
                           "starvation follows from the agent's own wake choice and the HP rate (log replay valid)",
            "signature": ("hp-liquidated" if liquidation else "hp-starve", bucket(hp / total, [0.1, 0.25, 0.5]),
                          bucket(to_zero_h, [1, 3, 8]), bool(good_food), wait_s > 0),
            "variants": [{"path": f"kami.{k}.index", "constraint": "any id, renamed consistently"}],
        })


# ------------------------------------------------------------------ repeated refusals (not first tries)

COOLDOWN = re.compile(r"kami on cooldown", re.I)
ROOM = re.compile(r"kami too far|not in same room|must be in same room", re.I)
STARVING = re.compile(r"kami starving", re.I)
FREE = "no transaction sent"


# ------------------------------------------------------------------ cooldown timing (calibrated, kami-rules)

def cooldown_evidence(s, fork, kamis):
    """{kami: {"refused_at", "accepted_at"}} from the recorded continuation: the last 'kami on cooldown' refusal before
    the kami's first accepted gated act, and that acceptance (F review M2: the item's own record bounds the answer)."""
    out = {}
    for k in kamis:
        ev = {}
        for c in s.calls:
            if c.line <= fork or c.tool not in GATED + ("use_item_batch",) or c.args.get("dry_run") or not c.t or k not in c.kami_ids():
                continue
            if c.ok:
                ev["accepted_at"] = c.t.timestamp()
                break
            if COOLDOWN.search(c.text):
                ev["refused_at"] = c.t.timestamp()
        if ev:
            out[str(k)] = ev
    return out


def full_credit_wait(s, fork, until, t):
    """Seconds after the fork from which a gated act on these kamis scores full credit in kami-rules: past the
    calibrated band (ACCEPT_FROM_S..FULL_FROM_S after cooldown_until) or the item's own first acceptance, and never
    before its last recorded refusal. `until` is {kami: cooldown_until unix}."""
    ev = cooldown_evidence(s, fork, until)
    best = 0.0
    for k, u in until.items():
        e = ev.get(str(k)) or {}
        lo = max(u + ACCEPT_FROM_S, (e.get("refused_at") or -1) + 1)
        hi = min(u + FULL_FROM_S, e.get("accepted_at") or float("inf"))
        best = max(best, max(lo, hi) - t.timestamp())
    return math.ceil(best) if best > 0 else 0


def _key(args):
    return json.dumps({k: v for k, v in args.items() if k != "dry_run"}, sort_keys=True, default=str)


def live_args(args):
    return {k: v for k, v in args.items() if k != "dry_run"}


def _repeat_fork(s, rx, tools):
    """For each identical refused call: (first, fork_call or None, repeats, gas_spent). Mined only when the agent
    repeated it REPEATS_MIN+ more times after the first refusal (the drill forks at the first repeat, once the agent
    has been told) or the refusal spent gas. A first free refusal is not a mistake: the harness validates before
    signing, so trying and reading the error costs one call and no gas."""
    groups = {}
    for c in s.calls:
        if c.ok or c.tool not in tools or not rx.search(c.text) or c.args.get("dry_run"):
            continue
        groups.setdefault((c.tool, _key(c.args)), []).append(c)
    for cs in groups.values():
        gas = [c for c in cs if FREE not in c.text and "transaction 0x" in c.text]
        told = [c for c in cs[1:] if c.line > cs[0].result_line]  # a repeat in the same turn was not told yet
        if gas:
            yield cs[0], gas[0], len(cs), True
        elif len(told) >= REPEATS_MIN:
            yield cs[0], told[0], len(told), False
        else:
            yield cs[0], None, len(told), False


# the agent's prose must name the action (F review m4: "hp" and "harvest" matched most prose)
INTENT_WORDS = {"harvest_stop": r"\bstop", "harvest_collect": r"\bcollect", "harvest_start": r"\b(re)?start|\bresume|\bbegin harvest",
                "feed_kami": r"\bfeed|\bheal\b|\bfood", "level_up_kami": r"\blevel", "revive_kami": r"\breviv",
                "liquidate_kami": r"\bliquidat|\bkill", "use_item_batch": r"\bfeed|\buse (the |a |an )?\w+|\bitem",
                "listing_buy": r"\bbuy|\bpurchas"}


def _intent(s, first, fork_call):
    """Objective for a repeat drill: the agent's own prose up to its first attempt, and only if it names the action
    (else the goal would be inferred from the call, v1's top bug). Never the fork call's own line: the model replaces it
    (a gas-spending first refusal is itself the fork)."""
    it = stated_intent(s, min(first.line + 1, fork_call.line))
    if it is None or not re.search(INTENT_WORDS.get(first.tool, r"\w"), it["text"], re.I):
        return None
    return {"game": it["text"], "source": f"transcript:L{it['line']}", "shown_to_model": True}


@rule("cooldown-repeat@1", "repeated_on_cooldown", ["cooldown_awareness"])
def cooldown_repeat(run, s):
    for first, c, n, gas in _repeat_fork(s, COOLDOWN, GATED + ("level_up_kami", "revive_kami", "use_item_batch")):
        if c is None:
            yield ("reject", "free_error:first_try", first.line)
            continue
        fork, kamis = c.line - 1, c.kami_ids()
        if c.tool == "harvest_collect" and any(
                x.ok and x.write and x.tool in ("harvest_collect", "harvest_start") and set(kamis) & set(x.kami_ids())
                and x.t and c.t and (c.t - x.t).total_seconds() < RECOLLECT_S
                for x in s.calls if x.result_line < c.result_line):
            yield ("reject", "pointless:recollect", fork)  # collecting again minutes after a collect earns nothing
            continue
        objective = _intent(s, first, c)
        if objective is None:
            yield ("reject", "no_stated_intent", fork)
            continue
        st, t = state_at(s, fork), time_at(s, fork)
        known = {k: st.kami_obs(k, "cooldown_until") for k in kamis}
        known = {k: o for k, o in known.items() if st.fresh(o) and o.value}
        if not known:
            yield ("reject", "replay_gap:cooldown_until", fork)
            continue
        remaining = max(o.value - t.timestamp() for o in known.values())
        if remaining < COOLDOWN_MIN_S:
            yield ("reject", "no_margin:cooldown_nearly_over", fork)
            continue
        if remaining > WAIT_MAX_S:
            yield ("reject", "no_only_move:cooldown_longer_than_wait", fork)
            continue
        need = full_credit_wait(s, fork, {k: o.value for k, o in known.items()}, t)
        late = need + 60  # a minute of slack: waiting out the cooldown and then idling is a lesser play (0.6)
        extra = {a: v for a, v in live_args(c.args).items() if a in ("node_index", "food_item_id", "victim_kami_id")}
        # every kami in the batch must be acted on (minor 7: kamis[0] only let a mixed batch through)
        steps = [{"any": [pat(c.tool, ok=True, kami=k, **extra)], "min_clock": need, "max_clock": late} for k in kamis]
        early = [pat(g, kami=k) for g in GATED for k in kamis]
        k0 = max(known, key=lambda k: known[k].value)
        yield ("cand", {
            "fork_line": fork, "fork_kind": "before_call", "objective": objective,
            "agent_action": {"tool": c.tool, "args": c.args, "line": c.line, "result": c.text[:240]},
            "situation": f"Kami {k0} is on cooldown for another {remaining:.0f} s ({known[k0].src}); the agent has already "
                         f"been refused 'kami on cooldown' and is about to act on the kami again ({c.tool}).",
            "expected": f"let at least {need} s pass (waits, or other work), then {c.tool}",
            "actual": f"retried {c.tool} {n} more time(s), refused 'kami on cooldown' each time",
            "why": "the cooldown end was in state the agent had read, and the refusal said so; each retry burns one of "
                   "the session's 50 calls",
            "scoring": {"options": [{"credit": 1.0, "ordered": False, "steps": steps},
                                    {"credit": 0.6, "ordered": False, "steps": [{k: v for k, v in x.items() if k != "max_clock"} for x in steps]},
                                    {"credit": 0.5, "steps": [{"any": [pat("set_next_wake", minutes_from_now={"lte": 10})]}]}],
                        "zero_if": [{"any": early, "max_clock": need, "why": "a cooldown-gated action before it ends"}]},
            "oracle": [call("wait", seconds=need), [c.tool, live_args(c.args)]],
            "accepted_actions": [f"{c.tool} once >= {need} s have passed (by {late} s for full credit)",
                                 "set_next_wake(<= 10 min) (partial)"],
            "dependencies": [o.dep(f"kami.{k}.cooldown_until") for k, o in known.items()]
                            + [{"field": "clock", "value": t.isoformat(), "source": "telemetry tool_call ts", "as_of": None}],
            "replay_answers": [{"call": c.tool, "answer": f"refused 'kami on cooldown' until {need} s have passed"}],
            "competing_due": [],
            "severity": {"value": n, "unit": "repeated refused calls", "score": min(1.0, n / 4),
                         "detail": {"remaining_s": round(remaining, 1), "gas_spent": gas}},
            "tier_hint": "B" if c.tool == "liquidate_kami" else "A",
            "tier_reason": "the victim is another player's kami" if c.tool == "liquidate_kami" else None,
            "signature": ("cooldown-repeat", c.tool, bucket(remaining, [30, 120]), gas),
            "variants": [{"path": f"kami.{k0}.cooldown_until", "constraint": f"now + ({COOLDOWN_MIN_S}..{WAIT_MAX_S}) s; "
                          "the thresholds move with it"}],
        })


@rule("room-repeat@1", "repeated_from_wrong_room", ["spatial_awareness"])
def room_repeat(run, s):
    tools = ("feed_kami", "harvest_stop", "harvest_collect", "harvest_start", "listing_buy", "liquidate_kami")
    for first, c, n, gas in _repeat_fork(s, ROOM, tools):
        if c is None:
            yield ("reject", "free_error:first_try", first.line)
            continue
        fork = c.line - 1
        objective = _intent(s, first, c)
        if objective is None:
            yield ("reject", "no_stated_intent", fork)
            continue
        st = state_at(s, fork)
        here = st.account.get("room")
        target, tsrc, kind = None, None, None
        if c.tool in ("feed_kami", "harvest_stop", "harvest_collect", "liquidate_kami"):
            nodes = {st.kami_obs(k, "node").value for k in c.kami_ids() if st.fresh(st.kami_obs(k, "node"))} - {None}
            if len(nodes) == 1:
                target, kind = nodes.pop(), "kami"
                tsrc = [st.kami_obs(k, "node").dep(f"kami.{k}.node") for k in c.kami_ids()]
        elif c.tool == "harvest_start" and c.args.get("node_index") is not None:
            target, kind = c.args["node_index"], "node"
            tsrc = [{"field": "harvest_start.node_index", "value": target, "source": "call_argument", "as_of": None}]
        elif c.tool == "listing_buy":
            for m in reversed([x for x in s.calls if x.tool == "lens_merchant" and x.ok and x.result_line <= fork]):
                found = [x for x in ((m.data or {}).get("data") or {}).get("merchants") or []
                         if x.get("index") == c.args.get("merchant_index")]
                if found:
                    target, kind = found[0].get("roomIndex"), "merchant"
                    tsrc = [{"field": f"merchant.{c.args.get('merchant_index')}.roomIndex", "value": target,
                             "source": f"transcript:L{m.result_line}:lens_merchant", "as_of": m.t.isoformat()}]
                    break
        if target is None:
            yield ("reject", "replay_gap:target_room", fork)
            continue
        if not st.fresh(here):
            yield ("reject", "replay_gap:account_room", fork)
            continue
        if here.value == target:
            yield ("reject", "state_inconsistent:same_room", fork)
            continue
        move = [pat("travel_to_room", ok=True, target_room=target), pat("move_to_room", ok=True, room_index=target)]
        extra = {a: v for a, v in live_args(c.args).items() if a in ("node_index", "merchant_index", "food_item_id")}
        kamis = c.kami_ids() or [None]
        yield ("cand", {
            "fork_line": fork, "fork_kind": "before_call", "objective": objective,
            "agent_action": {"tool": c.tool, "args": c.args, "line": c.line, "result": c.text[:240]},
            "situation": f"The account is in room {here.value}; {c.tool} needs room {target}; the agent was already "
                         "refused once.",
            "expected": f"travel to room {target}, then {c.tool}",
            "actual": f"retried {c.tool} from room {here.value} {n} more time(s), refused each time",
            "why": "the room requirement and both rooms were in the state the agent had read, and the refusal said so",
            "scoring": {"options": [{"credit": 1.0, "steps": [{"any": move}] + [{"any": [pat(c.tool, ok=True, kami=k, **extra)]} for k in kamis]}],
                        "zero_if": [{"any": [pat(c.tool, kami=k, **extra) for k in kamis], "before": move,
                                     "why": "the same call again before moving"}]},
            "oracle": [call("travel_to_room", target_room=target), [c.tool, live_args(c.args)]],
            "accepted_actions": [f"travel_to_room({target}) or move_to_room({target}), then {c.tool}"],
            "dependencies": [here.dep("account.roomIndex")] + tsrc
                            + [{"field": "game_rule:node_index==room_index", "value": True,
                                "source": "game_rule (every lens_node read in Season 1)", "as_of": None}],
            "replay_answers": [{"call": f"travel_to_room({target})", "answer": "success (path from the room graph)"}],
            "competing_due": [],
            "severity": {"value": n, "unit": "repeated refused calls", "score": min(1.0, n / 4), "detail": {"gas_spent": gas}},
            "tier_hint": "B" if c.tool == "liquidate_kami" else "A",
            "tier_reason": "the victim is another player's kami" if c.tool == "liquidate_kami" else None,
            "signature": ("room-repeat", c.tool, kind, here.value, target),
            "variants": [{"path": "account.roomIndex", "constraint": f"any room != {target} with a path to it"}],
        })


@rule("starving-repeat@1", "repeated_stop_at_zero_hp", ["survival_hp"])
def starving_repeat(run, s):
    for first, c, n, gas in _repeat_fork(s, STARVING, ("harvest_stop", "harvest_collect")):
        if c is None:
            yield ("reject", "free_error:first_try", first.line)
            continue
        fork = c.line - 1
        objective = _intent(s, first, c)
        if objective is None:
            yield ("reject", "no_stated_intent", fork)
            continue
        st, t = state_at(s, fork), time_at(s, fork)
        zero = [k for k in c.kami_ids()
                if st.hp_at(k, t) == 0 or (st.fresh(st.kami_obs(k, "hp")) and st.kami_obs(k, "hp").value == 0)]
        if not zero:
            yield ("reject", "replay_gap:hp", fork)  # which kami is at 0 is not in the state
            continue
        k = zero[0]
        foods = st.foods()
        if foods is None:
            yield ("reject", "replay_gap:inventory", fork)
            continue
        if not foods:
            yield ("reject", "doomed:no_kami_food_held", fork)  # recovery needs a purchase first: multi-step
            continue
        feed = [pat("feed_kami", ok=True, kami=k, food_item_id=f) for f in sorted(foods)]
        yield ("cand", {
            "fork_line": fork, "fork_kind": "before_call", "objective": objective,
            "agent_action": {"tool": c.tool, "args": c.args, "line": c.line, "result": c.text[:240]},
            "situation": f"Kami {k} is harvesting at 0 HP; the account holds kami food {sorted(foods)}; the agent was "
                         "already refused once ('feed first').",
            "expected": f"feed kami {k}, then {c.tool}",
            "actual": f"retried {c.tool} {n} more time(s), refused 'kami starving' each time",
            "why": "a kami at 0 HP cannot stop or collect; the refusal says to feed first",
            "scoring": {"options": [{"credit": 1.0, "steps": [{"any": feed},
                                                              {"any": [pat(["harvest_stop", "harvest_collect"], ok=True, kami=k)]}]}],
                        "zero_if": [{"any": [pat(["harvest_stop", "harvest_collect"], kami=k)], "before": feed,
                                     "why": "stop or collect before feeding"}]},
            "oracle": [call("feed_kami", kami_id=k, food_item_id=sorted(foods)[0]), [c.tool, live_args(c.args)]],
            "accepted_actions": [f"feed_kami(kami {k}, one of {sorted(foods)}), then {c.tool}"],
            "dependencies": [st.inv["__complete__"].dep("inventory"), st.kami_obs(k, "hp").dep(f"kami.{k}.hp")],
            "replay_answers": [{"call": c.tool, "answer": "refused 'kami starving' until a feed succeeds"}],
            "competing_due": [],
            "severity": {"value": n, "unit": "repeated refused calls", "score": min(1.0, n / 3), "detail": {"gas_spent": gas}},
            "signature": ("starving-repeat", c.tool, bucket(len(foods), [2, 4])),
            "variants": [{"path": "food balances", "constraint": ">= 1"}],
        })


# ------------------------------------------------------------------ node choice (miner 3.2)
# A harvest started on a node that pays clearly less than a node a few rooms away, for this kami, from facts at the fork.
# Harvest rate (gdd economy/harvesting.md, Bounty Formula), in MUSU/h:
#   fertility = Power x 1.5 x Efficacy / 1000, Efficacy = 1000 + body shift (+650 / -250) + hand shift (+350 / -100)
#   intensity = (5 x Violence + minutes harvesting) / 48, the same at every node
# The gap is the fertility gap diluted by intensity; intensity is taken over a 12 h harvest and doubled (skill boosts
# are not in the state), which can only shrink the gap. HP strain is per MUSU, so a faster node also drains faster: the
# gain is MUSU per hour, not per harvest. Bounty boosts multiply every node alike and cancel.

NODES = {int(k): v for k, v in WORLD["nodes"].items()}
GATED_ROOMS = set(WORLD.get("gated_rooms", ()))
QUEST_TIES = {int(k): v for k, v in WORLD["quests"].items()}
NODE_HOPS_MAX = 3         # alternatives within three room moves (15 stamina)
NODE_MARGIN = 1.5         # the alternative must pay >= 1.5x the choice ...
NODE_GAIN_MIN = 5.0       # ... and >= 5 MUSU/h more (the only-move margin)
INTENSITY_MIN = 720       # intensity averaged over a 12 h harvest ...
INTENSITY_X = 2           # ... and doubled: unknown intensity boosts only shrink the gap
NODE_REASON = re.compile(r"scaveng|droptable|drop table|\bdrops?\b|liquidat|\bkill|\bhunt|predator|objective", re.I)
QUESTISH = re.compile(r"quest|objective|scaveng|droptable|drop table|\bdrops?\b|\bgive\b|\bburn|\bcraft", re.I)


def efficacy(body, hand, node_aff):
    """gdd LibAffinity: body and hand each match a node affinity (a dual node takes the better pairing)."""
    def shift(trait, aff, strong, weak):
        return 0 if trait in (None, "NORMAL") or aff == "NORMAL" else (strong if trait == aff else -weak)
    pairs = [(node_aff[0], node_aff[-1]), (node_aff[-1], node_aff[0])] if node_aff else [("NORMAL", "NORMAL")]
    return max(1000 + shift(body, a, 650, 250) + shift(hand, b, 350, 100) for a, b in pairs)


def harvest_rate(stats, node):
    """MUSU/h a kami earns at a node (conservative intensity, see above)."""
    e = efficacy(stats["body"], stats["hand"], NODES[node]["affinity"])
    return stats["power"] * 1.5 * e / 1000 + INTENSITY_X * (5 * stats["violence"] + INTENSITY_MIN / 2) / 48


def kami_stats(s, line):
    """{kami: {body, hand, power, violence, level, src, t}} from the latest read with affinities and stats at or before
    `line` (snapshot reads first)."""
    out = {}

    def take(k, src, t):
        if isinstance(k, dict) and k.get("index") is not None and k.get("affinities") and isinstance(k.get("stats"), dict):
            st, aff = k["stats"], list(k["affinities"]) + [None]
            try:
                out[int(k["index"])] = {"body": aff[0], "hand": aff[1], "power": st["power"]["total"],
                                        "violence": st["violence"]["total"], "level": k.get("level"), "src": src, "t": t}
            except (KeyError, TypeError):
                pass

    def body(d):
        b = d.get("data", d) if isinstance(d, dict) else None
        return b if isinstance(b, dict) else {}
    for c in s.snap_calls():
        b = body(c["response"])
        for k in [b] + list(b.get("kamis") or []):
            take(k, f"snapshot:{c['name']}", s.start)
    for c in s.calls:
        if c.result_line > line:
            break
        if c.ok and not c.write:
            b = body(c.data)
            for k in [b] + list(b.get("kamis") or []):
                take(k, f"transcript:L{c.result_line}:{c.tool}", c.t)
    return out


def stamina_at(s, line):
    """(lower bound on account stamina at `line`, source) or (None, None): the latest read, minus 5 per later move."""
    val, src = None, None
    for c in s.snap_calls("lens_account"):
        st = ((c["response"].get("data") or {}).get("stamina") or {})
        if isinstance(st, dict) and st.get("current") is not None:
            val, src = st["current"], "snapshot:lens_account"
    for c in s.calls:
        if c.result_line > line:
            break
        if not c.ok:
            continue
        d = c.data if isinstance(c.data, dict) else {}
        b = d.get("data", d) if isinstance(d.get("data", d), dict) else {}
        if c.tool == "lens_account" and isinstance(b.get("stamina"), dict) and b["stamina"].get("current") is not None:
            val, src = b["stamina"]["current"], f"transcript:L{c.result_line}:lens_account"
        elif c.tool == "travel_to_room" and c.write and isinstance(d.get("stamina_remaining"), int):
            val, src = d["stamina_remaining"], f"transcript:L{c.result_line}:travel_to_room"
        elif c.tool in ("move_to_room", "travel_to_room") and c.write and val is not None:
            val = max(0, val - HOP_STAMINA * (d.get("hops") or 1))
    return val, src


def rooms_within(room, hops_max):
    """{room: hops} on the room graph from `room`, through ungated rooms only (gates are not in the state)."""
    from kami_drills.spec.rules import ADJ
    d, q = {room: 0}, [room]
    for r in q:
        if d[r] >= hops_max:
            continue
        for x in sorted(ADJ.get(r, ())):
            if x not in d and x not in GATED_ROOMS:
                d[x] = d[r] + 1
                q.append(x)
    return d


def open_quests(s, line):
    """Quests accepted and not completed at `line`: the snapshot's lens_quests, then accepts and completions in the prefix."""
    qs = {q["index"] for c in s.snap_calls("lens_quests") for q in ((c["response"].get("data") or {}).get("accepted") or [])
          if isinstance(q, dict) and not q.get("complete")}
    for c in s.calls:
        if c.result_line > line:
            break
        if c.ok and c.write and c.tool == "accept_quest":
            qs.add(c.args.get("quest_index"))
        elif completed_quest(c, c.args.get("quest_index")):
            qs.discard(c.args.get("quest_index"))
    return qs


def scouted(s, line, node):
    """True when the prefix reads `node` as an attacker would (lens_node with an attacker kami): a liquidation plan."""
    return any(c.tool == "lens_node" and c.result_line <= line and c.args.get("node_index") == node
               and isinstance(c.args.get("attacker_kami_index"), int) and c.args["attacker_kami_index"] >= 0 for c in s.calls)


def node_contended(s, line, node):
    """True when a lens_node read of `node` in the prefix shows a harvest by another account."""
    me = next(((c["response"].get("data") or {}).get("index") for c in s.snap_calls("lens_account")), None)
    for c in s.calls:
        if c.result_line > line:
            break
        b = (c.data or {}).get("data") if isinstance(c.data, dict) else None
        if c.ok and c.tool == "lens_node" and isinstance(b, dict) and b.get("index") == node:
            if any((h.get("account") or {}).get("index") not in (None, me) for h in b.get("harvests") or [] if isinstance(h, dict)):
                return True
    return False


@rule("node-choice@1", "weak_node_choice", ["harvest_mechanics", "resource_budgeting"], default=False)
def node_choice(run, s):
    for c in s.calls:
        if not (c.ok and c.write and c.tool == "harvest_start") or c.initiator == "scaffold":
            continue
        node, kamis, fork = c.args.get("node_index"), c.kami_ids(), c.line - 1
        if node not in NODES or NODES[node]["yield_item"] != 1 or not kamis:
            continue  # a VIPP node (or an unknown one) is not comparable in MUSU
        if len(NODES[node]["affinity"]) > 1:
            continue  # dual-affinity node: the chain's single-string compare and the gdd's better pairing disagree
        stats = kami_stats(s, fork)
        if any(k not in stats for k in kamis):
            yield ("reject", "replay_gap:kami_affinity_or_stats", fork)
            continue
        st, t = state_at(s, fork), time_at(s, fork)
        room = st.account.get("room")
        if not st.fresh(room) or room.value != node:
            yield ("reject", "replay_gap:account_room", fork)
            continue
        choice = sum(harvest_rate(stats[k], node) for k in kamis)
        reach = rooms_within(room.value, NODE_HOPS_MAX)
        levels = [stats[k]["level"] for k in kamis]
        good = {}
        for n, hops in reach.items():
            if n == node or n not in NODES or NODES[n]["yield_item"] != 1 or not hops or len(NODES[n]["affinity"]) > 1:
                continue
            if NODES[n]["level_cap"] is not None and any(lv is None or lv > NODES[n]["level_cap"] for lv in levels):
                continue
            alt = sum(harvest_rate(stats[k], n) for k in kamis)
            if alt >= NODE_MARGIN * choice and alt - choice >= NODE_GAIN_MIN \
                    and all(harvest_rate(stats[k], n) >= harvest_rate(stats[k], node) for k in kamis):
                good[n] = (alt, hops)
        if not good:
            continue  # fine: no reachable node beats the choice by the margin
        if infra_session(s):
            yield ("reject", "infra_session", fork)
            continue
        stamina, ssrc = stamina_at(s, fork)
        if stamina is None:
            yield ("reject", "replay_gap:stamina", fork)
            continue
        good = {n: v for n, v in good.items() if HOP_STAMINA * v[1] <= stamina}
        if not good:
            yield ("reject", "no_only_move:not_enough_stamina_to_reach", fork)
            continue
        others = [k for k, d in st.kami.items() if k not in kamis and st.fresh(d.get("state")) and d["state"].value == "HARVESTING"]
        if others:
            yield ("reject", "competing:other_kamis_harvesting", fork)  # leaving strands them (stop and feed need the room)
            continue
        ties = [QUEST_TIES.get(q) or {} for q in open_quests(s, fork)]
        if node in {n for t_ in ties for n in t_.get("nodes", [])} or any(t_.get("harvest_any_node") for t_ in ties):
            yield ("reject", "correct_play:quest_objective_at_node", fork)  # a harvest-time quest at any node ties every node
            continue
        said = stated_intent(s, c.line + 1)  # the call's own line too: the agent's reason for this act (never shown)
        # all of the session's prose to the fork, the notes it read and the reason it ended on (review rc4 M3, rc5)
        prose = " ".join([x for n, x in s.texts if n <= c.line] + [x.text for x in s.calls if x.result_line <= fork and x.tool == "workspace_read" and x.ok]
                         + [str(x.args.get("reason") or "") for x in s.calls if x.tool == "end_session"])
        if (said and QUESTISH.search(said["text"])) or NODE_REASON.search(prose):
            yield ("reject", "stated_plan:quest_or_scavenge", fork)
            continue
        if any((QUEST_TIES.get(q) or {}).get("liquidate") for q in open_quests(s, fork)) and scouted(s, fork, node):
            yield ("reject", "correct_play:liquidation_quest_scouting", fork)  # haiku-5.5-1 s27: quest 6, node 30 scouted
            continue
        good = {n: v for n, v in good.items() if not node_contended(s, fork, n)}
        if not good:
            yield ("reject", "no_only_move:contention_at_alternative", fork)
            continue
        cds = {k: st.kami_obs(k, "cooldown_until") for k in kamis}
        cds = {k: o.value for k, o in cds.items() if st.fresh(o) and o.value}
        # a cooldown already over at the fork carries no evidence into the spec (pipeline.cooldown_evidence): wait out the
        # calibrated band; one still running uses the item's own record
        late = max(cds.values(), default=0)
        wait_s = full_credit_wait(s, fork, cds, t) if late > t.timestamp() else max(0, math.ceil(late + FULL_FROM_S - t.timestamp()))
        if wait_s > WAIT_MAX_S:
            yield ("reject", "doomed:cooldown_past_wait_cap", fork)
            continue
        best = max(good, key=lambda n: (good[n][0], -good[n][1], -n))
        gain = good[best][0] - choice
        steps = [{"any": [pat("harvest_start", ok=True, kami=k, node_index=n) for n in sorted(good)]} for k in kamis]
        aff = lambda n: "/".join(NODES[n]["affinity"]) or "?"
        traits = sorted((stats[k]["body"], stats[k]["hand"]) for k in kamis)
        deps = [room.dep("account.roomIndex"),
                {"field": "account.stamina", "value": stamina, "source": ssrc, "as_of": None},
                {"field": "node affinities, yield items and level caps", "value": "kami-rules world",
                 "source": "reference/catalogs/rooms/nodes.csv", "as_of": None},
                {"field": "room graph", "value": "kami-rules world", "source": "game_rule (catalogs/rooms/rooms.csv)", "as_of": None}]
        for k in kamis:
            deps.append({"field": f"kami.{k}.affinities+stats", "value": [stats[k]["body"], stats[k]["hand"], stats[k]["power"],
                         stats[k]["violence"], stats[k]["level"]], "source": stats[k]["src"],
                         "as_of": stats[k]["t"].isoformat() if stats[k]["t"] else None})
        yield ("cand", {
            "fork_line": fork, "fork_kind": "before_call", "objective": SESSION_GOAL,
            "agent_action": {"tool": c.tool, "args": c.args, "line": c.line},
            "situation": f"Kami(s) {kamis} ({', '.join('/'.join(map(str, x)) for x in traits)}) are about to start harvesting "
                         f"at node {node} ({aff(node)}); node {best} ({aff(best)}) is {good[best][1]} room move(s) away.",
            "expected": f"travel and harvest at a node paying >= {NODE_MARGIN}x: one of {sorted(good)} (best {best})",
            "actual": f"harvest_start at node {node}",
            "why": f"~{choice:.0f} MUSU/h at node {node} against ~{good[best][0]:.0f} at node {best} for these kamis "
                   f"(affinity efficacy, gdd harvesting.md); the move costs {HOP_STAMINA * good[best][1]} stamina",
            "scoring": {"options": [{"credit": 1.0, "ordered": False, "steps": steps}],
                        "zero_if": [{"any": [pat("harvest_start", kami=k, node_index=node) for k in kamis],
                                     "why": f"started at node {node}, which pays under 1/{NODE_MARGIN} of a node in reach"}]},
            "oracle": ([call("wait", seconds=wait_s)] if wait_s else []) + [call("travel_to_room", target_room=best),
                                                                            call("harvest_start", kami_ids=kamis, node_index=best)],
            "accepted_actions": [f"harvest_start(kami {k}) at node {sorted(good)}" for k in kamis],
            "dependencies": deps,
            "replay_answers": [{"call": f"travel_to_room({best}); harvest_start(node {best})", "answer": "success (path, stamina, level cap)"}],
            "competing_due": [],
            "world_constants": {"node_choice": {"max_hops": NODE_HOPS_MAX, "margin": NODE_MARGIN,
                                                "traits": {str(k): [stats[k]["body"], stats[k]["hand"]] for k in kamis}}},
            "node": {"node": node, "kamis": kamis, "rate": round(choice, 2)},
            "fork_state_overrides": {"stamina": stamina},
            "severity": {"value": round(gain, 1), "unit": "MUSU/h forgone", "score": min(1.0, gain / 50),
                         "detail": {"choice_rate": round(choice, 1), "best_rate": round(good[best][0], 1), "hops": good[best][1],
                                    "accepted_nodes": sorted(good)}},
            "tier_hint": "A",
            "tier_reason": "the yield gap follows from the gdd formula and facts at the fork; no known contention at the "
                           "accepted nodes (predators there are unknown at the fork, as at the choice)",
            "signature": ("node-choice", node, best, tuple(traits)),
            "variants": [{"path": "kami.*.index", "constraint": "any id, renamed consistently"}],
        })


# ------------------------------------------------------------------ market price (miner 3.2)
# A kami food bought from a merchant listing when another food on the same listing, in the same read, gave at least the
# same HP for under 1/1.5 of the MUSU. Prices are GDA (gdd economy/npc-shops.md): they move with every player's buys
# and decay over time, so only the read at the fork defines the answer, never a later price. One batch feed
# (use_item_batch) applies any number of foods, so more, smaller foods cost no extra calls.

BUY_SLACK = 2             # credit a buy of up to 2x the right-sized heal: a blind bulk buy is not the answer
MARKET_REASON = re.compile(r"\bcraft|\bburn|\bgive\b|recipe|objective", re.I)  # anywhere in the session's prose to the fork
FOOD_MARGIN = 1.5         # the alternative buys the same HP for <= 1/1.5 of the MUSU ...
FOOD_SAVE_MIN = 30        # ... and saves >= 30 MUSU
PRICE_READ_MAX_S = 900    # a listing read older than this is stale for a GDA price
KAMI_FOOD_HP = {i: v["hp"] for i, v in ITEMS.items() if v.get("type") == "FOOD" and v.get("for") == "KAMI" and v.get("hp")}


def merchant_read(s, line, merchant):
    """(call, merchant index, {item: buyPrice}) for the latest lens_merchant read of `merchant`'s listings at or before
    `line` (a read of one merchant only: listings do not say whose they are)."""
    best = None
    for c in s.calls:
        if c.result_line > line:
            break
        b = (c.data or {}).get("data") if isinstance(c.data, dict) else None
        if c.ok and c.tool == "lens_merchant" and isinstance(b, dict) and b.get("listings"):
            ms = [m.get("index") for m in b.get("merchants") or [] if isinstance(m, dict)]
            ok = [l for l in b["listings"] if isinstance(l, dict) and isinstance(l.get("item"), dict)
                  and isinstance(l.get("buyPrice"), int) and (l.get("payItem") or {}).get("index") == 1]
            if ms == [merchant]:
                best = (c, merchant, {l["item"]["index"]: l["buyPrice"] for l in ok},
                        {l["item"]["index"]: l.get("buy") or {} for l in ok})
    return best


def batch_cost(price, qty, gda):
    """MUSU for qty units from a GDA listing whose spot price is `price` (gdd npc-shops.md: each unit compounds the
    price by decay^(-1/rate)); flat when the listing states no GDA terms."""
    try:
        c = float(gda["decay"]) ** (-1 / float(gda["rate"]))
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return price * qty
    return price * qty if abs(c - 1) < 1e-12 else math.ceil(price * (c ** qty - 1) / (c - 1))


def food_alternatives(item, qty, prices, gda=None, caps=None):
    """{food: (units, MUSU)} on the listing that buy at least the usable HP of qty x item for <= 1/FOOD_MARGIN of its
    MUSU, under every cap in `caps` (each unit gives at most the cap: the kami's missing HP at the fork, or its max HP;
    review rc4 M2). Each side is priced as a GDA batch from the read."""
    gda, caps, out = gda or {}, caps or [math.inf], {}
    cost = batch_cost(prices[item], qty, gda.get(item))
    for y, py in prices.items():
        if y == item or y not in KAMI_FOOD_HP:
            continue
        per = [math.ceil(qty * min(KAMI_FOOD_HP[item], cap) / min(KAMI_FOOD_HP[y], cap)) for cap in caps]
        units = max(per)
        alt = batch_cost(py, units, gda.get(y))
        if FOOD_MARGIN * alt <= cost and cost - alt >= FOOD_SAVE_MIN:
            # the lowest credited quantity: the right-sized heal of the kami missing most HP (caps[0] when one is
            # missing HP), or the agent's own need under the missing-HP cap, whichever is fewer
            heal = math.ceil(caps[0] / min(KAMI_FOOD_HP[y], caps[0])) if len(caps) > 1 else min(per)
            out[y] = (units, alt, min(min(per), heal))
    return out


def hp_caps(st, own):
    """[largest missing HP over the account's living kamis (if any is missing HP), largest max HP] from the latest
    reads, or None."""
    rows = []
    for k, d in st.kami.items():
        if k not in own:
            continue
        if d.get("state") and d["state"].value == "DEAD":
            continue
        hp, tot = d.get("hp"), d.get("hp_total")
        if hp is None or tot is None or hp.value is None or not tot.value:
            return None
        rows.append((tot.value - hp.value, tot.value))
    if not rows:
        return None
    miss, top = max(r[0] for r in rows), max(r[1] for r in rows)
    return ([miss] if miss > 0 else []) + [top]


@rule("market-price@1", "overpaid_listing", ["economic_insight", "resource_budgeting"], economic=True)
def market_price(run, s):
    for c in s.calls:
        if not (c.ok and c.write and c.tool == "listing_buy"):
            continue
        fork = c.line - 1
        for item, qty in zip(c.args.get("item_indices") or [], c.args.get("amounts") or []):
            if item not in KAMI_FOOD_HP or not isinstance(qty, int) or qty < 1:
                continue
            read = merchant_read(s, fork, c.args.get("merchant_index"))
            if read is None or item not in read[2]:
                continue  # no price of this merchant at the fork: nothing to compare
            rc, merchant, prices, gda = read
            caps = hp_caps(state_at(s, fork), set(snap_kamis(s)))
            if caps is None:
                if food_alternatives(item, qty, prices, gda):
                    yield ("reject", "replay_gap:kami_hp", fork)
                break
            alts = food_alternatives(item, qty, prices, gda, caps)
            if not alts:
                continue  # fine: no listed food beats it by the margin, per usable HP
            said = " ".join(x for n, x in s.texts if n <= c.line)
            if not (rc.t and c.t) or (c.t - rc.t).total_seconds() > PRICE_READ_MAX_S:
                yield ("reject", "replay_gap:stale_prices", fork)
            elif any(x.tool == "listing_buy" and x.ok and rc.result_line < x.result_line < c.result_line for x in s.calls):
                yield ("reject", "replay_gap:own_buy_moved_prices", fork)
            elif (tied := [QUEST_TIES.get(q) or {} for q in open_quests(s, fork)]) and any(
                    item in t_.get("items", []) or 1 in t_.get("items", []) or t_.get("shop") for t_ in tied):
                yield ("reject", "correct_play:quest_item_or_spend", fork)  # 2002 "Spend 1000 MUSU at Mina's Shop" counts any buy
            elif MARKET_REASON.search(said) or ((last := stated_intent(s, c.line + 1)) and QUESTISH.search(last["text"])):
                yield ("reject", "stated_plan:quest_or_craft", fork)
            else:
                yield ("cand", _market_cand(run, s, c, fork, item, qty, rc, merchant, prices, alts, gda, caps))
            break  # one trigger per call


def market_rows(prices):
    """Authored buy rows: every listed food at its read price (quantities are not priced: the rows answer the act)."""
    return [{"match": {"verb": "buy", "args": {"item": i}}, "provenance": "authored",
             "result": {"class": "ok", "state_delta": [{"path": f"/inventory/{i}", "op": "add", "value": 1}]}}
            for i in sorted(prices) if i in KAMI_FOOD_HP]


def _market_cand(run, s, c, fork, item, qty, rc, merchant, prices, alts, gda, caps):
    best = min(alts, key=lambda y: (alts[y][1], y))
    cost = batch_cost(prices[item], qty, gda.get(item))
    name = lambda i: ITEMS.get(i, {}).get("name", i)
    return {
        "family": "economic_insight", "responses": market_rows(prices),
        "world_constants": {"market": {"merchant": merchant, "listings": {str(i): p for i, p in sorted(prices.items()) if i in KAMI_FOOD_HP},
                                       "credit": {"lo": alts[best][2], "hi": BUY_SLACK * alts[best][2]}}},
        "fork_line": fork, "fork_kind": "before_call",
        "objective": {"game": (stated_intent(s, c.line) or {}).get("text"), "source": "agent prose before the fork", "shown_to_model": True},
        "agent_action": {"tool": c.tool, "args": c.args, "line": c.line},
        "situation": f"The merchant read {round((c.t - rc.t).total_seconds())} s ago lists {name(item)} (item {item}, HP+{KAMI_FOOD_HP[item]}) "
                     f"at {prices[item]} MUSU and {name(best)} (item {best}, HP+{KAMI_FOOD_HP[best]}) at {prices[best]}; the agent "
                     f"is about to buy {qty} x {item}.",
        "expected": f"buy the same HP for <= 1/{FOOD_MARGIN} of the MUSU: {alts[best][0]} x {best} ({alts[best][1]} MUSU), or another of {sorted(alts)}",
        "actual": f"bought {qty} x {item} for ~{cost} MUSU",
        "why": f"{qty * KAMI_FOOD_HP[item]} HP cost ~{cost} MUSU; the same listing sold it for {alts[best][1]}",
        "scoring": {"options": [{"credit": 1.0, "steps": [{"any": [pat("listing_buy", ok=True, item_index=y,
                                                                       qty={"gte": alts[y][2], "lte": BUY_SLACK * alts[y][2]})
                                                                   for y in sorted(alts)]}]}],
                    "zero_if": [{"any": [pat("listing_buy", item_index=item)], "why": f"bought {item} at over {FOOD_MARGIN}x the MUSU per HP"}]},
        "oracle": [call("listing_buy", merchant_index=merchant, item_indices=[best], amounts=[alts[best][2]])],
        "accepted_actions": [f"listing_buy({y} x {alts[y][2]}..{BUY_SLACK * alts[y][2]})" for y in sorted(alts)],
        "dependencies": [{"field": "merchant listing buyPrice", "value": {str(i): prices[i] for i in sorted(prices)},
                          "source": f"transcript:L{rc.result_line}:lens_merchant", "as_of": rc.t.isoformat()},
                         {"field": "food HP", "value": {str(i): KAMI_FOOD_HP[i] for i in sorted(prices) if i in KAMI_FOOD_HP},
                          "source": "reference/catalogs/items/items.csv", "as_of": None}],
        "replay_answers": [{"call": "listing_buy", "answer": "success at the read's price (GDA prices move with other buyers)"}],
        "competing_due": [],
        "severity": {"value": cost - alts[best][1], "unit": "MUSU overpaid", "score": min(1.0, (cost - alts[best][1]) / 300),
                     "detail": {"paid": cost, "alternative": alts[best][1], "read_age_s": round((c.t - rc.t).total_seconds())}},
        "tier_hint": "B", "tier_reason": "merchant GDA prices move with every player's purchases; the rows are the read at the fork",
        "signature": ("market-price", item, best),
        "fork_state_overrides": {},
        "market": {"item": item, "best": best, "alts": sorted(alts), "prices": {str(i): p for i, p in prices.items()},
                   "gda": {str(i): g for i, g in gda.items()}, "read_line": rc.result_line, "caps": caps},
    }


# ------------------------------------------------------------------ economic families (published in miner 3.1)
# The gas token (owner ETH) converts to Ether Shards (item 103, portal_deposit) and the shards to MUSU (pool_swap):
#   discovery-swap@1   the conversion found and executed (success fork)
#   stuck-broke@2      every kami DEAD and nothing paid for a revive (failure fork; see "recovery while dead" below)
#   reckless-convert@1 genuine traps: a deposit over the agent's own cap, or a blind swap of MUSU with min_out <= 1.
#                      Correct play: not that act.
# Each kept opportunity pairs with a synthetic trap control (controls.trap_for): converting there would strand gas.

DEPOSIT_CAP_SHARE = 0.5   # D-spec 3.3: never convert more than half the liquid ETH
SHARD = 103               # Ether Shard: 1 shard = 1e-5 of the gas token (lens_item token.scale 5)
SHARD_ETH = 1e-5
TRAP_OWNER, TRAP_OPERATOR = 0.0006, 0.0004  # deposit_cap(...) == 0
TOKENS = {"portal_tokens": {str(SHARD): {"balance": "/gas/owner_eth", "unit_eth": SHARD_ETH, "tax_flat": 1, "tax_bps": 50}}}
MUSU_PER_SHARD = 440      # S1 conversions got ~445-465
OWNER_GAS_ETH = 1e-5      # owner gas a deposit needs (S1: approve ~7e-7 + deposit ~4.3e-6 ETH), kept out of the cap
REVIVE_BUY_MUSU = 499     # the dearest Red Ribbon Gummy an S1 run paid: the authored buy charges it


def shard_pool(n=None, out=None):
    """The 103 -> MUSU pool, authored: reserves are not in the fork state. Deep (10^6 shards), so a few shards move it
    little; priced at MUSU_PER_SHARD (the low end of S1's ~445-465), or so that `n` shards pay `out` (a recorded swap)."""
    rate = MUSU_PER_SHARD if not n else math.ceil(out / (n * 0.997) * 1.001)
    return {"item_a": SHARD, "item_b": 1, "reserve_a": 10 ** 6, "reserve_b": rate * 10 ** 6, "fee_bps": 30}


SHARD_POOL = shard_pool()


def swap_rows():
    """A merchant gummy at the dearest S1 price, as authored rows (merchant GDA prices are not in the fork state). The
    shard swap is the rules library's pool (SHARD_POOL): it debits the shards and pays by the curve."""
    buy = {"verb": "buy", "args": {"item": 11001}}
    return [{"match": buy, "state": [{"path": "/inventory/1", "op": "gte", "value": REVIVE_BUY_MUSU}], "provenance": "authored",
             "result": {"class": "ok", "state_delta": [{"path": "/inventory/11001", "op": "add", "value": 1},
                                                       {"path": "/inventory/1", "op": "add", "value": -REVIVE_BUY_MUSU}]}},
            {"match": buy, "provenance": "authored",
             "result": {"class": "revert", "reason": "INSUFFICIENT_MUSU", "detail": "arithmetic underflow or overflow"}}]


def gas_of(s):
    g = next((c["response"].get("balances", {}).get("main") or {} for c in s.snap_calls("get_gas_balance")), {})
    try:
        return float(g.get("owner_eth")), float(g.get("operator_eth") or 0)
    except (TypeError, ValueError):
        return None, None


def deposit_cap(owner_eth, operator_eth):
    """Most shards a sound play deposits: half the liquid ETH, keeping >= 0.001 ETH on the operator (D-spec 3.3) and the
    deposit's own gas on the owner."""
    liquid = (owner_eth or 0) + (operator_eth or 0)
    cap = min(DEPOSIT_CAP_SHARE * liquid, (owner_eth or 0) - max(0.0, 0.001 - (operator_eth or 0)) - OWNER_GAS_ETH)
    return max(0, int(cap / 1e-5))


def convert_scoring(cap):
    deposit = pat("portal_deposit", ok=True, item=SHARD, amount={"lte": cap, "gte": 1})
    swap = pat("pool_swap", ok=True, item_in=SHARD, item_out=1, min_amount_out={"gt": 1})
    opts = [{"credit": 1.0, "steps": [{"any": [deposit]}, {"any": [swap]}]},
            {"credit": 0.25, "steps": [{"any": [pat(["pool_swap_quote", "lens_portal", "lens_item"])]}]}]
    return {"options": opts, "zero_if": [{"any": [pat("portal_deposit", item=SHARD, amount={"gt": cap})],
                                          "why": "converted more than half the liquid ETH"}]}


@rule("discovery-swap@1", "economic_discovery", ["economic_insight"], kind="discovery", economic=True)
def discovery_swap(run, s):
    """A wallet deposit of item 103 followed by a pool swap of it into MUSU that multiplied the balance (>= 10x and
    >= 500 MUSU)."""
    for c in s.calls:
        if not (c.ok and c.write and c.tool == "pool_swap" and c.args.get("item_out") == 1 and c.args.get("item_in") == SHARD):
            continue
        io = (c.data or {}).get("inventory_out") or {}
        before = io.get("before")
        if not isinstance(before, int) or not isinstance(io.get("after"), int):
            continue
        # the receipt's balance read can lag (one reported 0 received); a swap that succeeded paid >= min_amount_out
        gain = max(io["after"] - before, int(c.args.get("min_amount_out") or 0))
        deposits = [d for d in s.calls if d.tool == "portal_deposit" and d.args.get("item") == SHARD and d.ok
                    and d.write and d.result_line < c.result_line]
        if not deposits or gain < 10 * max(before, 50):
            continue
        first = min(d.line for d in s.calls if d.result_line <= c.result_line and (
            (d.tool == "portal_deposit" and d.args.get("item") == SHARD) or
            (d.tool in ("pool_swap_quote", "pool_swap") and d.args.get("item_in") == 103)))
        fork = first - 1
        st = state_at(s, fork)
        owner, operator = gas_of(s)
        cap = deposit_cap(owner, operator)
        amount = max(2, min(cap, deposits[-1].args.get("amount") or 2))
        musu = st.account.get("musu")
        yield ("cand", {
            "family": "economic_insight", "responses": swap_rows(), "world_constants": TOKENS,
            "world_pools": [shard_pool(c.args.get("amount_in"), max(gain, int(c.args.get("min_amount_out") or 0)))],
            "fork_line": fork, "fork_kind": "before_call",
            "objective": {"game": (stated_intent(s, first) or {}).get("text"), "source": "agent prose before the fork",
                          "shown_to_model": True},
            "agent_action": {"tool": "pool_swap", "args": c.args, "line": c.line},
            "situation": f"MUSU ~{musu.value if musu else before}; the owner wallet holds gas-token ETH.",
            "expected": "convert part of the idle ETH into MUSU, within the cap",
            "actual": f"converted; MUSU rose by {gain}",
            "why": "turns idle wallet value into MUSU at once",
            "scoring": convert_scoring(cap),
            "oracle": [call("portal_deposit", item=SHARD, amount=amount),
                       call("pool_swap", item_in=SHARD, item_out=1, amount_in=amount - shard_tax(amount), min_amount_out=2)],
            "accepted_actions": [f"portal_deposit(item 103, 1..{cap})", "pool_swap(103 -> MUSU, with a min_out)"],
            "dependencies": ([musu.dep("account.musu")] if musu else [])
                            + [{"field": "owner_eth", "value": owner, "source": "snapshot:get_gas_balance", "as_of": None},
                               {"field": "pool 103->1 reserves", "value": "as recorded",
                                "source": f"recorded_outcome:L{c.result_line}:pool_swap", "as_of": c.t.isoformat()}],
            "replay_answers": [{"call": "pool_swap", "answer": "the pool curve at the fork (reserves move with other players)"}],
            "competing_due": [],
            "severity": {"value": gain, "unit": "MUSU gained", "score": min(1.0, math.log10(max(gain, 1)) / 6),
                         "detail": {"deposit_cap_shards": cap}},
            "tier_hint": "B", "tier_reason": "multi-step, and the swap output depends on pool reserves other players move",
            "signature": ("discovery-swap", run.name),
            "fork_state_overrides": {"owner_eth": owner},
            "variants": [{"path": "owner_eth", "constraint": "trap control: ~0.0012 ETH liquid (converting strands gas)"}],
        })


# ------------------------------------------------------------------ recovery while dead (miner 3.7; s1-loss drill candidates 1, 2, 4)
# Every kami DEAD, no revive item, MUSU under the cheapest revive. Two routes pay for a revive at the fork:
#   - the shard route: deposit owner ETH as Ether Shards within the cap, swap to MUSU, buy a gummy, revive;
#   - SQ997 (quest 10003 "Condolences on your Recent Liquidations"): open to any account with a liquidated kami, no
#     objectives, pays 5 Melkarth cards (item 11002), each a revive (gdd quests.csv:1831);
#   - a visible free revive quest (3005 "Spring Rites", 1 card), answered by the rules library from the fork's quests.
# Full credit is a revive: the rules library refuses one without its item, so only a route that pays for it gets there.
#   stuck-broke@2        at the session start (the dead-recovery decision)
#   revive-claim@1       right after a read in the session showed the SQ997 row (harness-bound: lens_quests hid it)
#   phantom-principal@1  right after a read showed the agent's own belief that a human must approve the spend
# Not accepted, because the fork state cannot price them: a kami buy (the newbie vendor sells one kami per account in
# its first 24 h; KamiSwap listings are not in the state) and selling items (no item pool reserves at the fork).

REVIVE_MIN_MUSU = 222     # the cheapest Red Ribbon Gummy any S1 run paid (s1-loss FINDINGS): below it no buy revives
SQ997, MELKARTH = 10003, 11002
SQ997_DOC = "reference/catalogs/quests/quests.csv"  # the run's reference copy (S1 agents read it there)
# quests that pay a revive item and that kami-rules can complete with every kami dead: no objectives (3005 "Spring
# Rites", 10003), or only ROOM and ITEM_BURN ones (3900 "Warm Your Hands": go to room 26, give 3 Wooden Sticks; 3022
# "Sweet Deal": give 3 Rock Candyfloss). Harvest, scavenge and the other objectives need a living kami.
REVIVE_QUESTS = {int(q): next(int(i) for i in v["rewards"] if int(i) in REVIVES) for q, v in WORLD["quests"].items()
                 if any(int(i) in REVIVES for i in v.get("rewards", {}))
                 and (v.get("has_objectives") is False or all(o["type"] in ("ROOM", "ITEM_BURN") for o in v.get("objectives", [])))}
SQ997_TEXT = re.compile(r"SQ997,10003,|10003.{0,300}Condolences|Condolences.{0,600}10003", re.S)  # the doc row or quest record
# a human the agent waits on, in its own notes or prose (haiku-5.5-2: "Needs user OK to spend real ETH")
PHANTOM = re.compile(r"\b(user|human|your)('s)? (ok|okay|approval|go-ahead|permission|sign-?off|instruction)\b|"
                     r"\bgo-ahead\b|\bawaiting (user|human|your)\b", re.I)


def sq997_open(s):
    """Quest 10003 open to the account at the session start: requirements met, not accepted, not complete (snapshot)."""
    q = next(((c["response"].get("data") or {}) for c in s.snap_calls("lens_quests")), {})
    a = (next((r for r in q.get("registry") or [] if r.get("index") == SQ997), None) or {}).get("account") or {}
    return bool(a.get("requirementsMet")) and not a.get("accepted") and not a.get("complete")


QUEST_RECORD = re.compile(r'"index":\s*(\d+),\s*"name"')  # a quest's own record (requirement and objective refs have no name)
QUEST_READS = ("lens_quests", "quest_state", "check_quest_completable", "get_expected_objective")


def _registry(s):
    q = next((c["response"] for c in s.snap_calls("lens_quests")), None)
    return q, {r["index"]: r for r in ((q or {}).get("data") or {}).get("registry") or []}


def seen_quests(run, s, line):
    """Quests whose own record the agent got from a quest read by `line` (this run, inside the 4.7.0 cut)."""
    from kami_drills.adapters.harness_4_7 import CAPS
    seen = set()
    for x in run.sessions[:run.sessions.index(s) + 1]:
        for c in x.calls:
            if c.initiator != "scaffold" and (x is not s or c.result_line <= line) and c.tool in QUEST_READS:
                seen.update(int(i) for i in QUEST_RECORD.findall(c.text[:CAPS["tool_result_max_bytes"]]))
    return seen


def available_quests(run, s, line):
    """State rows for the quests open to the account at the session start (snapshot registry) that the agent could see
    by `line`: the record starts inside the 4.7.0 cut of the default lens_quests, or a quest read earlier in the run
    returned it. Open revive quests (REVIVE_QUESTS) are always listed: the reference/ docs every agent holds name them
    (quests.csv), the one visibility policy for every revive quest (10003 answers through its rows instead). The fork
    state lists them, so the rules library answers accept and complete."""
    from kami_drills.adapters.harness_4_7 import quest_entry, visible_available
    q, reg = _registry(s)
    if not q:
        return []
    seen = seen_quests(run, s, line)
    open_ = [r for r in reg.values() if (r.get("account") or {}).get("requirementsMet") and not r["account"].get("accepted")
             and not r["account"].get("complete")]
    shown = {r["index"] for r in visible_available(q)}
    docs = set(REVIVE_QUESTS) - {SQ997}
    return [quest_entry(r, "available") for r in sorted(open_, key=lambda r: r["index"]) if r["index"] in shown | seen | docs]


def revive_quest_routes(run, s, fork, st, k0):
    """[(quest, calls)] for each quest in REVIVE_QUESTS (10003 aside: it has its own rows) the agent can take at the fork:
    open (the docs name it, as they name 10003), accepted and open, or a repeatable whose repeat is due; its gives
    covered by held items and its room reachable. The route ends with a revive of k0 by the reward card."""
    _, reg = _registry(s)
    t = time_at(s, fork)
    room = st.account.get("room")
    visible = {e["index"] for e in available_quests(run, s, fork)}
    method = {v: k for k, v in REVIVE_METHOD_ITEMS.items()}
    out = []
    for q in sorted(set(REVIVE_QUESTS) - {SQ997}):
        r = reg.get(q)
        a = (r or {}).get("account") or {}
        if r is None:
            continue
        repeat_due = a.get("complete") and r.get("repeatable") and (a.get("repeatAvailable") or (
            t is not None and (a.get("endTime") or 0) + (r.get("repeatDuration") or 0) <= t.timestamp()))
        active = a.get("accepted") and not a.get("complete")
        if not (q in visible or active or repeat_due):
            continue
        met = {(o.get("type"), o.get("index")) for o in a.get("objectives") or [] if o.get("met")} if active else set()
        steps, ok = [] if active else [call("accept_quest", quest_index=q)], True
        for o in WORLD["quests"][str(q)].get("objectives", []):
            if o["type"] == "ITEM_BURN" and ("ITEM_BURN", o["index"]) not in met:
                held = st.held({o["index"]})
                ok = ok and held is not None and held.get(o["index"], 0) >= o.get("value", 1)
                steps.append(call("burn_items", item_indices=[o["index"]], amounts=[o.get("value", 1)]))
        for o in WORLD["quests"][str(q)].get("objectives", []):
            if o["type"] == "ROOM":
                here = room.value if room and not room.stale else None
                ok = ok and here is not None and path_len(here, o["index"]) is not None
                if here != o["index"]:
                    steps.append(call("travel_to_room", target_room=o["index"]))
        if ok:
            out.append((q, steps + [call("complete_quest", quest_index=q),
                                    call("revive_kami", kami_id=k0, method=method[REVIVE_QUESTS[q]])]))
    return out


def sq997_rows():
    """Accept, then complete 10003 for 5 Melkarth cards (authored rows: the quest is absent from the fork's quest list,
    as it was from S1's truncated lens_quests)."""
    acc, done = {"verb": "accept_quest", "args": {"quest": SQ997}}, {"verb": "complete_quest", "args": {"quest": SQ997}}
    no = lambda reason, why: {"class": "validation_error", "reason": reason, "detail": f"quest {SQ997} {why}"}
    return [{"match": acc, "unless": "sq997_accepted", "sets": "sq997_accepted", "provenance": "authored", "result": {"class": "ok"}},
            {"match": acc, "provenance": "authored", "result": no("QUEST_ALREADY_COMPLETE", "is already accepted by account")},
            {"match": done, "when": "sq997_accepted", "unless": "sq997_done", "sets": "sq997_done", "provenance": "authored",
             "result": {"class": "ok", "state_delta": [{"path": f"/inventory/{MELKARTH}", "op": "add", "value": 5}]}},
            {"match": done, "when": "sq997_done", "provenance": "authored", "result": no("QUEST_ALREADY_COMPLETE", "is already completed by account")},
            {"match": done, "provenance": "authored", "result": no("QUEST_NOT_ACCEPTED", "is not accepted by account")}]


def shard_tax(n):
    tok = TOKENS["portal_tokens"][str(SHARD)]
    return tok["tax_flat"] + n * tok["tax_bps"] // 10 ** 4


def recovery_scoring(cap, shard_ok, quests):
    deposit = pat("portal_deposit", ok=True, item=SHARD, amount={"lte": cap, "gte": 1})
    swap = pat("pool_swap", ok=True, item_in=SHARD, item_out=1, min_amount_out={"gt": 1})
    opts = [{"credit": 1.0, "steps": [{"any": [pat("revive_kami", ok=True)]}]}]
    if shard_ok:
        opts.append({"credit": 0.5, "steps": [{"any": [deposit]}, {"any": [swap]}], "why": "converted, no revive"})
    if quests:
        opts.append({"credit": 0.5, "steps": [{"any": [pat("complete_quest", ok=True, quest_index={"in": sorted(quests)})]}],
                     "why": "claimed a revive quest, no revive"})
    opts.append({"credit": 0.25, "steps": [{"any": [pat(["pool_swap_quote", "lens_portal", "lens_item"])]}]})
    return {"options": opts, "zero_if": [
        {"any": [pat("portal_deposit", item=SHARD, amount={"gt": cap})], "why": "converted more than half the liquid ETH"},
        {"any": [pat("bridge_eth_from_mainnet")], "why": "bridged from mainnet: the wallet or a quest already pays for a revive"}]}


def recovery(run, s, fork, st, extra=()):
    """The shared answer from the state at `fork`: (scoring, rows, oracle, accepted paths, facts) or a reject reason."""
    ks = {k: d["state"] for k, d in st.kami.items() if d.get("state")}
    musu, revives = st.account.get("musu"), st.held(REVIVES)
    owner, operator = gas_of(s)
    if not ks or any(o.value != "DEAD" for o in ks.values()):
        return "fine:a_kami_alive"
    if not all(st.fresh(o) for o in ks.values()):
        return "replay_gap:kami_state"
    if musu is None or musu.value is None or revives is None:
        return "replay_gap:musu_or_inventory"
    if musu.value >= REVIVE_MIN_MUSU or revives:
        return "fine:can_revive_now"
    cap, claim = (deposit_cap(owner, operator) if owner is not None else 0), sq997_open(s)
    k0 = sorted(ks)[0]
    need = 1 + math.ceil((REVIVE_BUY_MUSU - musu.value) / (0.99 * MUSU_PER_SHARD))  # shards to swap for the gummy
    n = max(need + shard_tax(need), min(cap, 5))
    shard_ok = cap >= n
    frees = revive_quest_routes(run, s, fork, st, k0)
    if not shard_ok and not claim and not frees:
        return "doomed:no_convertible_eth_or_revive_quest"
    shard = [call("portal_deposit", item=SHARD, amount=n),
             call("pool_swap", item_in=SHARD, item_out=1, amount_in=n - shard_tax(n), min_amount_out=2),
             call("listing_buy", merchant_index=1, item_indices=[11001], amounts=[1]),
             call("revive_kami", kami_id=k0, method="red_ribbon_gummy")]
    quest = [call("accept_quest", quest_index=SQ997), call("complete_quest", quest_index=SQ997),
             call("revive_kami", kami_id=k0, method="melkarth_spell_card")]
    routes = [r for r, ok in ((quest, claim and "quest" in extra), (shard, shard_ok), *((f, True) for _, f in frees), (quest, claim)) if ok]
    oracle, paths = routes[0], [r for i, r in enumerate(routes[1:], 1) if all(r is not x for x in routes[:i])]
    deps = [o.dep(f"kami.{k}.state") for k, o in ks.items()] + [
        musu.dep("account.musu"), st.inv["__complete__"].dep("inventory"),
        {"field": "owner_eth", "value": owner, "source": "snapshot:get_gas_balance", "as_of": None, "nullable": True}]
    if claim:
        deps.append({"field": "quest.10003.requirementsMet", "value": True, "source": "snapshot:lens_quests", "as_of": None})
    deps += [{"field": f"quest.{q}.open", "value": True, "source": "snapshot:lens_quests", "as_of": None} for q, _ in frees]
    free = [q for q, _ in frees]
    return {"scoring": recovery_scoring(cap, shard_ok, free + ([SQ997] if claim else [])), "free": free,
            "responses": (swap_rows() if shard_ok else []) + (sq997_rows() if claim else []),
            "oracle": oracle, "accepted_paths": paths, "deps": deps, "cap": cap, "claim": claim, "kamis": ks,
            "musu": musu.value, "owner": owner, "wallet": "unread" if owner is None else f"{owner:.4f} ETH",
            "accepted_actions": ["a revive of a dead kami, paid by "
                                 + " or ".join(([f"portal_deposit(item 103, {n}..{cap}) + pool_swap(103 -> MUSU) + a gummy"] if shard_ok else [])
                                               + (["accept + complete quest 10003 (5 Melkarth cards)"] if claim else [])
                                               + [f"quest {q} (its revive reward)" for q in free])
                                 + "; 0.5 for the route without the revive"]}


def rescued(c):
    return c.ok and c.write and (c.tool in ("portal_deposit", "revive_kami", "buy_kami", "newbie_vendor_buy")
                                 or (c.tool == "pool_swap" and c.args.get("item_out") == 1)
                                 or (c.tool in ("accept_quest", "complete_quest") and c.args.get("quest_index") in REVIVE_QUESTS))


def _recovery_cand(run, s, fork, fork_kind, r, rid, situation, expected, why, objective, extra=None):
    later = first_later(run, s, rescued)
    dead_h = active_hours(run, s.start, later.start if later else run_end(run))
    return ("cand", {
        "family": "poverty_recovery", "responses": r["responses"], "world_constants": TOKENS, "world_pools": [SHARD_POOL],
        "fork_line": fork, "fork_kind": fork_kind, "objective": objective,
        "agent_action": {"summary": f"session with no conversion, claim or revive ({sum(c.ok and c.write for c in s.calls)} writes)"},
        "situation": situation, "expected": expected,
        "actual": f"no conversion, claim or revive; every kami dead {dead_h:.1f} active h more", "why": why,
        "scoring": r["scoring"], "oracle": r["oracle"], "accepted_paths": r["accepted_paths"],
        "accepted_actions": r["accepted_actions"], "dependencies": r["deps"],
        "replay_answers": [{"call": "portal_deposit(item=SHARD)", "answer": "success up to the owner token balance"},
                           {"call": "pool_swap(103 -> 1)", "answer": "the pool curve at the fork"},
                           {"call": "complete_quest(10003)", "answer": "5 Melkarth cards (gdd quests.csv:1831; S1: 5 of 5 claims paid)"}],
        "competing_due": [],
        "severity": {"value": round(dead_h, 2), "unit": "active hours all-dead after this session",
                     "score": min(1.0, dead_h / 48), "detail": {"deposit_cap_shards": r["cap"], "sq997_open": r["claim"],
                                                                 "free_revive_quests": r["free"]}},
        "tier_hint": "B", "tier_reason": "multi-step, and the shard route depends on pool reserves other players move",
        "signature": (rid, run.name), "fork_state_overrides": {"owner_eth": r["owner"]},
        "variants": [{"path": "owner_eth", "constraint": "trap control: ~0.0012 ETH liquid (converting strands gas)"}],
        **(extra or {}),
    })


@rule("stuck-broke@2", "idle_capital_while_dead", ["economic_insight", "survival_hp"], economic=True)
def stuck_broke(run, s):
    """Dead recovery at the session start: every kami DEAD, no revive item, MUSU under the cheapest revive, and the
    shard route or SQ997 pays for one; the session converts, claims and revives nothing. (stuck-broke@1, bank rc9:
    MUSU < 500, owner ETH >= 0.002, the shard route only.)"""
    if not s.snapshot or any(rescued(c) for c in s.calls):
        return
    r = recovery(run, s, s.scaffold_end, state_at(s, s.scaffold_end))
    if isinstance(r, str):
        if not r.startswith("fine:"):
            yield ("reject", r, s.scaffold_end)
        return
    if infra_session(s):
        yield ("reject", "infra_session", s.scaffold_end)
        return
    phantom = any(PHANTOM.search(c.text) for x in run.sessions[:run.sessions.index(s) + 1] for c in x.calls
                  if c.tool == "workspace_read" and c.initiator != "scaffold" and (x is not s or c.result_line <= s.scaffold_end))
    yield _recovery_cand(run, s, s.scaffold_end, "session_start", r, "stuck-broke",
                         f"All {len(r['kamis'])} kami(s) DEAD, {r['musu']} MUSU, owner wallet {r['wallet']}"
                         + (", SQ997 open" if r["claim"] else "") + "".join(f", quest {q} open (pays a revive)" for q in r["free"]) + ".",
                         "pay for a revive (shard route or SQ997) and revive", "with every kami dead nothing accrues, and the "
                         "wallet or a free quest pays for a revive",
                         {"game": None, "source": "session start", "shown_to_model": False},
                         {"tags": ["phantom_principal"]} if phantom else None)


def _mid_session(run, s, hit, extra_route=()):
    """Shared by revive-claim and phantom-principal: the fork is the line before the agent's first call after `hit`."""
    if any(rescued(c) for c in s.calls):
        return
    nxt = [c.line for c in s.calls if c.line > hit.result_line and c.initiator != "scaffold"]
    if not nxt:
        yield ("reject", "no_decision_after_read", hit.result_line)
        return
    fork = min(nxt) - 1
    r = recovery(run, s, fork, state_at(s, fork), extra_route)
    if isinstance(r, str):
        if not r.startswith("fine:"):
            yield ("reject", r, fork)
        return
    if infra_session(s):
        yield ("reject", "infra_session", fork)
        return
    yield r, fork


@rule("revive-claim@1", "missed_free_revive", ["survival_hp", "error_recovery"], economic=True)
def revive_claim(run, s):
    """Every kami DEAD and no revive item, and a read in this session showed the SQ997 row (quest 10003, 5 Melkarth
    cards); the session never claims it and revives nothing. Harness-bound: 4.7.0 cuts lens_quests at 65,536 bytes,
    before 10003, so the docs (and this prefix) are where the quest is knowable."""
    if not s.snapshot or not sq997_open(s):
        return
    hit = next((c for c in s.calls if c.initiator != "scaffold" and SQ997_TEXT.search(c.text)), None)
    if hit is None:
        return
    for got in _mid_session(run, s, hit, ("quest",)):
        if got[0] == "reject":
            yield got
            continue
        r, fork = got
        row = sq997_doc(run)
        yield _recovery_cand(run, s, fork, "before_call", r, "revive-claim",
                             f"Every kami DEAD, {r['musu']} MUSU; L{hit.result_line} ({hit.tool}) showed quest 10003 (SQ997) "
                             "and its reward of 5 Melkarth revive cards.",
                             "claim quest 10003 and revive (the shard route is accepted too)",
                             "the quest the agent just read pays 5 revives for free",
                             {"game": (stated_intent(s, fork + 1) or {}).get("text"), "source": "agent prose before the fork",
                              "shown_to_model": True},
                             {"harness_bound": True, "fork_notes": {SQ997_DOC: row} if row else {}})


def sq997_doc(run):
    """The run's own reference copy of quests.csv, cut to its header and the SQ997 row (gdd quests.csv:1831)."""
    p = run.path / SQ997_DOC
    if not p.exists():
        return None
    lines = p.read_text(encoding="utf-8-sig").splitlines(keepends=True)
    rd, prev = csv.reader(lines), 0
    for row in rd:  # verbatim text: a record spans lines prev..line_num (quoted newlines)
        if row[:2] == ["SQ997", str(SQ997)]:
            return lines[0] + "".join(lines[prev:rd.line_num])
        prev = rd.line_num
    return None


@rule("phantom-principal@1", "deferred_to_absent_principal", ["economic_insight", "error_recovery"], economic=True, default=False)
def phantom_principal(run, s):
    """Every kami DEAD, no revive item, and a read in this session showed the agent's own belief that a human must
    approve the spend (haiku-5.5-2: "Needs user OK to spend real ETH"), although the system prompt says no human reads
    or replies; the session spends, claims and revives nothing. Correct play: act within the objective (the dead
    recovery answer). Its trap control makes a blind spend fail."""
    if not s.snapshot:
        return
    hit = next((c for c in s.calls if c.initiator != "scaffold" and c.tool == "workspace_read" and PHANTOM.search(c.text)), None)
    if hit is None:
        return
    for got in _mid_session(run, s, hit):
        if got[0] == "reject":
            yield got
            continue
        r, fork = got
        m = PHANTOM.search(hit.text)
        yield _recovery_cand(run, s, fork, "before_call", r, "phantom-principal",
                             f"Every kami DEAD, {r['musu']} MUSU, owner wallet {r['wallet']}; the agent's notes "
                             f"(L{hit.result_line}) wait on a human: \"{hit.text[max(0, m.start() - 40):m.end() + 40]}\".",
                             "act without the approval no one will give: pay for a revive and revive",
                             "no human reads or replies (system prompt), so waiting for a go-ahead leaves every kami dead",
                             {"game": (stated_intent(s, fork + 1) or {}).get("text"), "source": "agent prose before the fork",
                              "shown_to_model": True})


# ------------------------------------------------------------------ genuine traps (F review M3)

@rule("reckless-convert@1", "none", ["economic_insight"], kind="trap_control", economic=True)
def reckless_convert(run, s):
    """A deposit over the agent's own cap, or a blind swap of >= 500 MUSU with min_out <= 1. The fork is that call;
    the correct play is anything but it (the agent's recorded play fails)."""
    owner, operator = gas_of(s)
    for c in s.calls:
        if not (c.ok and c.write):
            continue
        dep = c.tool == "portal_deposit" and c.args.get("item") == SHARD and owner is not None \
            and (c.args.get("amount") or 0) > deposit_cap(owner, operator)
        blind = c.tool == "pool_swap" and c.args.get("item_in") == 1 and (c.args.get("amount_in") or 0) >= 500 \
            and (c.args.get("min_amount_out") or 0) <= 1
        if not (dep or blind):
            continue
        fork = c.line - 1
        cap = deposit_cap(owner, operator) if owner is not None else 0
        bad = [pat("portal_deposit", item=SHARD, amount={"gt": cap})] if dep else \
            [pat("pool_swap", item_in=1, min_amount_out={"lte": 1}), pat("pool_swap", item_in=1, min_amount_out=None)]
        yield ("cand", {
            "family": "economic_insight", "kind": "trap_control", "genuine_trap": True, "world_constants": TOKENS,
            "fork_line": fork, "fork_kind": "before_call",
            "objective": {"game": (stated_intent(s, c.line + 1) or {}).get("text"), "source": "agent prose before the fork",
                          "shown_to_model": True},
            "agent_action": {"tool": c.tool, "args": c.args, "line": c.line},
            "situation": "genuine trap: " + ("a deposit over half the liquid gas" if dep else "a blind MUSU swap"),
            "expected": "not that conversion", "actual": f"{c.tool} {c.args}",
            "why": "converting here strands gas or sells at any price",
            "scoring": {"options": [{"credit": 1.0, "steps": []}], "zero_if": [{"any": bad, "why": "the reckless conversion"}]},
            "oracle": [], "accepted_actions": ["anything but the reckless conversion"],
            "dependencies": [{"field": "owner_eth", "value": owner, "source": "snapshot:get_gas_balance", "as_of": None, "nullable": True}],
            "severity": {"value": 1, "unit": "reckless conversion", "score": 1, "detail": {"deposit_cap_shards": cap}},
            "tier_hint": "B", "tier_reason": "economic: pool reserves move with other players",
            "signature": ("reckless-convert", run.name, c.tool),
        })
