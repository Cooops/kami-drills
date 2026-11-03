"""kami-harness 4.7.0 adapter: surface, tool -> intent table, answers, quirks, invariance, Inspect end to end."""
import copy
import glob
import json
import os
from pathlib import Path

import pytest
from inspect_ai import eval as inspect_eval
from inspect_ai.model import ModelOutput, get_model

from kami_drills import spec as sp
from kami_drills import task as kd
from kami_drills.adapters import harness_4_7 as h
from kami_drills.spec import check as ck
from test_spec import SPECS, synthetic

S1 = Path(os.environ.get("KAMIARENA_S1", "/nonexistent"))  # Season 1 run directories (not public); tests needing them skip


def test_surface_is_the_live_season1_list():
    names = [n for n, _, _ in h.surface()]
    assert len(names) == 107 == len(set(names))  # 109 harness tools - chat_send - 9 OUTSOURCE + 8 scaffold
    assert "chat_send" not in names and not kd.OUTSOURCE & set(names)
    assert set(kd.SCAFFOLD) <= set(names)


def V(intents):
    return [(i["verb"], i["args"], i.get("dry_run", False)) for i in intents]


@pytest.mark.parametrize("tool,args,want", [
    ("harvest_stop", {"kami_ids": [1, 2]}, [("harvest_stop", {"kami": 1}, False), ("harvest_stop", {"kami": 2}, False)]),
    ("act_sequence", {"steps": [{"op": "feed", "kami_id": 1, "item_id": 11301}, {"op": "harvest_stop", "kami_ids": [1]}]},
     [("feed", {"kami": 1, "item": 11301}, False), ("harvest_stop", {"kami": 1}, False)]),
    ("pool_swap", {"item_in": 1, "item_out": 1005, "amount_in": 20, "min_amount_out": 2, "dry_run": True},
     [("quote", {"of": "swap", "item_in": 1, "item_out": 1005, "amount_in": 20, "min_out": 2}, True)]),
    ("harvest_start", {"kami_ids": [9], "node_index": 29, "dry_run": True}, [("quote", {"of": "harvest_start", "kami": 9, "node": 29}, True)]),
    ("pool_swap_quote", {"item_in": 1001, "item_out": 1, "amount_in": 5}, [("quote", {"of": "swap", "item_in": 1001, "item_out": 1, "amount_in": 5}, False)]),
    ("move_to_room", {"room_index": 12}, [("travel", {"room": 12, "hop": True}, False)]),  # one hop: adjacency is checked
    ("travel_to_room", {"target_room": 12, "use_items": True}, [("travel", {"room": 12}, False)]),
    ("level_and_allocate_batch", {"targets": []}, []),
    ("level_and_allocate_batch", {"targets": [{"kami_id": 5, "skill_plan": [{"skill_index": 110, "points": 2}]}]},
     [("upgrade_skill", {"kami": 5, "skill": 110}, False)] * 2),
    ("set_next_wake", {"minutes_from_now": 2}, [("schedule_wake", {"minutes": 5}, False)]),
    ("set_next_wake", {"minutes_from_now": 9999}, [("schedule_wake", {"minutes": 1440}, False)]),
    ("wait", {"seconds": 900}, [("wait", {"seconds": 300}, False)]),
    ("lens_kami", {"kami_index": 7, "at_least_block": 5}, [("read", {"kind": "kami", "kami": 7}, False)]),
    ("portal_deposit", {"item": 100, "amount": 1}, [("portal_deposit", {"item": 100, "amount": 1}, False)]),
    ("listing_buy", {"merchant_index": 1, "item_indices": [11001], "amounts": [2]}, [("buy", {"item": 11001, "qty": 2, "venue": "merchant:1"}, False)]),
    ("burn_items", {"item_indices": [1], "amounts": [1]}, [("other_act", {"tool": "burn_items"}, False)]),
    ("newbie_vendor_buy", {"kami_index": 3149, "max_price_eth": "0.006"},
     [("buy", {"item": "kami", "qty": 1, "venue": "newbie_vendor", "max_unit_price": "0.006", "kami": 3149}, False)]),
    ("buy_kami", {"kami_ids": [7, 8], "max_total_eth": "0.01"},  # the cap is a total: per unit = total / qty
     [("buy", {"item": "kami", "qty": 2, "venue": "kamiswap", "kami": [7, 8], "max_total_price": "0.01", "max_unit_price": "0.005"}, False)]),
    ("search_reference", {"query": "x"}, [("unknown", {"tool": "search_reference"}, False)]),
])
def test_tool_to_intent(tool, args, want):
    assert V(h.to_intents(tool, args)) == want


def test_use_item_batch_is_state_aware():
    sim = sp.Simulator(synthetic())  # kami 1 is DEAD
    assert V(h.to_intents("use_item_batch", {"kami_id": 1, "item_id": 11002, "count": 1}, sim)) == [("revive", {"kami": 1, "method": "melkarth_spell_card"}, False)]
    sim.state["kamis"][0]["state"] = "RESTING"
    assert V(h.to_intents("use_item_batch", {"kami_id": 1, "item_id": 11002, "count": 1}, sim))[0][0] == "use_item"


@pytest.mark.parametrize("sid", sorted(SPECS))
def test_reference_plays_round_trip(sid):
    for p in SPECS[sid]["scoring"]["reference_plays"]:
        calls = [h.from_intent(i) for i in p["intents"]]
        assert None not in calls
        back = [i for t, a in calls for i in h.to_intents(t, a)]
        clamp = lambda i: {**i, "args": {"minutes": max(i["args"]["minutes"], 5)}} if i["verb"] == "schedule_wake" else i  # 4.7.0 wake floor
        assert V(back) == V([clamp({**i, "args": i.get("args", {})}) for i in p["intents"]])


def test_gap_reply_is_neutral():
    ep = h.Episode(SPECS["risk-starving-feed-first"])
    text = ep.call("get_scavenge_points", {"node_index": 99})
    assert "drill" not in text.lower() and "503" in text and ep.sim.gaps
    act = ep.call("harvest_start", {"kami_ids": [21340], "node_index": 29})  # an act with no row
    assert act == h.GAP_TEXT.format(tool="harvest_start") and ep.misses[-1] == "harvest_start"


def test_answers_use_native_text_then_4_7_templates():
    ep = h.Episode(SPECS["risk-starving-feed-first"])
    stop = ep.call("harvest_stop", {"kami_ids": [21340]})
    assert stop.endswith("'revert: kami starving..: Reverted'}")  # recorded 4.7.0 text, verbatim
    fed = json.loads(ep.call("feed_kami", {"kami_id": 21340, "food_item_id": 11301}))
    assert fed["status"] == "success" and fed["hp_after"] == 25
    again = ep.call("feed_kami", {"kami_id": 21340, "food_item_id": 11301})
    assert again.startswith("Error executing tool feed_kami: validation failed; no transaction sent:")
    inv = json.loads(ep.call("lens_inventory", {}))["data"]["items"]  # read from the simulated state, after the feed
    assert [r["item"]["index"] for r in inv] == [1]


def test_reads_follow_the_simulated_clock():
    ep = h.Episode(SPECS["stale-cooldown-retry-start"])
    cd = lambda: json.loads(ep.call("lens_kami", {"kami_index": 9787}))["data"]["cooldownSec"]
    assert cd() == 99
    ep.call("wait", {"seconds": 60})
    assert cd() == 39


def test_reason_codes():
    assert h.reason_code("Error executing tool get_gas_balance: 503 Server Error: Service Unavailable for url: x") == "INFRA"
    assert h.reason_code("Error executing tool harvest_start: validation failed; no transaction sent: transaction dry-run reverted: "
                         "{'code': -32000, 'message': 'revert: kami on cooldown: Reverted'}") == "COOLDOWN"
    assert h.reason_code("Error executing tool accept_quest: validation failed; no transaction sent: quest 3900 is already "
                         "completed by account 'main'") == "QUEST_ALREADY_COMPLETE"
    assert h.reason_code("Error executing tool pool_swap: validation failed; no transaction sent: transaction dry-run reverted: "
                         "{'code': -32000, 'message': 'Reverted'}") == "BARE_REVERT"
    assert set(code for _, code in h.REASON_RE) <= set(sp.REASONS)


@pytest.mark.skipif(not S1.exists(), reason="Season 1 run data not present")
def test_reason_code_coverage_on_season1_errors():
    errs = [str(d["error"]) for f in glob.glob(str(S1 / "*/telemetry.jsonl")) for d in map(json.loads, open(f))
            if str(d.get("error") or "").startswith("Error executing tool")]
    other = sum(h.reason_code(e) == "OTHER" for e in errs)
    assert len(errs) > 1000 and other / len(errs) < 0.02, other / len(errs)


def test_accept_quest_quirk_marks_the_drill_harness_bound():
    """4.7.0 refuses to re-accept an owned repeatable quest (3900) that the chain accepts: a drill whose oracle needs
    the re-accept is not expressible on this adapter, and the quirk is named."""
    s = synthetic()
    s["state"]["quests"][0]["status"] = "complete"  # done once; repeatable and ready again on chain
    s["content_hash"] = sp.content_hash(s)
    ref = {p["name"]: sp.play(s, p["intents"]) for p in s["scoring"]["reference_plays"]}
    assert ref["oracle"]["pass"]  # the game allows it ...
    inv = ck.invariance(s, h, ref)
    assert inv["status"] == "not_expressible" and "accept-owned-repeatable" in inv["quirks"]  # ... the harness does not
    assert ck.invariance(synthetic(), h, {p["name"]: sp.play(synthetic(), p["intents"]) for p in synthetic()["scoring"]["reference_plays"]})["status"] == "ok"


def test_harness_bound_drill_is_not_rendered_on_another_harness():
    s = SPECS["repeat-pool-swap-retry"]
    assert s["harness"]["bound"] and h.render_mode(s) is None
    assert ck.check(s, [h])["adapters"][0]["status"] == "rendering_changed"


def test_empty_batches_are_refused_and_score_nothing():
    ep = h.play_tools(SPECS["risk-starving-feed-first"], [("level_and_allocate_batch", {"targets": []}), ("harvest_stop", {"kami_ids": []})])
    assert ep.sim.trace == [] and not ep.result()["pass"]


def test_snapshot_brief_rendering():
    msgs = h.render_fork(synthetic(), "snapshot_brief")
    calls = [tc.function for m in msgs for tc in getattr(m, "tool_calls", None) or []]
    assert calls == ["lens_roster", "get_gas_balance"] and msgs[1].content == "Session start."
    roster = json.loads(msgs[3].content)["data"]["kamis"]
    assert roster[0]["state"] == "DEAD"


M = "mockllm/model"


def run(spec_id, calls, tmp_path):
    outs = [*(ModelOutput.for_tool_call(M, t, a) for t, a in calls), ModelOutput.from_content(M, "done")]
    [log] = inspect_eval(h.spec_v2(paths="drills_v2/spec"), model=get_model(M, custom_outputs=outs), sample_id=spec_id,
                         log_dir=str(tmp_path), display="none")
    assert log.status == "success" and not log.samples[0].error, log.error
    return next(iter(log.samples[0].scores.values())).metadata["pass"]


def test_inspect_end_to_end(tmp_path):
    oracle = [h.from_intent(i) for i in SPECS["risk-starving-feed-first"]["scoring"]["reference_plays"][0]["intents"]]
    assert run("risk-starving-feed-first", oracle, tmp_path)
    assert not run("risk-starving-feed-first", [("harvest_stop", {"kami_ids": [21340]})], tmp_path)
    assert run("stale-cooldown-retry-start", [("wait", {"seconds": 60}), ("wait", {"seconds": 45}),
                                              ("harvest_start", {"kami_ids": [9787], "node_index": 29})], tmp_path)
    assert not run("stale-cooldown-retry-start", [], tmp_path)


def test_kamiswap_buy_round_trips_and_native_any_text_is_served_unless_bound():
    buy = h.to_intents("buy_kami", {"kami_ids": [11242], "max_total_eth": "0.0045"})[0]
    assert buy["args"]["kami"] == 11242 and h.from_intent(buy) == ("buy_kami", {"kami_ids": [11242], "max_total_eth": "0.0045"})
    assert h.from_intent({"verb": "buy", "args": {"item": "kami", "venue": "kamiswap", "max_unit_price": "0.005"}}) is None  # no ids
    s = copy.deepcopy(SPECS["risk-starving-feed-first"])
    row = next(r for r in s["responses"] if r["result"].get("native"))  # the starving stop revert, recorded on 4.7.0
    row["result"]["native"] = {sp.NATIVE_ANY: "recorded on another harness"}
    assert h.Episode(s).call("harvest_stop", {"kami_ids": [21340]}) == "recorded on another harness"
    s["harness"]["bound"] = True
    assert h.Episode(s).call("harvest_stop", {"kami_ids": [21340]}).startswith("Error executing tool harvest_stop:")
