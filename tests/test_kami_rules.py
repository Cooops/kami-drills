"""kami-rules (spec/rules.py): the game refusals the one scorer predicts from simulated state, and the check_bank gate."""
import copy

import pytest

from kami_drills import spec as sp
from kami_drills.adapters import harness_4_7 as h
from kami_drills.spec import check as ck
from kami_drills.spec import rules as R

T = 1_790_000_000.0


def spec(kamis, room=3, inventory=None, quests=None, rule=None, oracle=(), constants=None, lesser=None, mistake=None, **world):
    s = {"spec_version": "2.0.0", "id": "test-rules", "version": 1, "family": "decision", "competency": ["state_tracking"],
         "mistake_class": "stale_state", "tier": "A", "situation": "a synthetic rules-library test", "objective": {"game": "g", "drill": "d"},
         "fork": {"kind": "session_start", "rendering": ["snapshot_brief"], "clock": {"unix": T, "iso": "2026-09-21T00:00:00+00:00"}},
         "state": {"account": {"index": 1, "room": room}, "kamis": kamis, "inventory": inventory or {}, **({"quests": quests} if quests else {})},
         "world": {"rules_library": R.NAME, "constants": {"call_seconds": 2, **(constants or {})}, **world},
         "decision_point": {"closing": None, "budget": {"tool_calls": 50}}, "responses": [],
         "scoring": {"kind": "rule", "rule": rule or {"require_any": [{"verb": "end_session"}]}, "pass_threshold": 0.8,
                     "refusal_penalty": {"amount": 0.15}, "flag_caps": [{"flag": "cooldown_band", "max": 0.8}],
                     "null_play": {"policy": "none", "expected": 0},
                     "reference_plays": [{"name": "oracle", "intents": list(oracle), "expected": 1}]
                     + ([{"name": "recorded-mistake", "is_recorded_mistake": True, "intents": mistake, "expected": 0}] if mistake else [])},
         "state_dependencies": [{"path": "/state", "why": "t", "source": "t"}], "harness": {"bound": False, "recorded_on": {"harness": "x"}},
         "provenance": {"credit": "t", "license": "CC-BY-4.0", "source": {"dataset": "t"}}, "heldout": {"flag": False, "method": "hash_split"}}
    if lesser:
        s["scoring"]["lesser"] = lesser
    return s


def K(idx=7, state="RESTING", hp=50, hp_max=100, **kw):
    return {"index": idx, "state": state, "hp": hp, "hp_max": hp_max, **kw}


def I(verb, **a):
    return {"verb": verb, "args": a}


def results(s, intents):
    sim = sp.Simulator(s).run(intents)
    return [(i["verb"], i["result"]["class"], i["result"].get("reason")) for i in sim.trace], sim


def test_level_up_is_refused_while_harvesting_and_when_not_ready():
    s = spec([K(state="HARVESTING", harvest={"node": 3, "room": 3}, level_up_ready=True), K(8, level_up_ready=False)])
    got, _ = results(s, [I("level_up", kami=7), I("level_up", kami=8)])
    assert got == [("level_up", "revert", "KAMI_NOT_RESTING"), ("level_up", "revert", "LEVEL_REQS")]
    # F review: "harvest_start, then level_up" passed 4/5 level-up items; the chain refuses the level-up
    s = spec([K(level_up_ready=True, level=5)], rule={"require_any": [{"verb": "level_up", "args": {"kami": 7}, "ok": True}]})
    assert not sp.play(s, [I("harvest_start", kami=7, node=3), I("level_up", kami=7)])["pass"]
    assert sp.play(s, [I("level_up", kami=7), I("harvest_start", kami=7, node=3)])["pass"]


def test_node_level_caps_come_from_the_gdd():
    assert R.NODE_CAP[55] == 15 and R.NODE_CAP[31] == 35 and R.NODE_CAP.get(12) is None  # gdd nodes.md; harness csv leaves 55 blank
    s = spec([K(level=16), K(8, level=15)], room=55)
    got, _ = results(s, [I("harvest_start", kami=7, node=55), I("harvest_start", kami=8, node=55)])
    assert got == [("harvest_start", "revert", "LEVEL_REQS"), ("harvest_start", "ok", None)]


def test_move_needs_an_adjacent_room_and_travel_needs_a_path():
    assert 29 in R.ADJ[1] and 30 not in R.ADJ[1]
    s = spec([K()], room=1)
    got, sim = results(s, [I("travel", room=30, hop=True), I("travel", room=29, hop=True), I("travel", room=29)])
    assert got == [("travel", "validation_error", "ROOM_UNREACHABLE"), ("travel", "ok", None), ("travel", "validation_error", "WRONG_ROOM")]
    got, sim = results(spec([K()], room=1), [I("travel", room=30)])  # travel_to_room pathfinds
    assert got == [("travel", "ok", None)] and sim.state["account"]["room"] == 30
    assert h.to_intents("move_to_room", {"room_index": 30}) == [{"verb": "travel", "args": {"room": 30, "hop": True}}]
    assert h.from_intent(I("travel", room=30, hop=True)) == ("move_to_room", {"room_index": 30})


@pytest.mark.parametrize("method,inv,ok", [("onyx", {"100": 33}, True), ("onyx", {"100": 32}, False), ("pale_potion", {"11004": 1}, True),
                                           ("djed_pillar", {"11003": 1}, True), ("red_ribbon_gummy", {}, False)])
def test_revive_methods_need_their_item(method, inv, ok):
    got, sim = results(spec([K(state="DEAD", hp=0)], inventory=inv), [I("revive", kami=7, method=method)])
    assert (got[0][1] == "ok") == ok
    if ok:
        assert sim.state["kamis"][0]["state"] == "RESTING" and sim.state["kamis"][0]["hp"] == {"onyx": 33, "pale_potion": 75, "djed_pillar": 5}[method]


def test_foods_are_kami_foods_and_held():
    assert 21204 not in R.KAMI_FOODS and 11301 in R.KAMI_FOODS  # 21201-21204 are account (stamina) foods
    s = spec([K()], inventory={"21204": 3, "11301": 1})
    got, sim = results(s, [I("feed", kami=7, item=21204), I("feed", kami=7, item=11301), I("feed", kami=7, item=11301)])
    assert got == [("feed", "validation_error", "BAD_ARGS"), ("feed", "ok", None), ("feed", "validation_error", "INSUFFICIENT_ITEM")]
    assert sim.state["kamis"][0]["hp"] == 75


def test_harvest_room_starving_and_state_gates():
    s = spec([K(state="HARVESTING", harvest={"node": 9, "room": 9}), K(8, state="HARVESTING", hp=0, harvest={"node": 3}), K(9)])
    got, _ = results(s, [I("harvest_stop", kami=7), I("harvest_stop", kami=8), I("harvest_collect", kami=9), I("harvest_start", kami=9, node=4)])
    assert got == [("harvest_stop", "revert", "KAMI_TOO_FAR"), ("harvest_stop", "revert", "KAMI_STARVING"),
                   ("harvest_collect", "revert", "KAMI_NOT_HARVESTING"), ("harvest_start", "revert", "WRONG_ROOM")]


def test_collect_sets_the_cooldown_that_refuses_the_stop():
    """Collect resets the cooldown, so a stop right after it is refused for 180 s; stop alone collects anyway."""
    s = spec([K(state="HARVESTING", harvest={"node": 3, "room": 3, "accrued_musu": 40})])
    got, sim = results(s, [I("harvest_collect", kami=7), I("harvest_stop", kami=7)])
    assert got == [("harvest_collect", "ok", None), ("harvest_stop", "revert", "COOLDOWN")] and "early_retry" in sim.flags
    got, sim = results(s, [I("harvest_stop", kami=7)])
    assert got == [("harvest_stop", "ok", None)] and sim.state["kamis"][0]["state"] == "RESTING"


def test_quests_completable_only_when_met_and_not_contradicted():
    s = spec([K()], quests=[{"index": 1, "status": "objectives_met"}, {"index": 2, "status": "active"},
                            {"index": 3, "status": "objectives_met", "requirements_met": False}])
    got, _ = results(s, [I("complete_quest", quest=q) for q in (1, 2, 3, 4)] + [I("complete_quest", quest=1)])
    assert [g[2] for g in got] == [None, "QUEST_OBJS_NOT_MET", "QUEST_REQS_NOT_MET", "QUEST_NOT_ACCEPTED", "QUEST_ALREADY_COMPLETE"]


def test_token_deposit_is_bounded_by_the_declared_balance():
    s = spec([K()], constants={"portal_tokens": {"100": {"balance": "/gas/owner_eth", "unit_eth": 1e-4}}})
    s["state"]["gas"] = {"owner_eth": "0.0005"}
    got, sim = results(s, [I("portal_deposit", item=100, amount=6), I("portal_deposit", item=100, amount=5)])
    assert got == [("portal_deposit", "validation_error", "TOKEN_BALANCE"), ("portal_deposit", "ok", None)]
    assert sim.state["inventory"]["100"] == 5 and float(sim.state["gas"]["owner_eth"]) == 0
    assert results(spec([K()]), [I("portal_deposit", item=100, amount=1)])[0] == [("portal_deposit", "gap", None)]  # undeclared: no opinion


def test_cooldown_band_is_calibrated_and_item_evidence_bounds_it():
    until = T + 100
    act = I("harvest_stop", kami=7)
    rule = {"require_any": [{"verb": "harvest_stop", "args": {"kami": 7}, "ok": True}], "forbid_flags": ["early_retry"]}
    s = spec([K(state="HARVESTING", harvest={"node": 3}, cooldown_until_unix=until)], rule=rule)
    at = lambda off: [I("wait", seconds=until + off - T - 2), act]  # the act lands 2 s (call_seconds) after the wait
    assert sp.play(s, at(-5))["score"] == 0  # visible cooldown: early_retry
    assert sp.play(s, at(2))["score"] == 0.85 - 0.85  # refused, not flagged, but no retry: the rule fails
    assert sp.play(s, at(2) + [act])["score"] == pytest.approx(0.8 - 0.15)  # refused once, retried inside the band
    assert sp.play(s, at(R.ACCEPT_FROM_S + 1))["score"] == 0.8  # accepted inside the band where S1 also refused
    assert sp.play(s, at(R.FULL_FROM_S + 1))["score"] == 1.0
    # the item's own record: refused at +17 s (grok-4.7-3 s28), accepted at +13 s (grok-4.7-3 s22)
    s2 = copy.deepcopy(s)
    s2["world"]["constants"]["cooldown_evidence"] = {"7": {"refused_at": until + 17}}
    assert sp.play(s2, at(15))["score"] == 0 and sp.play(s2, at(19))["score"] == 0.8
    s3 = copy.deepcopy(s)
    s3["world"]["constants"]["cooldown_evidence"] = {"7": {"accepted_at": until + 13}}
    assert sp.play(s3, at(14))["score"] == 1.0


def test_clock_dependent_reads_show_the_wait_elapse():
    s = spec([K(state="HARVESTING", harvest={"node": 3}, cooldown_until_unix=T + 100)])
    ep = h.Episode(s)
    assert '"cooldownSec": 98' in ep.call("lens_kami", {"kami_index": 7})  # each call takes 2 s of game time
    ep.call("wait", {"seconds": 60})
    assert '"cooldownSec": 36' in ep.call("lens_kami", {"kami_index": 7})
    assert str(int(T + 66)) in ep.call("get_status", {})


def test_refusal_penalty_and_lesser_credit():
    rule = {"require_any": [{"verb": "level_up", "args": {"kami": 7}, "ok": True}]}
    s = spec([K(level_up_ready=True), K(8, level_up_ready=False)], rule=rule,
             lesser=[{"credit": 0.5, "rule": {"require_any": [{"verb": "schedule_wake", "args": {"minutes": {"lte": 10}}}]}}])
    assert sp.play(s, [I("level_up", kami=7)])["score"] == 1.0
    assert sp.play(s, [I("level_up", kami=8), I("level_up", kami=7)])["score"] == pytest.approx(0.85)  # one fair try: still a pass
    assert not sp.play(s, [I("level_up", kami=8), I("level_up", kami=8), I("level_up", kami=7)])["pass"]
    assert sp.play(s, [I("schedule_wake", minutes=5)])["score"] == 0.5


def test_kitchen_sink_fails_once_refusals_cost():
    """Why spec and the miner disagreed: unmatched acts were gaps (free) and accepted acts were unconditional ok rows,
    so trying everything was free. With kami-rules the sink's refused acts cost, and the right act succeeds only when
    the state allows it."""
    rule = {"require_any": [{"verb": "level_up", "args": {"kami": 7}, "ok": True}]}
    s = spec([K(level_up_ready=True, skill_points=0), K(8, state="DEAD", hp=0)], rule=rule, oracle=[I("level_up", kami=7)],
             mistake=[I("end_session")], inventory={"11301": 1})
    bat = ck.battery(s)
    assert not sp.play(s, bat["kitchen_sink"])["pass"]
    assert sp.play(s, bat["always_level"])["score"] == pytest.approx(0.85)  # one refusal: a constant policy still passes -> needs a control
    no_lib = copy.deepcopy(s)
    no_lib["world"].pop("rules_library")
    no_lib["responses"] = [{"match": {"verb": "level_up", "args": {"kami": 7}}, "result": {"class": "ok"}}]
    assert sp.play(no_lib, bat["kitchen_sink"])["pass"]  # the old converter's shape: the sink passed


def test_check_bank_gates_a_constant_policy_over_the_group():
    rule = {"require_any": [{"verb": "level_up", "args": {"kami": 7}, "ok": True}]}
    item = spec([K(level_up_ready=True)], rule=rule, oracle=[I("level_up", kami=7)], mistake=[I("end_session")])
    ctl = spec([K(level_up_ready=False)], rule={"forbid": [{"verb": "level_up", "args": {"kami": 7}}]}, mistake=None)
    ctl.update(id="test-rules-ctl", mistake_class="none")
    ctl["scoring"]["null_play"]["expected"] = 1
    ctl["variants"] = {"origin": "trap_control", "group": "g"}
    assert "lazy pass: always_level" in ck.check(item)["failures"]  # alone, "level every kami" passes
    item["variants"] = {"origin": "genuine", "group": "g"}
    reps = ck.check_bank([item, ctl])
    assert reps[0]["failures"] == [] and reps[1]["failures"] == []  # 1 of 2: at most half the group
    ctl2 = copy.deepcopy(ctl)
    ctl2["state"]["kamis"][0]["level_up_ready"] = True
    ctl2["scoring"]["rule"] = {"require_any": [{"verb": "end_session"}]}
    reps = ck.check_bank([item, ctl2])  # a control the play does not fail: 2 of 2
    assert any(f.startswith("lazy pass: always_level") for f in reps[0]["failures"])
