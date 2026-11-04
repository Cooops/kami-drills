"""kami-drills profile (docs/v2-profile.md): the skill-area mapping, the area and aggregate math, CI determinism, the CLI."""
import json
import statistics
from pathlib import Path

import pytest

from kami_drills import cli
from kami_drills import profile as pf
from kami_drills import spec as sp

ROOT = Path(__file__).parent.parent
V11 = sp.load([ROOT / "specs" / "v1.1"])
BANK = sp.load([ROOT / "bank" / "v2.0-rc10"])
ALL = V11 + BANK + sp.load([ROOT / "drills_v2" / "spec"])
FIX = ROOT / "tests" / "fixtures" / "v1_rc2_plays.json"


def test_every_drill_maps_to_an_area():
    assert len(V11) == 47 and BANK
    for s in ALL:
        if pf.family(s) != "control":  # a genuine control (miner detector control@1) is gate-only and has no area
            assert pf.area(s) in pf.AREAS, s["id"]
    for row in [*pf.MAPPING["families"].values(), *pf.MAPPING["drills"].values()]:
        assert row["area"] in pf.AREAS and row["why"]
    ids = {s["id"] for s in ALL}
    assert set(pf.MAPPING["drills"]) <= ids  # no override for a drill that does not exist


COMPETENCY_AREA = {"risk_survival": "survival", "economy": "economic", "quest_planning": "progression",
                   "error_recovery": "recovery", "state_tracking": "cadence", "memory": "cadence", "persistence": "cadence"}


def test_area_agrees_with_the_competency_tags_or_says_why():
    """A drill's area must be one its spec's competency tags name, unless the drill has an override (with its reason) or
    its family is a listed exception (with its reason). tool_use names no area."""
    exc = pf.MAPPING["competency_exceptions"]
    assert all(exc[f] for f in exc)
    bad = []
    for s in ALL:
        if pf.family(s) == "control":
            continue
        tagged = {COMPETENCY_AREA[c] for c in s.get("competency", []) if c in COMPETENCY_AREA}
        if tagged and pf.area(s) not in tagged and s["id"] not in pf.MAPPING["drills"] and pf.family(s) not in exc:
            bad.append((s["id"], pf.area(s), s["competency"]))
    assert not bad


def test_unknown_family_is_an_error():
    with pytest.raises(ValueError, match="no skill area"):
        pf.area({"id": "x", "category": "nope", "provenance": {}})


def scored_rows(specs, value):
    return [{"model": "m", "drill": s["id"], "try": 0, "score": value(s)} for s in specs]


def test_area_and_aggregate_math():
    val = {a: (k + 1) / 10 for k, a in enumerate(pf.AREAS)}  # every drill in an area scores the same
    out = pf.profile(V11, scored_rows(V11, lambda s: val[pf.area(s)]), n_boot=200)
    m = out["models"]["m"]
    scored = [s for s in V11 if not pf.excluded(s)]
    assert sum(out["n_by_area"].values()) == len(scored) == 27
    assert out["excluded"] == {"smoke": 10, "retired": 10}
    full = [a for a in pf.AREAS if out["n_by_area"][a] >= pf.MIN_N]
    assert full and len(full) < len(pf.AREAS)  # v1.1 has thin areas
    for a in pf.AREAS:
        c = m["areas"][a]
        assert c["n"] == out["n_by_area"][a] and c["score"] == pytest.approx(val[a])
        assert c["thin"] == (c["n"] < pf.MIN_N)
        assert c["ci95"] is None if c["thin"] else c["ci95"] == pytest.approx([val[a], val[a]])
    assert m["equal_weight"] == pytest.approx(statistics.mean(val[a] for a in full))  # each non-thin area counts the same
    assert m["areas_in_equal_weight"] == full and m["thin_areas_excluded"] == [a for a in pf.AREAS if a not in full]
    assert m["equal_weight_all"] == pytest.approx(statistics.mean(val.values()))
    assert pf.profile(V11, scored_rows(V11, lambda s: 1.0), n_boot=10, min_n=99)["models"]["m"]["equal_weight"] is None
    assert m["drill_weighted"] == pytest.approx(statistics.mean(val[pf.area(s)] for s in scored))
    assert m["equal_weight"] != pytest.approx(m["drill_weighted"])


def test_controls_traps_and_tier_b_get_no_score():
    out = pf.profile(BANK, scored_rows(BANK, lambda s: 1.0), n_boot=50)
    ex = out["excluded"]
    assert ex["control or trap (gate-only)"] == sum(s.get("variants", {}).get("origin") == "trap_control" for s in BANK)
    assert ex["tier B (unscored)"] > 0
    assert out["models"]["m"]["drills"] == len(BANK) - sum(ex.values())


def test_ci_is_deterministic():
    rows = scored_rows(V11, lambda s: len(s["id"]) % 3 / 2)  # varied, reproducible scores
    a, b = pf.profile(V11, rows, n_boot=500), pf.profile(V11, rows, n_boot=500)
    assert a == b
    c = a["models"]["m"]["areas"]["recovery"]
    assert c["ci95"][0] <= c["score"] <= c["ci95"][1] and c["ci95"][0] < c["ci95"][1]


def run_cli(*argv, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["profile", *map(str, argv)])
    assert e.value.code == 0
    return capsys.readouterr().out


def test_cli_on_the_fixture(capsys):
    # 11 models x 2 tries x 3 converted v1 drills; in v1.1 risk-starving-feed-first is smoke, the other two recovery
    out = json.loads(run_cli(FIX, "--json", capsys=capsys))
    assert len(out["models"]) == 11 and out["excluded"] == {"smoke": 10, "retired": 10}
    assert out["n_by_area"] == {"survival": 0, "economic": 0, "progression": 0, "cadence": 0, "recovery": 2}
    assert len(out["not_administered"]) == 25
    rows = json.loads(FIX.read_text())
    spec = next(s for s in V11 if s["id"] == "stale-cooldown-retry-start")
    from kami_drills.spec import from_v1
    want = from_v1.equivalence(spec, [r for r in rows if r["drill"] == spec["id"]])
    m = want[0]["model"]
    got = pf.drill_scores([spec], rows)[m][spec["id"]]
    assert got == pytest.approx(statistics.mean(r["score"] for r in want if r["model"] == m))
    p = out["models"][m]
    assert p["equal_weight"] is None and p["areas_in_equal_weight"] == []  # recovery n=2 is thin: no headline
    assert p["equal_weight_all"] == p["areas"]["recovery"]["score"] and p["areas"]["recovery"]["ci95"] is None
    text = run_cli(FIX, "--bars", capsys=capsys)
    assert "equal-weight all" in text and "drill-weighted" in text and "recovery (n=2, thin)" in text and "[thin]" in text
    assert "tier B items are reported beside a score, never in it" in text


def test_a_results_file_per_drill_keeps_its_scores():
    rows = pf.tries({"model": "m", "per_drill": {"a": [1, 0], "b": [0.5]}})
    assert rows == [{"model": "m", "drill": "a", "try": 0, "score": 1.0}, {"model": "m", "drill": "a", "try": 1, "score": 0.0},
                    {"model": "m", "drill": "b", "try": 0, "score": 0.5}]
    s = next(s for s in V11 if not pf.excluded(s))
    assert pf.drill_scores([s], [dict(r, drill=s["id"]) for r in rows[:2]]) == {"m": {s["id"]: 0.5}}
