"""kami-drills v1.1 (specs/v1.1): the 47 v1 drills as v2 specs with the B-audit fixes (docs/v1.1.md).
Every ranking spec must validate, keep its three plays (oracle passes, null and source mistake fail) and fail the whole
degenerate battery: check.battery (mistake plays from the authored mistake on success forks; timing items exempt only
through their trailing wait + act) and the 4.7.0 native battery."""
import json
from pathlib import Path

import pytest

from kami_drills import spec as sp
from kami_drills.adapters import harness_4_7 as h
from kami_drills.spec import check as ck
from kami_drills.spec import from_v1

ROOT = Path(__file__).parent.parent
DIR = ROOT / "specs" / "v1.1"
MANIFEST = json.loads((DIR / "MANIFEST.json").read_text())
ROWS = {m["id"]: m for m in MANIFEST["drills"]}
SPECS = {i: json.loads((DIR / f"{i}.json").read_text()) for i in ROWS}
RANKING = sorted(i for i, m in ROWS.items() if m["role"] == "ranking")


def source_mistake(spec):
    return ck.mistake(spec)  # the recorded mistake, else a success fork's authored one


def gate(spec):
    """Battery-gate failures for one spec ([] = may rank): check.check's battery (with the timing exemption) and the
    4.7.0 native battery."""
    return ck.check(spec, [h])["failures"]


def test_manifest_covers_every_v1_drill():
    v1 = sorted(p.stem for p in (ROOT / "drills").glob("*.json") if not p.stem.startswith("_"))
    assert sorted(ROWS) == v1 and len(v1) == 47
    assert sum(MANIFEST["counts"].values()) == 47
    assert MANIFEST["counts"] == {r: sum(m["role"] == r for m in ROWS.values()) for r in ("ranking", "smoke", "retired")}
    for i, m in ROWS.items():
        assert SPECS[i]["content_hash"] == m["content_hash"] == sp.content_hash(SPECS[i])
        assert (SPECS[i]["status"] == "retired") == (m["role"] == "retired") == (SPECS[i]["role"] == "retired")
        assert SPECS[i]["role"] == m["role"] and SPECS[i].get("scoring", {}).get("timing_item", False) == m.get("timing_item", False)
        assert m["role"] == "ranking" or m.get("reason"), i


@pytest.mark.parametrize("sid", sorted(ROWS))
def test_every_spec_validates_and_is_from_v1(sid):
    s = SPECS[sid]
    assert sp.validate(s) == []
    v1 = json.loads((ROOT / "drills" / f"{sid}.json").read_text())
    assert from_v1.convert(v1, s) == s  # from-v1 output, untouched since
    assert len(s["fork"]["native_prefix"]["messages"]) == len(v1["prefix"])  # verbatim, then pseudonymized
    assert s["decision_point"]["closing"].startswith("Operator goal: ")  # stated objective, never inferred


@pytest.mark.parametrize("sid", RANKING)
def test_ranking_three_plays(sid):
    s = SPECS[sid]
    assert sp.play(s, next(p for p in s["scoring"]["reference_plays"] if p["name"] == "oracle")["intents"])["pass"]
    assert not sp.play(s, [])["pass"]
    assert source_mistake(s) and not sp.play(s, source_mistake(s))["pass"]


@pytest.mark.parametrize("sid", RANKING)
def test_ranking_passes_the_battery_gate(sid):
    assert gate(SPECS[sid]) == []


@pytest.mark.parametrize("sid", RANKING)
def test_ranking_dry_run_probes_are_never_the_mistake(sid):
    """B audit: a dry run sends nothing, so probing the recorded mistake (then doing the oracle) must still pass."""
    s = SPECS[sid]
    oracle = next(p for p in s["scoring"]["reference_plays"] if p["name"] == "oracle")["intents"]
    probe = [{"verb": "quote", "args": {"of": i["verb"], **i.get("args", {})}, "dry_run": True} for i in source_mistake(s) if i["verb"] not in sp.PASSIVE]
    assert sp.play(s, probe + oracle)["pass"]


def test_move_to_room_is_one_step_and_travel_to_room_pathfinds():
    """v1's forbid on a one-step move_to_room to non-adjacent room 1 is expressible (travel hop); the legal path passes."""
    s = SPECS["onboard-quest2-move-three"]
    rest = [("complete_quest", {"quest_index": 2}), ("accept_quest", {"quest_index": 3})]
    assert h.play_tools(s, [("travel_to_room", {"target_room": 1}), *rest]).result()["pass"]
    assert not h.play_tools(s, [("move_to_room", {"room_index": 1}), ("travel_to_room", {"target_room": 1}), *rest]).result()["pass"]


def test_vendor_buy_keeps_the_kami_index():
    s = SPECS["onboard-first-kami-vendor"]
    read, done = ("lens_merchant", {"npc_index": -1}), ("complete_quest", {"quest_index": 3})
    assert h.play_tools(s, [read, ("newbie_vendor_buy", {"kami_index": 3149, "max_price_eth": "0.006"}), done]).result()["pass"]
    guess = h.play_tools(s, [read, ("newbie_vendor_buy", {"kami_index": 8757, "max_price_eth": "0.006"}),
                             ("newbie_vendor_buy", {"kami_index": 3149, "max_price_eth": "0.006"}), done]).result()
    assert not guess["pass"] and "guess" in "".join(guess["why"])  # an index that is not on display
