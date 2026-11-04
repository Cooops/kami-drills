"""Converters: v1 drill -> spec (verdict-equivalent on recorded rc2 plays) and miner candidate -> spec (best effort)."""
import copy
import json
from pathlib import Path

import pytest

from kami_drills import spec as sp
from kami_drills.adapters import harness_4_7 as h
from kami_drills.spec import check as ck
from kami_drills.spec import from_miner, from_v1
from test_spec import SPECS

ROOT = Path(__file__).parent.parent
FIX = Path(__file__).parent / "fixtures"
PLAYS = json.loads((FIX / "v1_rc2_plays.json").read_text())  # 11 models x 2 tries x 3 drills, tool calls + v1 verdict


@pytest.mark.parametrize("sid", sorted(SPECS))
def test_converted_drill_matches_v1_verdicts_on_recorded_plays(sid):
    tries = [p for p in PLAYS if p["drill"] == sid]
    rows = from_v1.equivalence(SPECS[sid], tries)
    assert len(rows) == 22
    assert [r for r in rows if r["v1"] != r["v2"]] == []


@pytest.mark.parametrize("sid", sorted(SPECS))
def test_mechanical_rule_translation_agrees(sid):
    """v1's own rule translated tool -> intent gives the hand-normalized spec's verdicts on the same plays."""
    v1 = json.loads((ROOT / "drills" / f"{sid}.json").read_text())
    s = copy.deepcopy(SPECS[sid])
    s["scoring"]["rule"] = from_v1.mechanical_rule(v1)
    tries = [p for p in PLAYS if p["drill"] == sid]
    assert [r["v2"] for r in from_v1.equivalence(s, tries)] == [p["pass"] for p in tries]


@pytest.mark.parametrize("sid", sorted(SPECS))
def test_from_v1_is_idempotent_and_verbatim(sid):
    v1 = json.loads((ROOT / "drills" / f"{sid}.json").read_text())
    s = SPECS[sid]
    assert from_v1.convert(v1, s) == s
    assert len(s["fork"]["native_prefix"]["messages"]) == len(v1["prefix"])  # verbatim, then pseudonymized
    assert s["provenance"]["source"]["v1_id"] == v1["id"]


def test_converted_drills_three_plays_and_adapter_status():
    status = {sid: ck.check(s, [h])["adapters"][0]["status"] for sid, s in SPECS.items()}
    assert status == {"risk-starving-feed-first": "ok", "stale-cooldown-retry-start": "ok", "repeat-pool-swap-retry": "rendering_changed"}
    for s in SPECS.values():
        plays = ck.check(s)["plays"]
        assert plays["oracle"]["pass"] and not plays["null"]["pass"] and not plays["recorded-mistake"]["pass"]


CANDS = [json.loads(line) for line in (FIX / "miner_candidates.jsonl").read_text().splitlines()]


def test_miner_redacted_rows_are_refused():
    out = from_miner.convert_file(FIX / "miner_candidates.jsonl")
    errors = [n[0] for s, n in out if s is None]
    assert len(errors) == 1 and "redacted" in errors[0]


@pytest.mark.parametrize("cand", [c for c in CANDS if not c.get("redacted")], ids=lambda c: c["id"])
def test_miner_candidate_converts_to_a_checkable_spec(cand):
    spec, notes = from_miner.convert(cand)  # no run data: state from the candidate's fork state
    assert sp.validate(spec) == []
    assert spec["world"]["rules_library"] == "kami-rules@1.0.0" and spec["responses"] == []  # the rules library answers
    assert spec["canary"] == from_miner.CANARY
    if cand["tier"] == "B":
        assert spec["scoring"]["model"]["use"].startswith("reported beside")
    r = ck.check(spec, [h])
    assert r["plays"]["oracle"]["pass"] and r["plays"]["oracle"]["score"] == 1, r["plays"]["oracle"]
    assert not r["plays"]["null"]["pass"] and not r["plays"]["recorded-mistake"]["pass"]
    assert r["adapters"][0]["status"] == "ok"
    assert not r["battery"]["kitchen_sink"]  # the 22 kitchen-sink passes of the two-scorer era are gone
    lazy = [f for f in r["failures"] if f.startswith("lazy pass")]
    assert lazy == ([] if not cand.get("group") else ["lazy pass: always_stop"])  # a paired item: check_bank gates its group


def test_miner_cooldown_uses_the_simulated_clock():
    """cooldown-repeat: a split wait passes; acting before the cooldown is flagged; acting long after is late."""
    cand = next(c for c in CANDS if c["rule_id"].startswith("cooldown-repeat"))
    spec, _ = from_miner.convert(cand)
    k = spec["state"]["kamis"][0]
    kami, until = k["index"], k["cooldown_until_unix"]
    act = next(p for p in spec["scoring"]["reference_plays"] if p["name"] == "oracle")["intents"][-1]
    wait_to = lambda t: {"verb": "wait", "args": {"seconds": t - spec["fork"]["clock"]["unix"]}}
    half = (until + 30 - spec["fork"]["clock"]["unix"]) / 2
    assert sp.play(spec, [{"verb": "wait", "args": {"seconds": half}}, {"verb": "read", "args": {"kind": "kami", "kami": kami}},
                          {"verb": "wait", "args": {"seconds": half}}, act])["pass"]
    assert sp.play(spec, [act])["score"] == 0  # on a visible cooldown: early_retry
    late = [{"verb": "wait", "args": {"seconds": 300}}] * 2 + [act]
    assert sp.play(spec, late)["score"] == 0.6  # waited out, then idled: the lesser play
    assert sp.play(spec, [wait_to(until + 10), act])["score"] == 0.8  # inside the S1 band: accepted, capped


def test_miner_trap_control_passes_by_not_doing_the_forbidden_act():
    base = {"verb": "portal_deposit", "args": {"item": 100, "amount": 5}}
    trap = {"schema": "kami-drills/candidate@3", "id": "x-s0001-L5-trap", "kind": "trap_control", "rule_id": "x@1", "economic": False,
            "mistake_class": "none", "competency": ["resource_budgeting"], "tier": "A", "oracle": [],
            "fork_state": {"kamis": {"1": {"state": "DEAD", "hp": 0, "hp_total": 50}}, "room": 1, "t0": 1000.0},
            "scoring": {"options": [{"credit": 1.0, "steps": []}], "zero_if": [{"any": [{"tool": "portal_deposit", "args": {"item": 100}}]}]},
            "group": "x-s0001-L5", "decision_point": {"fork_kind": "session_start", "fork_line": 5, "time": "2026-10-20T00:00:00+00:00", "block": 1},
            "provenance": {"run": "r", "session": 1, "source_model": "m/x", "credit": "c", "pins": {"gdd_sha": "3c6f4f9"}, "source_file": "x"}}
    spec, _ = from_miner.convert(trap)
    assert spec["variants"] == {"origin": "trap_control", "group": "x-s0001-l5"}
    assert sp.play(spec, [])["pass"] and not sp.play(spec, [base])["pass"]
    assert ck.check(spec)["failures"] == []
