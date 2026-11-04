"""Miner v2 behaviour checks on small synthetic runs (no real data, no secrets): uv run pytest -q tests/test_miner.py"""
import json
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

from kami_drills import spec as sp
from kami_drills.miner import bank, split
from kami_drills.miner import pipeline as P
from kami_drills.miner import rules as R
from kami_drills.miner.stats import econ_pairs, render
from kami_drills.miner.trace import load_run, state_at
from kami_drills.spec import check as CK
from kami_drills.spec import perturb

KEY = b"test-split-key-not-a-secret"

T0 = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
COOL = ("Error executing tool {tool}: validation failed; no transaction sent: transaction dry-run reverted: "
        "{{'code': -32000, 'message': 'revert: kami on cooldown: Reverted'}}")
GUM = {"balance": 2, "item": {"index": 11301, "name": "Gum", "type": "FOOD"}}            # kami food, HP+25
ICE = {"balance": 3, "item": {"index": 21204, "name": "Spell card", "type": "FOOD"}}     # account (stamina) food
MUSU = lambda n: {"balance": n, "item": {"index": 1, "name": "MUSU", "type": "MISC"}}


def kami(idx=7, state="HARVESTING", hp=50, total=100, rate="-5.00", cd=0, ready=False, node=3, level=5):
    return {"index": idx, "state": state, "hp": {"current": hp, "total": total, "percent": 100 * hp // total},
            "hpRatePerHr": rate, "cooldownUntil": cd, "levelUpReady": ready, "skillPoints": 0, "level": level,
            "node": {"index": node} if state == "HARVESTING" else None}


def snapshot(n, start, kamis=(), musu=500, room=3, completable=(), eth="0.0001", op_eth="0.004", items=(GUM,), accepted=(),
             notes=None):
    calls = [{"name": "workspace_read", "args": {"path": "notes.md"}, "response": {"content": notes}}] if notes else []
    calls += [
        {"name": "lens_party", "args": {"full": True}, "response": {"data": {"kamis": list(kamis)}}},
        {"name": "lens_account", "args": {}, "response": {"data": {"roomIndex": room, "musu": musu}}},
        {"name": "lens_inventory", "args": {}, "response": {"data": {"items": [MUSU(musu), *items]}}},
        {"name": "get_gas_balance", "args": {}, "response": {"balances": {"main": {"owner_eth": eth, "operator_eth": op_eth}},
                                                             "block": 100}},
    ] + [{"name": "check_quest_completable", "args": {"quest_index": q}, "response": {"quest_index": q, "completable": True}}
         for q in completable] + [
        {"name": "lens_quests", "args": {}, "response": {"data": {"accepted": [{"index": q, "complete": False} for q in
                                                                               sorted(set(accepted) | set(completable))], "registry": []}}}]
    return {"session": n, "started_at": start.isoformat(), "block": 100 + n, "complete": True, "calls": calls}


class Builder:
    """Writes a run directory: config.yaml, telemetry.jsonl, transcripts/, snapshots/."""

    def __init__(self, root, name="s1-testmodel-1", model="lab/testmodel"):
        self.dir = root / name
        (self.dir / "transcripts").mkdir(parents=True)
        (self.dir / "snapshots").mkdir()
        (self.dir / "config.yaml").write_text(f"run_id: kamiarena-{name}\nmodel: {model}\npins:\n  agent_sha: aaa\n  harness_sha: bbb\n")
        self.tel = []

    def session(self, n, start, snap, steps, end="agent", wake_min=60, drop_tel=0):
        """steps: (tool, args, result_dict_or_error_str, ok, seconds_after_start[, prose])."""
        lines = [{"role": "user", "text": "Session start."}]
        if notes := next((c["response"]["content"] for c in snap["calls"] if c["name"] == "workspace_read"), None):
            lines.append({"role": "assistant", "text": None, "initiator": "scaffold", "tool_calls": [{"id": "n0", "name": "workspace_read", "args": {"path": "notes.md"}}]})
            lines.append({"role": "tool_result", "tool_call_id": "n0", "content": notes, "initiator": "scaffold"})
            self.tel.append({"session": n, "event": "tool_call", "ts": start.isoformat()})
        self.tel.append({"session": n, "event": "session_start", "ts": start.isoformat()})
        for i, (tool, args, res, ok, sec, *say) in enumerate(steps):
            lines.append({"role": "assistant", "text": say[0] if say else None,
                          "tool_calls": [{"id": f"c{i}", "name": tool, "args": args}]})
            lines.append({"role": "tool_result", "tool_call_id": f"c{i}",
                          "content": res if isinstance(res, str) else json.dumps(res), "is_error": not ok})
            if i >= drop_tel:
                self.tel.append({"session": n, "event": "tool_call", "ts": (start + timedelta(seconds=sec)).isoformat()})
        last = start + timedelta(seconds=(steps[-1][4] if steps else 0) + 1)
        self.tel += [{"session": n, "event": "session_end", "reason": end, "ts": last.isoformat()},
                     {"session": n, "event": "schedule_next", "source": "agent", "ts": last.isoformat(),
                      "next_wake_at": (last + timedelta(minutes=wake_min)).isoformat()}]
        (self.dir / "transcripts" / f"session-{n:04d}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
        (self.dir / "snapshots" / f"session-{n:04d}.json").write_text(json.dumps(snap))
        return self

    def done(self):
        (self.dir / "telemetry.jsonl").write_text("".join(json.dumps(x) + "\n" for x in self.tel))
        return self.dir


def END(sec, minutes=60):
    return [("set_next_wake", {"minutes_from_now": minutes}, {"ok": True}, True, sec),
            ("end_session", {"reason": "done"}, {"ok": True}, True, sec + 1)]


def findings(run_dir, rule_id, n=1):
    run = load_run(run_dir)
    s = next(x for x in run.sessions if x.n == n)
    return run, s, list(R.RULES[rule_id]["fn"](run, s))


def gated(run_dir, rule_id, n=1):
    """(candidate or None, reason) for the first trigger of rule_id in session n, after every pipeline gate."""
    run, s, f = findings(run_dir, rule_id, n)
    raw = next((x[1] for x in f if x[0] == "cand"), None)
    if raw is None:
        return None, [x[1] for x in f]
    return P.gate(R.RULES[rule_id], run, s, raw)


def calls(*pairs):
    return [{"tool": t, "args": a} for t, a in pairs]


def cooldown_run(root, refusals=3, prose="Stopping the harvest on kami 7 to bank the MUSU.", cd_s=150, tool="harvest_stop"):
    cd = int((T0 + timedelta(seconds=cd_s)).timestamp())
    args = {"kami_ids": [7]} if tool != "feed_kami" else {"kami_id": 7, "food_item_id": 11301}
    steps = [("lens_kami", {"kami_index": 7}, {"data": kami(cd=cd)}, True, 10, prose)]
    steps += [(tool, args, COOL.format(tool=tool), False, 20 + i) for i in range(refusals)]
    return Builder(root).session(1, T0, snapshot(1, T0, [kami()]), steps + END(40)).done()




def play(cand, *pairs):
    """Score tool calls on a gated candidate's spec through the 4.7.0 adapter: the one scorer a model gets."""
    from kami_drills.adapters import harness_4_7 as h
    return h.play_tools(cand["spec"], list(pairs)).result()


def quest_session(b, n, start, kamis, completable=(), accepted=(), steps=None, **kw):
    return b.session(n, start, snapshot(n, start, kamis, completable=completable, accepted=accepted, **kw), steps or END(10))


# ------------------------------------------------------------------ free errors, intent, re-collects (B2)

def test_a_first_free_refusal_is_not_a_mistake_but_one_repeat_after_being_told_is(tmp_path):
    _, _, f = findings(cooldown_run(tmp_path, refusals=1), "cooldown-repeat@1")
    assert [x[1] for x in f] == ["free_error:first_try"]
    cand, why = gated(cooldown_run(tmp_path / "b", refusals=2), "cooldown-repeat@1")  # told once, repeated once
    assert why is None and cand["decision_point"]["fork_line"] == 5  # forks at the repeat; the first refusal is in the prefix


def test_a_repeat_in_the_same_turn_was_not_told(tmp_path):
    cd = int((T0 + timedelta(seconds=150)).timestamp())
    b = Builder(tmp_path)
    steps = [("lens_kami", {"kami_index": 7}, {"data": kami(cd=cd)}, True, 10, "Stopping kami 7 now.")]
    b.session(1, T0, snapshot(1, T0, [kami()]), steps + END(40)).done()
    lines = [json.loads(x) for x in (b.dir / "transcripts" / "session-0001.jsonl").read_text().splitlines()]
    call = {"role": "assistant", "text": None, "tool_calls": [{"id": f"p{i}", "name": "harvest_stop", "args": {"kami_ids": [7]}} for i in range(2)]}
    res = [{"role": "tool_result", "tool_call_id": f"p{i}", "content": COOL.format(tool="harvest_stop"), "is_error": True} for i in range(2)]
    (b.dir / "transcripts" / "session-0001.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines[:3] + [call] + res + lines[3:]))
    b.tel[1:1] = [{"session": 1, "event": "tool_call", "ts": (T0 + timedelta(seconds=20 + i)).isoformat()} for i in range(2)]
    b.done()
    assert [x[1] for x in findings(b.dir, "cooldown-repeat@1")[2]] == ["free_error:first_try"]


def test_repeat_needs_the_agents_own_stated_objective(tmp_path):
    cand, why = gated(cooldown_run(tmp_path, refusals=3), "cooldown-repeat@1")
    assert why is None
    assert cand["objective"] == {"game": "Stopping the harvest on kami 7 to bank the MUSU.", "source": "transcript:L2",
                                 "shown_to_model": True}
    _, _, f = findings(cooldown_run(tmp_path / "b", prose="Quest 10 done. Try 11."), "cooldown-repeat@1")
    assert [x[1] for x in f] == ["no_stated_intent"]
    # "hp" or "harvest" alone no longer counts as naming the action (F review m4)
    _, _, f = findings(cooldown_run(tmp_path / "c", prose="HP is fine, harvest looks good.", tool="feed_kami"), "cooldown-repeat@1")
    assert [x[1] for x in f] == ["no_stated_intent"]


def test_recollect_is_dropped(tmp_path):
    cd = int((T0 + timedelta(seconds=200)).timestamp())
    steps = [("harvest_collect", {"kami_ids": [7]}, {"status": "success", "cooldowns": [{"kami_id": 7, "cooldown_until": cd}]},
              True, 10, "Collecting kami 7 again to grab the MUSU.")]
    steps += [("harvest_collect", {"kami_ids": [7]}, COOL.format(tool="harvest_collect"), False, 20 + i) for i in range(3)]
    d = Builder(tmp_path).session(1, T0, snapshot(1, T0, [kami()]), steps + END(40)).done()
    assert [x[1] for x in findings(d, "cooldown-repeat@1")[2]] == ["pointless:recollect"]


# ------------------------------------------------------------------ one scorer: fair cooldown answers (M1, F M2)

def test_cooldown_scoring_credits_any_wait_plan_through_the_one_scorer(tmp_path):
    cand, _ = gated(cooldown_run(tmp_path), "cooldown-repeat@1")
    assert cand["spec"]["world"]["rules_library"].startswith("kami-rules@")
    need = cand["oracle"][0][1]["seconds"]
    assert need == 130 + R.FULL_FROM_S  # 150 s cooldown, fork at the first refusal (t+20), full credit past the S1 band
    act = ("harvest_stop", {"kami_ids": [7]})
    half = need // 2 + 3
    assert play(cand, ("wait", {"seconds": half}), ("lens_kami", {"kami_index": 7}), ("wait", {"seconds": half}), act)["pass"]
    assert play(cand, ("wait", {"seconds": need - 20}), *[("lens_party", {})] * 10, act)["pass"]  # useful reads count (2 s each)
    assert play(cand, ("set_next_wake", {"minutes_from_now": 5}))["score"] == 0.5  # a short wake: partial
    assert play(cand, act)["score"] == 0.0  # acting on the visible cooldown
    assert play(cand, ("wait", {"seconds": 300}), ("wait", {"seconds": 300}), act)["score"] == 0.6  # far too late
    assert play(cand, ("harvest_stop", {"kami_ids": [7], "dry_run": True}), ("wait", {"seconds": need}), act)["pass"]  # a probe is free
    assert play(cand, ("wait", {"seconds": 130 + R.ACCEPT_FROM_S + 2}), act)["score"] == 0.8  # in the band: accepted, capped


def test_cooldown_batch_needs_every_kami(tmp_path):
    cd = int((T0 + timedelta(seconds=150)).timestamp())
    steps = [("lens_party", {}, {"data": {"kamis": [kami(cd=cd), kami(8, cd=cd)]}}, True, 10, "Stopping both harvests now.")]
    steps += [("harvest_stop", {"kami_ids": [7, 8]}, COOL.format(tool="harvest_stop"), False, 20 + i) for i in range(2)]
    d = Builder(tmp_path).session(1, T0, snapshot(1, T0, [kami(), kami(8)]), steps + END(40)).done()
    cand, why = gated(d, "cooldown-repeat@1")
    assert why is None
    wait = ("wait", {"seconds": cand["oracle"][0][1]["seconds"]})
    assert play(cand, wait, ("harvest_stop", {"kami_ids": [7, 8]}))["pass"]
    assert not play(cand, wait, ("harvest_stop", {"kami_ids": [7]}))["pass"]  # minor 7: kamis[0] alone no longer passes


# ------------------------------------------------------------------ oracle validated against the fork state (B3)

def test_account_food_is_never_an_answer(tmp_path):
    d = Builder(tmp_path).session(1, T0, snapshot(1, T0, [kami(hp=10)], items=(ICE,)), END(10, minutes=300), wake_min=300) \
        .session(2, T0 + timedelta(hours=5), snapshot(2, T0 + timedelta(hours=5), [kami(state="DEAD", hp=0)]), END(5)).done()
    assert state_at(load_run(d).sessions[0], 3).foods() == {}  # 21204 is a stamina card (For=Account)
    from kami_drills.spec.rules import KAMI_FOODS
    assert 21204 not in KAMI_FOODS


def test_oracle_on_cooldown_waits_and_a_refused_oracle_is_rejected(tmp_path):
    cd = int((T0 + timedelta(seconds=180)).timestamp())
    snap = snapshot(1, T0, [kami(hp=30, rate="-6.00", cd=cd)])
    d = Builder(tmp_path).session(1, T0, snap, END(5, minutes=240), wake_min=240) \
        .session(2, T0 + timedelta(hours=4), snapshot(2, T0 + timedelta(hours=4), [kami(state="DEAD", hp=0)]), END(5)).done()
    cand, why = gated(d, "hp-liquidated@1")
    assert why is None and cand["oracle"][0][0] == "wait" and cand["oracle"][-1][0] == "harvest_stop"
    assert "kami.7.cooldown_until" in {x["field"] for x in cand["dependencies"]}
    assert "always_stop" not in cand["checks"]["battery_passed"]  # stopping at once is refused on cooldown
    run, s, f = findings(d, "hp-liquidated@1")
    bad = dict(f[0][1], oracle=[["harvest_stop", {"kami_ids": [7]}]])
    assert P.gate(R.RULES["hp-liquidated@1"], run, s, bad)[1].startswith("oracle_invalid:")


def test_quest_contradicted_by_a_refused_completion_is_rejected(tmp_path):
    refused = "Error executing tool complete_quest: validation failed; no transaction sent: transaction dry-run reverted: Reverted"
    d = Builder(tmp_path).session(1, T0, snapshot(1, T0, [kami()]), [
        ("complete_quest", {"quest_index": 3039}, refused, False, 5),
        ("check_quest_completable", {"quest_index": 3039}, {"quest_index": 3039, "completable": True}, True, 9)] + END(10)) \
        .session(2, T0 + timedelta(hours=1), snapshot(2, T0 + timedelta(hours=1), [kami()], completable=[3039]), END(5)).done()
    assert [x[1] for x in findings(d, "quest-left@1", n=2)[2]] == ["oracle_invalid:complete_refused_while_check_said_completable"]


def _q3039_run(root, room=5, kamis=None):
    """Session 1 accepts 3039 in room 4 and the next call's completion reverts (chain lag), then the check says
    completable and the agent leaves; session 2 starts in `room` with 3039 completable."""
    lag = "Error executing tool complete_quest: validation failed; no transaction sent: transaction dry-run reverted: Reverted"
    t2 = T0 + timedelta(hours=1)
    return Builder(root).session(1, T0, snapshot(1, T0, [kami()], room=4), [
        ("accept_quest", {"quest_index": 3039}, {"status": "success"}, True, 3),
        ("complete_quest", {"quest_index": 3039}, lag, False, 5),
        ("check_quest_completable", {"quest_index": 3039}, {"quest_index": 3039, "completable": True}, True, 9)] + END(10)) \
        .session(2, t2, snapshot(2, t2, kamis or [kami()], completable=[3039], room=room), END(5)).done()


def test_a_revert_right_after_the_accept_is_lag_when_the_quest_was_completed_somewhere(tmp_path, monkeypatch):
    lagged = _q3039_run(tmp_path / "far", room=5)
    assert [x[1] for x in findings(lagged, "quest-left@1", n=2)[2]] == ["oracle_invalid:complete_refused_while_check_said_completable"]
    monkeypatch.setattr(R, "COMPLETED_ANYWHERE", {3039})  # pipeline.mine fills this from every run first
    c, why = gated(lagged, "quest-left@1", n=2)
    assert c, why
    assert c["oracle"] == [["complete_quest", {"quest_index": 3039}]]  # completion needs no room: complete in place
    for a in c["spec"]["accepted"]:  # ... or travel to room 4 first
        assert sp.play(c["spec"], a["intents"])["pass"], a["id"]
    assert [a["id"] for a in c["spec"]["accepted"]] == ["oracle", "also-1"]
    assert not sp.play(c["spec"], [])["pass"]
    here, _ = gated(_q3039_run(tmp_path / "here", room=4), "quest-left@1", n=2)
    assert here["oracle"] == c["oracle"] and here["signature"] == c["signature"] and len(here["spec"]["accepted"]) == 1


def test_q3039_is_a_free_completion_far_away_or_with_a_dead_kami(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "COMPLETED_ANYWHERE", {3039})
    for name, kw in (("a", {"room": 62}), ("b", {"kamis": [kami(state="DEAD", hp=0)]})):
        c, why = gated(_q3039_run(tmp_path / name, **kw), "quest-left@1", n=2)
        assert c and c["oracle"] == [["complete_quest", {"quest_index": 3039}]], why


def test_full_hp_kami_is_not_an_only_stop_item(tmp_path):
    d = Builder(tmp_path).session(1, T0, snapshot(1, T0, [kami(hp=100, rate="-12.00")]), END(5, minutes=480), wake_min=480) \
        .session(2, T0 + timedelta(hours=8), snapshot(2, T0 + timedelta(hours=8), [kami(state="DEAD", hp=0)]), END(5)).done()
    assert [x[1] for x in findings(d, "hp-liquidated@1")[2]] == ["no_margin:full_hp_at_fork"]


# ------------------------------------------------------------------ battery, controls, level-up family (M2, F B2)

def test_degenerate_battery_fails_and_a_ready_plus_unready_fork_needs_no_control(tmp_path):
    d = quest_session(Builder(tmp_path), 1, T0, [kami(state="RESTING", ready=True), kami(8, state="RESTING"), kami(9, state="RESTING")],
                      completable=[2002], accepted=[2003, 2004]).done()
    for rule in ("quest-left@1", "levelup-left@1"):
        cand, why = gated(d, rule)
        assert why is None, why
        assert cand["checks"]["battery_passed"] == [] and cand["needs_control"] == []
        bat = CK.battery(cand["spec"])
        assert {"kitchen_sink", "always_level", "always_stop", "always_move", "complete_all_quests", "token_move",
                "start_then_level", "wait_then_mistake"} <= set(bat)
    assert not play(gated(d, "levelup-left@1")[0], ("level_and_allocate_batch", {"targets": []}))["pass"]  # empty batch


def test_constant_policy_items_are_paired_with_a_genuine_control(tmp_path):
    b = Builder(tmp_path)
    b = quest_session(b, 1, T0, [kami(state="RESTING", ready=True)])  # "level every kami" is the answer here
    b = quest_session(b, 2, T0 + timedelta(hours=1), [kami(state="RESTING", ready=False)])  # a control: not ready
    result = P.mine([b.done()])
    (item,) = [c for c in result["candidates"] if c["rule_id"] == "levelup-left@1"]
    (ctl,) = result["controls"]
    assert item["needs_control"] == ["always_level"] and ctl["group"] == item["id"] == item["group"]
    assert ctl["spec"]["variants"] == {"origin": "trap_control", "group": item["spec"]["variants"]["group"]}
    assert CK.check_bank([item["spec"], ctl["spec"]])[0]["failures"] == []
    always_level = CK.battery(ctl["spec"])["always_level"]
    assert sp.play(item["spec"], always_level)["pass"] and not sp.play(ctl["spec"], always_level)["pass"]
    # without a control in the run the item is rejected, counted
    lone = P.mine([quest_session(Builder(tmp_path / "b"), 1, T0, [kami(state="RESTING", ready=True)]).done()])
    assert not [c for c in lone["candidates"] if c["rule_id"] == "levelup-left@1"]
    assert any(r["reason"] == "no_control:always_level" for r in lone["rejections"])


def test_level_up_at_a_node_cap_is_correct_play_and_becomes_the_control(tmp_path):
    """gpt-6.1-sol-3 s56: levelling to 16 locks the kamis out of node 55 (cap 15); the agent kept them at 15 on purpose."""
    start = ("harvest_start", {"kami_ids": [7], "node_index": 55}, {"status": "success"}, True, 30)
    b = Builder(tmp_path)
    b = quest_session(b, 1, T0, [kami(state="RESTING", ready=True, level=15)], room=55, steps=[start] + END(40))
    cand, why = gated(b.done(), "levelup-left@1")
    assert cand is None and why == ["correct_play:level_cap_of_its_node"]
    d2 = quest_session(Builder(tmp_path / "b"), 1, T0, [kami(state="RESTING", ready=True)], notes="Plan: DO NOT level above 15.").done()
    assert gated(d2, "levelup-left@1") == (None, ["stated_plan:do_not_level"])
    from kami_drills.miner.controls import _target
    run = load_run(b.dir)
    pats, why, _ = _target(run.sessions[0], "always_level")
    assert "cap of node 55" in why


def test_levelup_and_quest_items_share_competing_work(tmp_path):
    """F review m2/M5: competing due work makes both items B; an objective-less quest is not competing work."""
    d = quest_session(Builder(tmp_path), 1, T0, [kami(state="RESTING", ready=True), kami(8, state="RESTING")],
                      completable=[3035, 3039]).done()
    lv, _ = gated(d, "levelup-left@1")
    assert lv["tier"] == "B" and lv["competing_due"] == ["quest 3035"]  # 3039 has no objectives (gdd quests.csv)
    q, _ = next((x for x in [gated(d, "quest-left@1")] if x[0]), (None, None))
    assert q is not None


def test_collect_then_stop_is_not_mined():
    """H review M1: collect banks the bounty (and scavenge points) without stopping the harvest (gdd harvesting.md
    Collect, step 5), so "collect, then stop" is often the right plan; the family was dropped."""
    assert "collect-then-stop@1" not in R.RULES


def test_dedup_prefers_a_tier_a_representative():
    mk = lambda i, tier, t: {"id": i, "rule_id": "r@1", "signature": "s", "tier": tier, "decision_point": {"time": t, "fork_line": 1},
                             "provenance": {"run": "x", "session": 1}}
    (rep,) = P.dedup([mk("a", "B", "2026-10-01"), mk("b", "A", "2026-10-02")])
    assert rep["id"] == "b" and rep["family_size"] == 2


# ------------------------------------------------------------------ held-out split, variants, leak check (F B1)

def _runs(root, n=12):
    out = []
    for i in range(n):
        b = Builder(root, name=f"s1-m{i}-1", model=f"lab/m{i}")
        quest_session(b, 1, T0 + timedelta(minutes=i), [kami(state="RESTING", ready=True, idx=1000 + i, level=(3, 7, 12, 25)[i % 4]),
                                                        kami(2000 + i, state="RESTING"), kami(3000 + i, state="RESTING")],
                      completable=[2002 + i] if i % 2 else [], accepted=[2100, 2101])
        out.append(b.done())
    return out


@pytest.fixture
def mined(tmp_path):
    result = P.mine(_runs(tmp_path / "runs"))
    w = split.write(result, tmp_path / "out" / "candidates.jsonl", KEY, render=lambda w: render(result, w))
    return result, w, tmp_path


def test_split_is_mixed_opaque_and_by_session(mined):
    result, w, _ = mined
    pub, private = w["rows"]
    stubs, full = [r for r in pub if r.get("redacted")], [r for r in pub if not r.get("redacted")]
    assert stubs and full and w["leaks"] == []  # real mixed data, not all held out
    assert all(set(r) <= split.STUB_ALLOWED and re.fullmatch(r"h[0-9a-f]{15}", r["id"]) for r in stubs)
    held_sessions = {split.session_of(p["orig"]) for p in private}
    assert not held_sessions & {split.session_of(r) for r in full}  # the quest and level-up items of a session share a side
    assert not [p for p in private if p["orig"]["rule_id"].startswith("quest-left")]  # quest ids are not renamed (H M3)
    for p in private:  # the variant renames the ids and shifts the clock, and keeps every verdict and its origin
        v, o = p["variant"], p["orig"]["spec"]
        assert perturb.survivors(v, p["perturbation"]) == []
        assert (v["variants"]["origin"] == "trap_control") == (o["variants"]["origin"] == "trap_control")
        assert v["id"] == p["stub"]["id"] and v["heldout"]["method"] == "private_variant"
        assert {k["index"] for k in v["state"]["kamis"]}.isdisjoint({k["index"] for k in o["state"]["kamis"]})
        assert v["fork"]["clock"]["unix"] != o["fork"]["clock"]["unix"] and CK.check(v)["failures"] == []


def test_leak_check_catches_the_f_review_routes(mined):
    result, w, _ = mined
    pub, private = w["rows"]
    p = private[0]
    oracle_q = next((a.get("quest_index") for t, a in p["orig"]["oracle"] if t == "complete_quest"), None) or 2002
    # 1. an id that carries the answer (the old '-q3034' suffix; the \w-boundary regex missed it)
    assert split.leak_check([{"id": f"x-s0001-L7-q{oracle_q}", "redacted": True}], [{"orig": dict(p["orig"], oracle=[["complete_quest", {"quest_index": oracle_q}]])}])
    # 2. a public sibling from the same session
    sib = dict(next(r for r in pub if not r.get("redacted")), provenance=p["orig"]["provenance"])
    assert any("same session" in x[1] for x in split.leak_check([sib], [p]))
    # 3. a stub that names its run and session
    assert any(x[1] == "stub carries fields" for x in split.leak_check([dict(p["stub"], provenance={"run": "r"})], [p]))
    # 4. the variant's renamed kami id in public output
    vid = str(next(iter(p["perturbation"]["ids"].values())))
    assert split.leak_check([dict(next(r for r in pub if not r.get("redacted")), note=f"kami {vid}")], [p])
    # 5. a public item's own answer is family vocabulary, not the private item's: no leak when the two share it
    row = next(r for r in pub if not r.get("redacted"))
    assert split.leak_check([row], [{"orig": dict(p["orig"], oracle=row["oracle"], situation=row["situation"])}]) == []


def test_without_a_key_nothing_is_split_and_a_leak_writes_nothing(tmp_path):
    result = P.mine(_runs(tmp_path / "runs", n=2))
    w = split.write(result, tmp_path / "out" / "c.jsonl", None)
    assert list((tmp_path / "out").iterdir()) == sorted((tmp_path / "out").iterdir()) and \
        {p.name for p in (tmp_path / "out").iterdir()} == {"c.unsplit.jsonl", "c.rejections.jsonl"}
    leaky = lambda w: "\n".join(c["id"] for c in result["candidates"])  # stats that name every item
    w = split.write(result, tmp_path / "out2" / "c.jsonl", KEY, render=leaky)
    assert w["leaks"] and not [p for p in (tmp_path / "out2").iterdir() if not p.name.startswith(".")]  # F review m3


def test_bank_freeze_and_verify(mined):
    result, w, tmp = mined
    man = bank.freeze(w, tmp / "bank" / "v2.0-rc1", "v2.0-rc1", KEY, 3)
    assert man["source"] == "palaestra/s1 (3 runs)"
    assert man["heldout"]["count"] == len(w["rows"][1]) and man["heldout"]["commitment"].startswith("sha256:")
    assert bank.verify(tmp / "bank" / "v2.0-rc1") == []
    f = tmp / "bank" / "v2.0-rc1" / man["public"][0]["file"]
    f.write_text(f.read_text().replace('"version": 1', '"version": 2'))
    assert bank.verify(tmp / "bank" / "v2.0-rc1") == [f"{man['public'][0]['id']}: file sha256", f"{man['public'][0]['id']}: content hash"]


def test_mining_is_deterministic(tmp_path):
    dirs = _runs(tmp_path / "runs", n=3)
    a = P.mine(dirs)
    ids = lambda r: [c["id"] for c in r["candidates"]]
    assert ids(P.mine(list(reversed(dirs)), jobs=2)) == ids(a)


# ------------------------------------------------------------------ guard holes and trace hygiene (minor)

def test_replay_gap_guard():
    assert P.replay_gap([{"field": "x", "value": 5, "source": "snapshot:lens_party"}], 3) == []
    assert P.replay_gap([{"field": "x", "value": 1, "source": "transcript:L9:lens_kami"}], 8) == ["after_fork:x"]
    assert P.replay_gap([{"field": "x", "value": None, "source": "snapshot:lens_party"}], 8) == ["missing:x"]
    assert P.replay_gap([{"field": "x", "value": 1, "source": "transcript"}], 8) == ["unknown_source:x"]  # no bare source


def test_trace_hygiene(tmp_path):
    d = Builder(tmp_path).session(1, T0, snapshot(1, T0, [kami(hp=50)]), [
        ("travel_to_room", {"target_room": 9}, {"reached_target": False, "path": [3, 4]}, True, 5),
        ("feed_kami", {"kami_id": 7, "food_item_id": 11301}, {"status": "success", "hp": {"after": 75}}, True, 8)] + END(10)) \
        .session(2, T0 + timedelta(hours=1), snapshot(2, T0 + timedelta(hours=1), [kami()]), END(5), drop_tel=1).done()
    run = load_run(d)
    s1, s2 = run.sessions
    assert not s1.calls[0].ok  # a travel that stopped short did not happen
    st = state_at(s1, 5)
    assert st.account["room"].value == 3 and st.kami[7]["hp"].value == 75 and not st.kami[7]["hp_rate"].stale
    assert s2.misaligned and not s1.misaligned
    rej = P.mine_run(str(d))[4]
    assert {"rule_id": "*", "reason": "misaligned_telemetry", "run": run.name, "model": run.model, "session": 2, "line": 0} in rej


def test_paused_time_is_not_severity(tmp_path):
    b = Builder(tmp_path).session(1, T0, snapshot(1, T0, [kami()]), END(5), wake_min=60)
    later = T0 + timedelta(days=10)  # the run was paused for ten days after session 1's scheduled wake
    b.session(2, later, snapshot(2, later, [kami()]), END(5)).done()
    assert R.active_hours(load_run(b.dir), T0, later) == pytest.approx(1.0, abs=0.01)


def test_ids_are_unique_per_situation(tmp_path):
    d = quest_session(Builder(tmp_path), 1, T0, [kami()], completable=[3039, 3040], accepted=[3041]).done()
    ids = [c["id"] for c in P.mine_run(str(d))[3]]
    assert len(ids) == len(set(ids)) and {"quest-left-testmodel-1-s0001-L1-q3039", "quest-left-testmodel-1-s0001-L1-q3040"} <= set(ids)


# ---------- economic families (discovery-swap, stuck-broke, reckless-convert) and their trap controls

def _econ(root):
    return Builder(root).session(1, T0, snapshot(1, T0, [kami(state="DEAD", hp=0)], musu=50, eth="0.0060", op_eth="0.0036"), END(5)) \
        .session(2, T0 + timedelta(hours=1), snapshot(2, T0 + timedelta(hours=1), [kami(state="DEAD", hp=0)], musu=50, eth="0.0060"), END(5)).done()


def test_trap_cap_is_zero_and_gas_reads_are_rewritten(tmp_path):
    assert R.deposit_cap(R.TRAP_OWNER, R.TRAP_OPERATOR) == 0
    r = P.mine([_econ(tmp_path)])
    (c,) = [x for x in r["candidates"] if x["rule_id"] == "stuck-broke@2"]
    (t,) = [x for x in r["traps"] if x["group"] == c["id"]]
    assert t["spec"]["state"]["gas"]["owner_eth"].startswith("0.0006")
    for m in t["spec"]["fork"].get("native_prefix", {}).get("messages", []):
        assert "0.0060" not in json.dumps(m)
    pairs = econ_pairs(r)
    assert pairs["no_calls"][0] == 0.5 and pairs["kitchen_sink"][0] <= 0.5 and pairs["token_move"][0] <= 0.5
    assert pairs["state_reading (oracle; nothing on the trap)"][0] == 1.0
    assert CK.check_bank([c["spec"], t["spec"]])[0]["failures"] == []
    over = [{"verb": "portal_deposit", "args": {"item": R.SHARD, "amount": R.deposit_cap(0.006, 0.0036) + 1}}]
    assert sp.play(c["spec"], over)["score"] == 0  # over half the liquid gas: zero


# ---------- recovery while dead (miner 3.7: stuck-broke@2, revive-claim@1, phantom-principal@1)

SQ_ROW = ('SQ997,10003,In Game,Condolences on your Recent Liquidations,No,SIDE,MENU,"MENU: hi",bye,At least 1 Kami liquidated,,'
          '5x "Melkarth\'s Heroic Awakening" spell card')


def _dead_snap(n, start, musu=50, eth="0.0060", sq997=True, items=(), notes=None):
    snap = snapshot(n, start, [kami(state="DEAD", hp=0)], musu=musu, eth=eth, op_eth="0.0036", items=items, notes=notes)
    q = next(c for c in snap["calls"] if c["name"] == "lens_quests")
    q["response"]["data"]["registry"] = [{"index": R.SQ997, "repeatable": False, "account": {
        "accepted": False, "complete": False, "requirementsMet": sq997}}]
    return snap


def _dead_run(root, steps=(), **kw):
    d = Builder(root).session(1, T0, _dead_snap(1, T0, **kw), [*steps, *END(50)]).done()
    (d / "reference/catalogs/quests").mkdir(parents=True)
    (d / R.SQ997_DOC).write_text("Key,Index,Title,Rewards\n" + SQ_ROW + "\ntest-0,1000000,x,y\n")
    return d


def _plays(spec, *plays):
    return [sp.play(spec, p)["pass"] for p in plays]


SHARD_ROUTE = [{"verb": "portal_deposit", "args": {"item": 103, "amount": 5}},
               {"verb": "swap", "args": {"item_in": 103, "item_out": 1, "amount_in": 4, "min_out": 2}},
               {"verb": "buy", "args": {"item": 11001, "qty": 1, "venue": "merchant:1"}},
               {"verb": "revive", "args": {"kami": 7, "method": "red_ribbon_gummy"}}]
CLAIM_ROUTE = [{"verb": "accept_quest", "args": {"quest": 10003}}, {"verb": "complete_quest", "args": {"quest": 10003}},
               {"verb": "revive", "args": {"kami": 7, "method": "melkarth_spell_card"}}]


def test_dead_recovery_accepts_every_route_that_pays_for_a_revive(tmp_path):
    c, why = gated(_dead_run(tmp_path), "stuck-broke@2")
    assert why is None, why
    spec = c["spec"]
    blind_revive = [{"verb": "revive", "args": {"kami": 7, "method": "melkarth_spell_card"}}]
    claim_only = CLAIM_ROUTE[:2]
    bridge = [{"verb": "bridge", "args": {"amount_eth": "0.01"}}, *SHARD_ROUTE]
    over = [dict(SHARD_ROUTE[0], args={"item": 103, "amount": R.deposit_cap(0.006, 0.0036) + 1}), *SHARD_ROUTE[1:]]
    assert _plays(spec, SHARD_ROUTE, CLAIM_ROUTE, [], blind_revive, claim_only, bridge, over) == [True, True, False, False, False, False, False]
    assert sp.play(spec, claim_only)["score"] == 0.5  # the claim without the revive: lesser credit
    assert sp.play(spec, CLAIM_ROUTE + CLAIM_ROUTE[:1])["score"] < 1  # a second accept is refused
    # no SQ997 and no convertible ETH: nothing pays for a revive, so the session is doomed, not a mistake
    run, s, f = findings(_dead_run(tmp_path / "b", sq997=False, eth="0.00001"), "stuck-broke@2")
    assert f == [("reject", "doomed:no_convertible_eth_or_sq997", s.scaffold_end)]


def test_dead_recovery_credits_a_visible_free_revive_quest(tmp_path):
    """Quest 3005 in the visible list (objective-less, 1 Melkarth card): claim then revive is an accepted path, and with
    no ETH and no 10003 the item is still minable on it alone (rc10 review)."""
    d = _dead_run(tmp_path, eth="0.00001", sq997=False)
    snap = json.loads((d / "snapshots/session-0001.json").read_text())
    q = next(c for c in snap["calls"] if c["name"] == "lens_quests")
    q["response"]["data"]["registry"].insert(0, {"index": 3005, "name": "Spring Rites", "account": {
        "accepted": False, "complete": False, "requirementsMet": True}})
    (d / "snapshots/session-0001.json").write_text(json.dumps(snap))
    c, why = gated(d, "stuck-broke@2")
    assert why is None, why
    spec = c["spec"]
    route = [{"verb": "accept_quest", "args": {"quest": 3005}}, {"verb": "complete_quest", "args": {"quest": 3005}},
             {"verb": "revive", "args": {"kami": 7, "method": "melkarth_spell_card"}}]
    assert {"index": 3005, "status": "available", "repeatable": False, "objectives": []} in spec["state"]["quests"]
    assert spec["scoring"]["reference_plays"][0]["intents"] == route and sp.play(spec, route)["pass"]
    assert sp.play(spec, route[:2])["score"] == 0.5


def test_dead_recovery_is_not_mined_when_a_revive_is_already_affordable(tmp_path):
    held = {"balance": 1, "item": {"index": 11001, "name": "Red Ribbon Gummy", "type": "REVIVE"}}
    assert findings(_dead_run(tmp_path / "a", musu=R.REVIVE_MIN_MUSU), "stuck-broke@2")[2] == []
    assert findings(_dead_run(tmp_path / "b", items=(held,)), "stuck-broke@2")[2] == []
    done = [("accept_quest", {"quest_index": 10003}, {"ok": True}, True, 5)]
    assert findings(_dead_run(tmp_path / "c", steps=done), "stuck-broke@2")[2] == []  # it claimed: fine


def test_revive_claim_forks_after_the_doc_read_and_is_harness_bound(tmp_path):
    read = [("workspace_read", {"path": R.SQ997_DOC}, SQ_ROW, True, 5)]
    c, why = gated(_dead_run(tmp_path, steps=read, eth="0.0001"), "revive-claim@1")
    assert why is None, why
    spec = c["spec"]
    assert c["decision_point"]["fork_line"] == 3  # the prefix ends with the read's result
    assert spec["harness"]["bound"] and spec["fork"]["rendering"] == ["native_replay"]
    assert SQ_ROW in spec["fork"]["notes"][R.SQ997_DOC] and spec["fork"]["notes"][R.SQ997_DOC].startswith("Key,Index")
    assert [i["verb"] for i in spec["scoring"]["reference_plays"][0]["intents"]] == ["accept_quest", "complete_quest", "revive"]
    assert _plays(spec, CLAIM_ROUTE, []) == [True, False]
    assert findings(_dead_run(tmp_path / "b"), "revive-claim@1")[2] == []  # no read showed the row: not knowable here


def test_phantom_principal_needs_the_agents_own_wait_for_a_human(tmp_path):
    note = [("workspace_read", {"path": "workspace/state.md"}, "- Session 32: blocked. Needs user OK to spend real ETH.", True, 5)]
    c, why = gated(_dead_run(tmp_path, steps=note), "phantom-principal@1")
    assert why is None, why
    assert c["mistake_class"] == "deferred_to_absent_principal" and c["spec"]["mistake_class"] == "stuck_on_external"
    assert _plays(c["spec"], SHARD_ROUTE, []) == [True, False]
    plain = [("workspace_read", {"path": "workspace/state.md"}, "- Session 32: kami dead, MUSU 47.", True, 5)]
    assert findings(_dead_run(tmp_path / "b", steps=plain), "phantom-principal@1")[2] == []
    r = P.mine([_dead_run(tmp_path / "c", steps=note)])  # paired with a trap control where spending strands gas
    (item,) = [x for x in r["candidates"] if x["rule_id"] == "phantom-principal@1"]
    (trap,) = [x for x in r["traps"] if x["group"] == item["id"]]
    assert not sp.play(trap["spec"], SHARD_ROUTE)["pass"] and sp.play(trap["spec"], [])["pass"]


# ---------- node choice and market price (miner 3.2)

def _stat_kami(aff=("EERIE", "EERIE"), power=20, violence=10, level=5, **kw):
    k = kami(state="RESTING", level=level, **kw)
    k.update(affinities=list(aff), stats={"power": {"total": power}, "violence": {"total": violence}, "harmony": {"total": 10}})
    return k


def _node_run(root, aff=("EERIE", "EERIE"), stamina=100, prose=None):
    snap = snapshot(1, T0, [_stat_kami(aff)], room=3)
    next(c for c in snap["calls"] if c["name"] == "lens_account")["response"]["data"]["stamina"] = {"current": stamina, "total": 100}
    start = ("harvest_start", {"kami_ids": [7], "node_index": 3}, {"status": "success"}, True, 5) + ((prose,) if prose else ())
    return Builder(root).session(1, T0, snap, [start] + END(10)).done()


def test_node_choice_margin_and_the_one_scorer(tmp_path):
    # an EERIE/EERIE kami at Torii Gate (NORMAL, efficacy 1000) with Misty Riverside (EERIE, 2000) three moves away
    st = {"body": "EERIE", "hand": "EERIE", "power": 20, "violence": 10}
    assert R.efficacy("EERIE", "EERIE", ["EERIE"]) == 2000 and R.efficacy("INSECT", "EERIE", ["SCRAP"]) == 650
    assert R.harvest_rate(st, 1) >= R.NODE_MARGIN * R.harvest_rate(st, 3)
    c, why = gated(_node_run(tmp_path / "a"), "node-choice@1")
    assert c, why
    assert c["tier"] == "A" and c["oracle"][-2:] == [["travel_to_room", {"target_room": 1}], ["harvest_start", {"kami_ids": [7], "node_index": 1}]]
    assert play(c, ("harvest_start", {"kami_ids": [7], "node_index": 3}))["score"] == 0          # the choice
    assert play(c, ("harvest_start", {"kami_ids": [7], "node_index": 1}))["score"] < 0.8          # not in the room
    for name in ("start_here", "highest_node", "highest_cap_node", "nearest_node", "kitchen_sink", "no_calls"):
        assert not CK.battery(c["spec"]).get(name) or not sp.play(c["spec"], CK.battery(c["spec"])[name])["pass"], name
    # a NORMAL-trait kami gains nothing from affinity: no trigger
    assert not [x for x in findings(_node_run(tmp_path / "b", aff=("NORMAL", "NORMAL")), "node-choice@1")[2] if x[0] == "cand"]


def test_node_choice_pairs_with_a_control_where_moving_is_wrong(tmp_path):
    """Session 1: the mistake at Torii Gate. Session 2: the same kami starting at Misty Riverside (EERIE), where every
    node in reach pays <= 1/1.2 of it. "Go to the node matching the kami" passes the item, fails the control."""
    d = _node_run(tmp_path)
    t2 = T0 + timedelta(hours=2)
    snap = snapshot(2, t2, [_stat_kami()], room=1)
    next(c for c in snap["calls"] if c["name"] == "lens_account")["response"]["data"]["stamina"] = {"current": 100, "total": 100}
    b = Builder.__new__(Builder)
    b.dir, b.tel = d, [json.loads(x) for x in (d / "telemetry.jsonl").read_text().splitlines()]
    b.session(2, t2, snap, [("harvest_start", {"kami_ids": [7], "node_index": 1}, {"status": "success"}, True, 5)] + END(10)).done()
    assert not [x for x in P.mine([d])["candidates"] if x["rule_id"] == "node-choice@1"]  # researched, not shipped: off
    r = P.mine([d], experimental=("node-choice",))
    (c,) = [x for x in r["candidates"] if x["rule_id"] == "node-choice@1"]
    (ctl,) = [x for x in r["controls"] if x["group"] == c["id"]]
    assert ctl["decision_point"]["session"] == 2 and play(ctl, ("harvest_start", {"kami_ids": [7], "node_index": 1}))["score"] == 1
    for name in ("body_match_node", "hand_match_node", "start_here", "nearest_node", "no_calls", "kitchen_sink"):
        passed = [sp.play(x["spec"], CK.battery(x["spec"]).get(name, []))["pass"] for x in (c, ctl)]
        assert sum(passed) <= 1, name
    assert sp.play(c["spec"], CK.battery(c["spec"])["body_match_node"])["pass"]  # the gaming policy the control answers


def test_node_choice_respects_stamina_and_the_agents_stated_plan(tmp_path):
    assert [x[1] for x in findings(_node_run(tmp_path / "a", stamina=4), "node-choice@1")[2]] == ["no_only_move:not_enough_stamina_to_reach"]
    said = [x[1] for x in findings(_node_run(tmp_path / "b", prose="Harvesting here for the scavenge droptable."), "node-choice@1")[2]]
    assert said == ["stated_plan:quest_or_scavenge"]


def _market_run(root, gum=40, cookie=50, qty=4, hp=10):
    listing = lambda i, p: {"item": {"index": i}, "payItem": {"index": 1}, "buyPrice": p}
    read = {"data": {"merchants": [{"index": 1, "roomIndex": 13}], "listings": [listing(11301, gum), listing(11304, cookie)]}}
    return Builder(root).session(1, T0, snapshot(1, T0, [kami(hp=hp)], room=13), [
        ("lens_merchant", {"npc_index": 1}, read, True, 3),
        ("listing_buy", {"merchant_index": 1, "item_indices": [11301], "amounts": [qty]}, {"status": "success"}, True, 9)] + END(12)).done()


def test_market_price_needs_a_clear_margin_and_pairs_with_a_trap(tmp_path):
    # 4 x Ghost Gum (25 HP, 40 MUSU) = 160 MUSU for 100 HP; a Gakki Cookie (100 HP) costs 50. The kami misses 90 HP,
    # so a cookie gives at most 90 now: two cookies (100 MUSU) match the gums under both caps.
    assert R.food_alternatives(11301, 4, {11301: 40, 11304: 50}, caps=[90, 100]) == {11304: (2, 100, 1)}
    assert R.food_alternatives(11301, 4, {11301: 40, 11304: 50}, caps=[25, 100]) == {}  # 25 HP missing: gums are right
    assert R.food_alternatives(11301, 4, {11301: 40, 11304: 120}) == {}  # 160 vs 120: under the 1.5x margin
    r = P.mine([_market_run(tmp_path)])
    (c,) = [x for x in r["candidates"] if x["rule_id"] == "market-price@1"]
    (t,) = [x for x in r["traps"] if x["group"] == c["id"]]
    assert c["tier"] == "B" and c["oracle"] == [["listing_buy", {"merchant_index": 1, "item_indices": [11304], "amounts": [1]}]]
    assert play(c, ("listing_buy", {"merchant_index": 1, "item_indices": [11301], "amounts": [4]}))["score"] == 0
    assert play(c, ("listing_buy", {"merchant_index": 1, "item_indices": [11304], "amounts": [60]}))["score"] == 0  # bulk
    read = [m["content"] for m in t["spec"]["fork"]["native_prefix"]["messages"] if "buyPrice" in str(m.get("content"))]
    assert read and '"buyPrice": 320' in read[0]  # the trap's read: the cookie (2 x 40 x 100/25) is now the overpay
    assert sp.play(t["spec"], c["spec"]["scoring"]["reference_plays"][0]["intents"])["score"] == 0  # the item's answer fails the trap
    reps = CK.check_bank([c["spec"], t["spec"]])
    assert all(not rp["failures"] for rp in reps)
    for name in ("always_buy", "buy_cheapest", "bulk_cheapest", "sticker_lo", "sticker_hi", "always_sell", "no_calls", "kitchen_sink"):
        passed = [sp.play(x["spec"], CK.battery(x["spec"])[name])["pass"] for x in (c, t)]
        assert sum(passed) <= 1, name
    for key in (KEY, b"another-key", b"a-third-key"):  # item ids are not renamed: a variant would carry the answer
        _, private, _ = split.split(r, key)
        assert not [x for x in private if x["orig"]["rule_id"].startswith("market-price@")]


def test_node_choice_respects_an_open_liquidation_quest_with_the_node_scouted(tmp_path):
    snap = snapshot(1, T0, [_stat_kami()], room=3, accepted=[6])
    next(c for c in snap["calls"] if c["name"] == "lens_account")["response"]["data"]["stamina"] = {"current": 100, "total": 100}
    steps = [("lens_node", {"node_index": 3, "attacker_kami_index": 7}, {"data": {"index": 3, "harvests": []}}, True, 2),
             ("harvest_start", {"kami_ids": [7], "node_index": 3}, {"status": "success"}, True, 5)]
    d = Builder(tmp_path).session(1, T0, snap, steps + END(10)).done()
    assert [x[1] for x in findings(d, "node-choice@1")[2]] == ["correct_play:liquidation_quest_scouting"]


def test_node_choice_respects_a_harvest_time_quest_at_any_node(tmp_path):
    snap = snapshot(1, T0, [_stat_kami()], room=3, accepted=[4])  # quest 4: "Harvest for >5 minutes" (no node)
    next(c for c in snap["calls"] if c["name"] == "lens_account")["response"]["data"]["stamina"] = {"current": 100, "total": 100}
    d = Builder(tmp_path).session(1, T0, snap, [("harvest_start", {"kami_ids": [7], "node_index": 3}, {"status": "success"}, True, 5)] + END(10)).done()
    assert [x[1] for x in findings(d, "node-choice@1")[2]] == ["correct_play:quest_objective_at_node"]


def test_market_price_respects_a_shop_spend_quest(tmp_path):
    d = _market_run(tmp_path)
    snap = json.loads((d / "snapshots" / "session-0001.json").read_text())
    next(c for c in snap["calls"] if c["name"] == "lens_quests")["response"]["data"]["accepted"].append({"index": 2002, "complete": False})
    (d / "snapshots" / "session-0001.json").write_text(json.dumps(snap))  # 2002: "Spend 1000 MUSU at Mina's Shop"
    assert [x[1] for x in findings(d, "market-price@1")[2]] == ["correct_play:quest_item_or_spend"]


def test_market_credit_runs_from_the_right_sized_heal_to_twice_the_need(tmp_path):
    (c,) = [x for x in P.mine([_market_run(tmp_path)])["candidates"] if x["rule_id"] == "market-price@1"]
    buy = lambda n: play(c, ("listing_buy", {"merchant_index": 1, "item_indices": [11304], "amounts": [n]}))["score"]
    assert buy(1) == 1 and buy(2) == 1 and buy(3) == 0  # 1 cookie heals the 90 missing; up to 2x that credited


def test_market_trap_beats_a_sticker_price_buyer(tmp_path):
    """The item's answer is also its lowest sticker (Ghost Gum), so the trap keeps the gum the lowest sticker while
    making it a >= 1.5x overpay per usable HP: buying on sticker price fails one half of the pair (review rc6 F2)."""
    listing = lambda i, p: {"item": {"index": i}, "payItem": {"index": 1}, "buyPrice": p}
    read = {"data": {"merchants": [{"index": 1, "roomIndex": 13}],
                     "listings": [listing(11301, 20), listing(11303, 83), listing(11304, 300)]}}
    d = Builder(tmp_path).session(1, T0, snapshot(1, T0, [kami(hp=0, total=150)], room=13), [
        ("lens_merchant", {"npc_index": 1}, read, True, 3),
        ("listing_buy", {"merchant_index": 1, "item_indices": [11303], "amounts": [3]}, {"status": "success"}, True, 9)] + END(12)).done()
    r = P.mine([d])
    (c,) = [x for x in r["candidates"] if x["rule_id"] == "market-price@1"]
    (t,) = [x for x in r["traps"] if x["group"] == c["id"]]
    tl = {int(k): v for k, v in t["spec"]["world"]["constants"]["market"]["listings"].items()}
    assert min(tl, key=tl.get) == 11301 and tl[11301] < tl[11303]  # still the lowest sticker in the trap ...
    assert sp.play(t["spec"], CK.battery(t["spec"])["sticker_lo"])["score"] == 0  # ... and an overpay there
    for name in ("sticker_lo", "sticker_hi", "buy_cheapest", "bulk_cheapest"):
        assert sum(sp.play(x["spec"], CK.battery(x["spec"])[name])["pass"] for x in (c, t)) <= 1, name
