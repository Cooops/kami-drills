"""v2 drill spec: schema, simulator, scoring, battery and the offline check (docs/v2-spec.md)."""
import copy
import json
from pathlib import Path

import pytest

from kami_drills import spec as sp
from kami_drills.spec import check as ck

ROOT = Path(__file__).parent.parent
SPECS = {s["id"]: s for s in sp.load([ROOT / "drills_v2" / "spec"])}


def synthetic(**over):
    """A small synthetic poverty-recovery drill (no run data): partial credit, penalties, a repeatable quest."""
    s = {
        "spec_version": "2.0.0", "id": "synthetic-poverty", "version": 1, "family": "poverty_recovery",
        "competency": ["recovery"], "mistake_class": "poverty_stall", "tier": "A",
        "situation": "Synthetic: one DEAD kami, 2 of 3 sticks, quest 900 open.",
        "objective": {"game": "complete as many quests as possible", "drill": "revive via the quest"},
        "fork": {"kind": "session_start", "rendering": ["snapshot_brief"], "clock": {"unix": 1000, "block": 1}},
        "state": {"account": {"index": 7, "room": 26, "musu": 50}, "kamis": [{"index": 1, "state": "DEAD", "hp": 0, "hp_max": 50}],
                  "inventory": {"1": 50, "1001": 2}, "quests": [{"index": 900, "status": "available", "repeatable": True}]},
        "decision_point": {"budget": {"tool_calls": 50}},
        "responses": [
            {"match": {"verb": "accept_quest", "args": {"quest": 900}}, "unless": "acc", "sets": "acc", "result": {"class": "ok"}},
            {"match": {"verb": "swap", "args": {"item_out": 1001}}, "sets": "sticks", "result": {"class": "ok",
             "state_delta": [{"path": "/inventory/1001", "op": "add", "value": 1}]}},
            {"match": {"verb": "swap", "args": {"item_in": 1001}}, "result": {"class": "ok"}},
            {"match": {"verb": "complete_quest", "args": {"quest": 900}}, "when": ["acc", "sticks"], "sets": "card", "result": {"class": "ok"}},
            {"match": {"verb": "complete_quest"}, "result": {"class": "revert", "reason": "QUEST_OBJS_NOT_MET"}},
            {"match": {"verb": "revive", "args": {"method": "melkarth_spell_card"}}, "when": "card", "sets": "revived", "result": {"class": "ok"}},
            {"match": {"verb": "revive"}, "result": {"class": "validation_error", "reason": "INSUFFICIENT_ITEM"}}],
        "scoring": {"kind": "partial_credit",
                    "components": [{"id": "sticks", "weight": 1, "rule": {"require_flags": ["sticks"]}},
                                   {"id": "quest", "weight": 2, "rule": {"require_flags": ["card"]}},
                                   {"id": "revive", "weight": 2, "rule": {"require_flags": ["revived"]}}],
                    "penalties": [{"id": "sell", "amount": 1, "zero_score": True, "match": {"verb": "swap", "args": {"item_in": 1001}, "ok": True}},
                                  {"id": "revert", "amount": 0.1, "per": "each", "cap": 0.3, "match": {"verb": ["complete_quest", "revive"], "ok": False}}],
                    "pass_threshold": 1, "null_play": {"policy": "no calls", "expected": 0},
                    "reference_plays": [
                        {"name": "oracle", "expected": 1, "intents": [
                            {"verb": "accept_quest", "args": {"quest": 900}}, {"verb": "swap", "args": {"item_in": 1, "item_out": 1001, "amount_in": 4, "min_out": 1}},
                            {"verb": "complete_quest", "args": {"quest": 900}}, {"verb": "revive", "args": {"kami": 1, "method": "melkarth_spell_card"}}]},
                        {"name": "recorded-mistake", "is_recorded_mistake": True, "expected": 0,
                         "intents": [{"verb": "read", "args": {"kind": "merchant"}}, {"verb": "schedule_wake", "args": {"minutes": 60}}]}]},
        "state_dependencies": [{"path": "/state/inventory/1001", "why": "2 of 3 sticks"}],
        "variants": {"origin": "synthetic_param"},
        "harness": {"bound": False, "recorded_on": {"harness": "kami-harness 4.7.0 df7129f"}},
        "provenance": {"credit": "synthetic", "license": "CC-BY-4.0", "source": {"dataset": "synthetic"}},
        "heldout": {"flag": False, "method": "hash_split"},
    }
    for path, value in over.items():
        parent, key = sp.pointer(s, path)
        parent[key] = value
    return s


def I(verb, **args):
    return {"verb": verb, "args": args}


def test_vocabulary_comes_from_the_schema():
    assert {"feed", "revive", "harvest_start", "harvest_stop", "harvest_collect", "travel", "accept_quest", "complete_quest",
            "level_up", "upgrade_skill", "wait", "schedule_wake", "swap", "portal_deposit", "buy", "end_session", "read"} <= set(sp.VERBS)


@pytest.mark.parametrize("sid", sorted(SPECS))
def test_converted_specs_validate_and_hash(sid):
    s = SPECS[sid]
    assert sp.validate(s) == []
    assert s["content_hash"] == sp.content_hash(s)
    tampered = copy.deepcopy(s)
    tampered["state"]["account"]["room"] += 1
    assert any("content_hash" in e for e in sp.validate(tampered))


@pytest.mark.parametrize("path,value,needle", [
    ("/tier", "B", "model"),  # Tier B without a declared model
    ("/heldout", {"flag": True, "method": "sealed"}, "sealed_hash"),
    ("/state/kamis/0/state", "ASLEEP", "ASLEEP"),
    ("/scoring/components", None, "components"),
])
def test_broken_specs_are_rejected(path, value, needle):
    s = synthetic()
    if value is None:
        del s["scoring"]["components"]
    else:
        parent, key = sp.pointer(s, path)
        parent[key] = value
    assert any(needle in e for e in sp.validate(s)), sp.validate(s)


def test_synthetic_spec_is_valid_and_clean():
    s = synthetic()
    assert sp.validate(s) == []
    r = ck.check(s)
    assert r["failures"] == [], r["failures"]
    assert r["plays"]["oracle"]["score"] == 1 and r["plays"]["recorded-mistake"]["score"] == 0


def test_partial_credit_and_penalties():
    s = synthetic()
    sticks_only = [I("swap", item_in=1, item_out=1001, amount_in=4, min_out=1)]
    assert sp.play(s, sticks_only)["score"] == pytest.approx(1 / 5)
    # two blind completes revert (-0.1 each) before the quest is accepted
    blind = [I("complete_quest", quest=900), I("complete_quest", quest=900), *sticks_only]
    assert sp.play(s, blind)["score"] == pytest.approx(1 / 5 - 0.2)
    many = [I("complete_quest", quest=900)] * 6 + sticks_only
    assert sp.play(s, many)["score"] == 0  # capped at -0.3, floored at 0
    sell = [*s["scoring"]["reference_plays"][0]["intents"], I("swap", item_in=1001, item_out=1, amount_in=1)]
    r = sp.play(s, sell)
    assert r["score"] == 0 and "zero_score penalty sell" in r["why"]  # selling the input overrides full credit
    s["responses"] = [x for x in s["responses"] if x["match"].get("args") != {"item_in": 1001}]
    assert sp.play(s, sell)["score"] == 1  # a sell that fails (no row: a gap) sold nothing, so no penalty


def test_ok_false_matches_only_failed_intents():
    calls = [sp.to_call({"verb": "revive", "args": {}, "ok": True}), sp.to_call({"verb": "revive", "args": {}, "ok": False})]
    assert [sp.matches(c, {"verb": "revive", "ok": False}) for c in calls] == [False, True]
    assert [sp.matches(c, {"verb": "revive", "ok": True}) for c in calls] == [True, False]
    assert [sp.matches(c, {"verb": "revive"}) for c in calls] == [True, True]


COOL = "stale-cooldown-retry-start"


@pytest.mark.parametrize("intents,passes", [
    ([I("wait", seconds=100), I("harvest_start", kami=9787, node=29)], True),
    ([I("wait", seconds=50), I("read", kind="kami", kami=9787), I("wait", seconds=50), I("harvest_start", kami=9787, node=29)], True),  # split
    ([I("wait", seconds=60), I("harvest_start", kami=9787, node=29)], False),  # too early: early_retry
    ([{"verb": "quote", "args": {"of": "harvest_start", "kami": 9787, "node": 29}, "dry_run": True},
      I("wait", seconds=100), I("harvest_start", kami=9787, node=29)], True),  # a dry-run probe is not a retry (B audit)
    ([I("harvest_start", kami=9787, node=29)], False),
])
def test_waiting_semantics(intents, passes):
    assert sp.play(SPECS[COOL], intents)["pass"] is passes


def test_useful_work_counts_toward_a_wait_when_calls_take_time():
    s = copy.deepcopy(SPECS[COOL])
    s["world"] = {"constants": {"call_seconds": 10}}
    work = [I("read", kind="kami", kami=9787)] * 4  # 40 s of reads
    assert sp.play(s, [*work, I("wait", seconds=60), I("harvest_start", kami=9787, node=29)])["pass"]
    assert not sp.play(SPECS[COOL], [*work, I("wait", seconds=60), I("harvest_start", kami=9787, node=29)])["pass"]


def test_cooldown_rows_skew_window_is_not_flagged():
    s = synthetic()
    s["responses"] = sp.cooldown_rows([{"verb": "harvest_start", "args": {"kami": 1}}], until=1100, skew_s=10)
    s["scoring"] = {"kind": "rule", "rule": {"require_any": [{"verb": "harvest_start", "ok": True}], "forbid_flags": ["early_retry"]},
                    "null_play": {"policy": "no calls", "expected": 0}}
    at = lambda secs: [I("wait", seconds=secs), I("harvest_start", kami=1)]
    assert not sp.play(s, at(50))["pass"]  # before the cooldown: flagged
    r = sp.play(s, at(105))  # inside the skew window: the live game reverts, unflagged, so a retry is fair
    assert not r["pass"] and not r["sim"].flags
    assert sp.play(s, [*at(105), I("wait", seconds=10), I("harvest_start", kami=1)])["pass"]


def test_simulator_session_end_and_state_delta():
    s = synthetic()
    sim = sp.Simulator(s).run([I("schedule_wake", minutes=10), I("accept_quest", quest=900)])
    assert sim.ended and sim.clock == 1600 and len(sim.trace) == 1  # nothing after the session ends
    sim = sp.Simulator(s).run([I("swap", item_out=1001)])
    assert sim.state["inventory"]["1001"] == 3 and s["state"]["inventory"]["1001"] == 2  # the spec is never mutated


def test_dry_run_quote_answers_without_side_effects():
    sim = sp.Simulator(synthetic())
    r = sim.step({"verb": "quote", "args": {"of": "swap", "item_out": 1001}, "dry_run": True})
    assert r["class"] == "ok" and "sticks" not in sim.flags and sim.state["inventory"]["1001"] == 2


def test_battery_on_converted_v1_drills_reports_inherited_lazy_passes():
    """v1 is frozen, so its converted drills keep v1's lazy passes (B audit): the battery must surface them."""
    lazy = {sid: {k for k, v in ck.check(s)["battery"].items() if v} for sid, s in SPECS.items()}
    assert {"wake_5", "wake_60", "wake_1440"} <= lazy[COOL]  # v1: set_next_wake >= 3 min passes
    assert "end_session" in lazy["repeat-pool-swap-retry"]  # v1: require_any includes end_session
    assert "always_feed" in lazy["risk-starving-feed-first"]  # the answer is "feed", so feeding blindly passes (smoke test)
    assert not any(ck.check(s)["battery"]["no_calls"] for s in SPECS.values())


def test_waiting_right_drill_may_pass_time_only_plays():
    s = synthetic()
    s["scoring"]["null_play"]["expected"] = 1
    s["scoring"]["components"] = [{"id": "idle", "weight": 1, "rule": {"forbid": [{"verb": "swap"}]}}]
    s["scoring"]["reference_plays"] = [{"name": "oracle", "expected": 1, "intents": []}]
    s["mistake_class"] = "none"
    r = ck.check(s)
    assert r["battery"]["wake_60"] and not any(f.startswith("lazy pass: wake") for f in r["failures"])


def test_unimplemented_scorer_is_reported_not_crashed():
    s = synthetic()
    s["scoring"]["kind"] = "horizon"
    s["scoring"]["horizon"] = {"metric": "productive_uptime_share"}
    assert any("not implemented" in f for f in ck.check(s)["failures"])


def test_cli_spec_check(capsys):
    from kami_drills import cli
    with pytest.raises(SystemExit) as e:
        cli.main(["spec", "check", str(ROOT / "drills_v2" / "spec" / "risk-starving-feed-first.json")])
    out = capsys.readouterr().out
    # a verdict-equivalent v1 conversion keeps v1's lazy passes on purpose: reported as expected, exit 0 (H review M6)
    assert e.value.code == 0 and "expected" in out and "risk-starving-feed-first" in out
    with pytest.raises(SystemExit) as e:
        cli.main(["spec", "check", "--json", str(ROOT / "drills_v2" / "spec")])
    rep = json.loads(capsys.readouterr().out)
    assert len(rep) == len(SPECS) and any("lazy pass: always_feed" in f for r in rep for f in r["expected"])
    bad = synthetic()  # an ungrouped ranking-style item that a lazy play passes is a real failure: exit 1
    bad["scoring"]["rule"] = {"require_any": [{"verb": "end_session"}]}
    f = ROOT / "tests" / ".tmp-bad-spec.json"
    f.write_text(json.dumps(bad))
    try:
        with pytest.raises(SystemExit) as e:
            cli.main(["spec", "check", str(f)])
        assert e.value.code == 1
    finally:
        f.unlink()
    with pytest.raises(SystemExit) as e:  # a path with no specs never checks green
        cli.main(["spec", "check", str(ROOT / "docs")])
    assert e.value.code != 0


def test_load_follows_a_bank_manifest():
    root = sorted((ROOT / "bank").glob("v2.0-rc*/manifest.json"))[-1].parent
    man = json.loads((root / "manifest.json").read_text())
    assert [s["id"] for s in sp.load([root])] == [e["id"] for e in man["public"]]


# ---------- integration step 2: the v1.1 "needs from spec" ----------

def test_role_and_retired_reason():
    assert sp.validate(synthetic(role="ranking")) == []
    assert any("retired_reason" in e for e in sp.validate(synthetic(role="retired", status="retired")))
    assert sp.validate(synthetic(role="retired", status="retired", retired_reason="no information")) == []
    assert any("go together" in e for e in sp.validate(synthetic(role="smoke", status="retired")))
    assert sp.validate(synthetic(role="bogus"))  # closed enum


def test_load_skips_a_suite_manifest(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps(synthetic()))
    (tmp_path / "MANIFEST.json").write_text(json.dumps({"suite": "x", "drills": []}))
    assert [s["id"] for s in sp.load([tmp_path])] == ["synthetic-poverty"]


def test_any_of_component_takes_the_best_passing_credit():
    comp = {"id": "act_or_wake", "weight": 1, "any_of": [{"rule": {"require_flags": ["revived"]}, "credit": 1},
                                                         {"rule": {"require_any": [{"verb": "schedule_wake"}]}, "credit": 0.5}]}
    s = synthetic(**{"/scoring/components": [comp], "/scoring/penalties": []})
    s["scoring"]["reference_plays"][1]["expected"] = 0.5  # the recorded mistake is a wake
    assert sp.validate(s) == []
    oracle = s["scoring"]["reference_plays"][0]["intents"]
    assert sp.play(s, oracle)["score"] == 1
    assert sp.play(s, [I("schedule_wake", minutes=60)])["score"] == 0.5
    assert sp.play(s, [*oracle, I("schedule_wake", minutes=60)])["score"] == 1  # best, not the sum
    assert sp.play(s, [])["score"] == 0
    both = dict(comp, rule={"require_flags": ["revived"]})
    assert sp.validate(synthetic(**{"/scoring/components": [both]}))  # rule xor any_of


def test_battery_builds_mistake_plays_from_an_authored_mistake():
    s = synthetic(mistake_class="none")
    s["scoring"]["reference_plays"][1] = {"name": "authored-mistake", "is_authored_mistake": True, "expected": 0,
                                          "intents": [I("revive", kami=1, method="onyx")]}
    assert sp.validate(s) == []
    b = ck.battery(s)
    assert b["wait_then_mistake"][1:] == b["mistake_then_wake"][:1] == [I("revive", kami=1, method="onyx")]
    assert b["kitchen_sink"][-1] == I("revive", kami=1, method="onyx")
    assert b["collect_all"] == [{"verb": "harvest_collect", "args": {"kami": 1}}, {"verb": "end_session", "args": {}}]


def test_timing_item_passes_only_through_its_trailing_wait_and_act():
    """A cooldown drill whose mistake (start early) is its oracle after a wait: wait_then_mistake may pass, but only
    because of the wait + act at its end."""
    start = I("harvest_start", kami=1, node=5)
    s = synthetic(**{"/state/kamis/0/state": "RESTING", "/state/kamis/0/hp": 50, "/mistake_class": "repeat_failed",
                     "/responses": sp.cooldown_rows({"verb": "harvest_start", "args": {"kami": 1}}, 1100),
                     "/scoring": {"kind": "rule", "rule": {"require_any": [dict(start, ok=True)], "forbid_flags": ["early_retry"]},
                                  "null_play": {"policy": "no calls", "expected": 0},
                                  "reference_plays": [{"name": "oracle", "expected": 1, "intents": [I("wait", seconds=300), start]},
                                                      {"name": "recorded-mistake", "is_recorded_mistake": True, "expected": 0, "intents": [start]}]}})
    assert "lazy pass: wait_then_mistake" in ck.check(s)["failures"]
    s["scoring"]["timing_item"] = True
    r = ck.check(s)
    assert r["failures"] == [] and r["lazy_ok"]["wait_then_mistake"], r["failures"]
    tail = [I("wait", seconds=300), start]
    assert ck.timing_ok(s, [I("read", kind="party"), *tail])
    assert not ck.timing_ok(s, [*tail, *tail])  # already passes before its tail: the pass is not the wait's
    assert not ck.timing_ok(s, [*tail, I("end_session")])  # the tail must end the play
