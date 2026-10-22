"""v2 scoring checks (hand-computed): uv run pytest -q tests/test_scoring_v2.py"""
import pytest

import math
import random
import statistics

from kami_drills.scoring_v2 import (MAX_SCHEDULE_LOSS, TIERS, cadence_score, certain_probs, choose_k,
                                    cluster_bootstrap_ci, composite, cost_per_action, default_tier_sizes, forecast,
                                    gap_code, gap_sensitivity, hedged_probs, heldout_split, is_heldout, item_stats,
                                    live_tiers, mcb_set, pass_at_k, pass_hat_k, pooled_budget_score, rho_cluster_ci, rps, rps_skill,
                                    rps_vs_baseline, season_baseline_probs, select_items, tier_probs_bootstrap,
                                    uniform_rps)


def test_pass_k():
    assert pass_hat_k([True, True]) and not pass_hat_k([True, False])
    assert pass_at_k([False, True]) and not pass_at_k([False, False])
    tries = {"a": [True, True], "b": [True, False], "c": [False, False], "d": [True, True]}
    assert pass_hat_k(tries) == .5 and pass_at_k(tries) == .75


def test_cluster_bootstrap():
    same = {"a": 1.0, "b": 1.0, "c": 1.0}
    assert cluster_bootstrap_ci(same, {"a": "x", "b": "x", "c": "y"}, n=200) == (1.0, 1.0, 2)
    # two all-or-nothing clusters: resampled means reach both 0 and 1
    lo, hi, k = cluster_bootstrap_ci({"a": 1, "b": 1, "c": 0, "d": 0}, dict(zip("abcd", "xxyy")), n=500)
    assert (lo, hi, k) == (0.0, 1.0, 2)
    args = ({"a": 1, "b": 0}, {"a": 1, "b": 2})
    assert cluster_bootstrap_ci(*args, n=50, seed=3) == cluster_bootstrap_ci(*args, n=50, seed=3)


def test_gap_code():
    act, read, gap = {"tool": "feed_kami", "gap": False}, {"tool": "lens_kami", "gap": False}, {"tool": "quest_state", "gap": True}
    assert gap_code({"calls": [act, act]}) == "clean" and gap_code({"calls": []}) == "clean"
    assert gap_code({"calls": [gap, act]}) == "not_administered"
    assert gap_code({"calls": [act, gap]}) == "scored"
    assert gap_code({"calls": [gap, act, gap]}) == "not_administered"
    # a successful read is not a scorable action: a gap after only reads is still not administered
    assert gap_code({"calls": [read, read, gap, act]}) == "not_administered"
    assert gap_code({"calls": [read, act, gap]}) == "scored"


def test_gap_sensitivity():
    s = [("clean", 1), ("scored", 0), ("not_administered", 1), ("not_administered", 0)]
    assert gap_sensitivity(s) == (.5, .25)
    assert gap_sensitivity([("not_administered", 1)]) == (None, 0.0)


def test_rps():
    certain = {"top": 1, "middle": 0, "bottom": 0}
    assert rps(certain, "top") == 0
    assert rps(certain, "bottom") == 1  # cumulative gaps 1 and 1, over K-1 = 2
    assert rps(certain, "middle") == .5
    assert uniform_rps("top") == pytest.approx((4 / 9 + 1 / 9) / 2)
    assert uniform_rps("middle") == pytest.approx((1 / 9 + 1 / 9) / 2)
    assert rps_vs_baseline(.1, .4) == pytest.approx(.75)
    # baseline RPS below 0.01: the registered rule reports the RPS difference, not an exploding ratio
    assert rps_vs_baseline(.1, 0) == pytest.approx(-.1) and rps_vs_baseline(.002, .005) == pytest.approx(.003)
    with pytest.raises(ValueError):
        rps({"top": .5, "middle": .2, "bottom": .2}, "top")


def test_heldout_split():
    cat = {f"d{i}": "AB"[i % 2] for i in range(300)}
    tune, held = heldout_split(list(cat), cat)
    assert not set(tune) & set(held) and len(tune) + len(held) == 300
    assert 70 < len(held) < 130  # 1/3 in expectation
    assert (tune, held) == heldout_split(list(reversed(list(cat))), cat)
    # retiring drills never moves another drill across the split
    kept = [d for d in cat if int(d[1:]) % 3]
    tune2, held2 = heldout_split(kept, cat)
    assert held2 == [d for d in held if d in kept] and tune2 == [d for d in tune if d in kept]
    assert is_heldout("d5", "A") == is_heldout("d5", "A")


def test_item_stats_takes_binary_model_tries():
    with pytest.raises(ValueError, match="model-try"):
        item_stats({"a": [.5, 1, 0], "b": [1, 0, 1]})


def test_item_stats():
    m = {"hard": [1, 0, 0, 0], "mid": [1, 1, 0, 0], "easy": [1, 1, 1, 0], "all": [1, 1, 1, 1], "flip": [0, 0, 1, 1]}
    st = item_stats(m)
    assert st["all"]["low_info"] and st["all"]["point_biserial"] is None
    assert not st["mid"]["low_info"] and st["hard"]["pass_rate"] == .25
    assert st["flip"]["reversed"] and not st["mid"]["reversed"]
    # rest scores: easy [3,2,2,2] correlates positively; mid [3,2,3,2] against [1,1,0,0] is 0
    assert st["easy"]["point_biserial"] > 0 and st["mid"]["point_biserial"] == 0


def test_select_items():
    st = item_stats({"hard": [1, 0, 0, 0], "mid": [1, 1, 0, 0], "easy": [1, 1, 1, 0], "all": [1, 1, 1, 1],
                     "flip": [0, 0, 1, 1]})
    assert select_items(st, 10) == ["easy", "hard", "mid"]  # all (low info) and flip (reversed) dropped
    assert select_items(st, 1) == ["easy"]


def test_cadence_score():
    c = cadence_score({"a": [0, 0, 1], "b": [1, 1, 0], "c": [2, float("inf"), float("inf")]},
                      {"a": 1.5, "b": .5, "c": None})
    # schedule: -median = 0, -1, -MAX; budget capped/None: 1, .5, 0
    assert c["a"] > c["b"] > c["c"] and sum(c.values()) == pytest.approx(0)
    flat = cadence_score({"a": [1], "b": [1]}, {"a": .2, "b": .8})
    assert flat["a"] == pytest.approx(-.5) and flat["b"] == pytest.approx(.5)  # schedule z is 0, budget z is -1/+1
    assert MAX_SCHEDULE_LOSS == pytest.approx(7.272, abs=1e-3)


def test_composite():
    d, c = {"a": .9, "b": .5, "c": .1}, {"a": 0.0, "b": 1.0, "c": -1.0}
    z = composite(d, c)
    assert z["b"] == pytest.approx(1.2247, abs=1e-4) and z["a"] == pytest.approx(1.2247, abs=1e-4)
    assert z["c"] == pytest.approx(-2.4495, abs=1e-4)
    assert composite({"a": .5}, {"a": .4}, form="product") == {"a": .2}
    with pytest.raises(ValueError):
        composite(d, c, form="product")
    with pytest.raises(ValueError):
        composite(d, c, form="max")


def test_tier_probs_bootstrap():
    # five models, items all identical in ordering: tiers are certain
    scores = {m: {i: v for i in "xyz"} for m, v in zip("abcde", [.9, .8, .5, .3, .1])}
    p = tier_probs_bootstrap(scores, tier_sizes=(1, 3, 1), n=100)
    assert p["a"]["top"] == 1 and p["e"]["bottom"] == 1 and p["c"]["middle"] == 1
    # two tied models share the top slot evenly; probabilities sum to 1
    tied = {"a": {"x": 1, "y": 0}, "b": {"x": 1, "y": 0}, "c": {"x": -1, "y": -1}}
    p = tier_probs_bootstrap(tied, tier_sizes=(1, 1, 1), n=2000, seed=1)
    assert p["a"]["top"] == pytest.approx(.5, abs=.05) and p["c"]["bottom"] == 1
    assert all(sum(v.values()) == pytest.approx(1) for v in p.values())
    # crossing items: rank depends on which item is drawn
    cross = {"a": {"x": 1, "y": 0}, "b": {"x": 0, "y": 1}}
    p = tier_probs_bootstrap(cross, tier_sizes=(1, 0, 1), n=2000, clusters={"x": 1, "y": 2})
    assert .4 < p["a"]["top"] < .6
    with pytest.raises(ValueError):
        tier_probs_bootstrap(scores, tier_sizes=(3, 4, 3))
    # score_fn gets the resampled item ids: scoring by minus the sum flips the tiers
    flip = tier_probs_bootstrap(scores, tier_sizes=(1, 3, 1), n=50,
                                score_fn=lambda pick: {m: -sum(scores[m][i] for i in pick) for m in scores})
    assert flip["e"]["top"] == 1 and flip["a"]["bottom"] == 1


def test_mcb_set():
    clear = {"a": {i: 1 for i in range(20)}, "b": {i: 0 for i in range(20)}}
    assert mcb_set(clear, n=200) == ["a"]
    close = {"a": {i: i % 2 for i in range(20)}, "b": {i: int(i % 2 == 0 or i == 1) for i in range(20)}}
    assert mcb_set(close, n=500) == ["a", "b"]


def test_rho_cluster_ci():
    drill = {m: i for i, m in enumerate("abcdefgh")}
    rho, lo, hi = rho_cluster_ci(drill, {m: [i, i + .5] for i, m in enumerate("abcdefgh")}, n=300)
    assert rho == pytest.approx(1) and lo == pytest.approx(1)
    rho, lo, hi = rho_cluster_ci(drill, {m: [v] for m, v in zip("abcdefgh", [3, 1, 4, 1, 5, 9, 2, 6])}, n=500)
    assert lo < rho < hi and lo < 0


def test_choose_k():
    # items always agree across tries: no within-item noise, K=2 is enough
    assert choose_k({"a": [1, 1], "b": [0, 0], "c": [1, 1]}) == 2
    # every item splits its tries: within variance .5 dwarfs between variance 0, so the cap
    assert choose_k({"a": [1, 0], "b": [0, 1]}, k_max=4) == 4
    # within = mean(0, 0, .5) = 1/6, between = var(1, 0, .5) = .25: 1/(6K) <= ratio * .25
    t = {"a": [1, 1], "b": [0, 0], "c": [1, 0]}
    assert choose_k(t, ratio=.5) == 2 and choose_k(t, ratio=.3) == 3 and choose_k(t, ratio=.2) == 4


def test_cost_per_action():
    assert cost_per_action(.6, ["feed_kami", "lens_kami", "harvest_start", "harvest_collect"]) == pytest.approx(.2)
    assert cost_per_action(.6, ["lens_kami"]) is None


def test_default_tier_sizes():
    # floor(0.3 N + 0.5): halves round up, unlike Python's round (which gives 4/7/4 at N = 15)
    assert default_tier_sizes(10) == (3, 4, 3) and default_tier_sizes(15) == (5, 5, 5)
    assert default_tier_sizes(5) == (2, 1, 2) and default_tier_sizes(7) == (2, 3, 2) and default_tier_sizes(12) == (4, 4, 4)


def test_bootstrap_not_administered_and_one_family():
    # b was never administered item y (absent) and z (None): no KeyError, and b is scored on x alone
    scores = {"a": {"x": 0, "y": .5, "z": .5}, "b": {"x": 1}, "c": {"x": 0, "y": 0, "z": None}}
    p = tier_probs_bootstrap(scores, tier_sizes=(1, 1, 1), n=200)
    assert p["b"]["top"] == 1
    # every item in one family: the stratum falls back to items, so no draw is empty and ranks still vary
    p = tier_probs_bootstrap({"a": {"x": 1, "y": 0}, "b": {"x": 0, "y": 1}}, tier_sizes=(1, 0, 1), n=2000,
                             clusters={"x": "f", "y": "f"})
    assert .4 < p["a"]["top"] < .6


def test_season_baseline_probs():
    runs = {"a": [9, 9], "b": [5, 5, 5], "c": [1, 1]}
    p = season_baseline_probs(runs, (1, 1, 1), n=100)
    assert p == certain_probs({"a": "top", "b": "middle", "c": "bottom"})
    # two runs: deviations x sqrt(2 * 2 / 1) = 2, so a draws -5 / 5 / 15 and b 3 / 5 / 7; a is top in 1/2 of draws
    noisy = season_baseline_probs({"a": [0, 10], "b": [4, 6], "c": [-50, -50]}, (1, 1, 1), n=4000, seed=1)
    assert noisy["c"]["bottom"] == 1 and .45 < noisy["a"]["top"] < .55 and all(
        sum(v.values()) == pytest.approx(1) for v in noisy.values())
    assert live_tiers({"a": [1, 3], "b": [2, 2], "c": [0]}, (1, 1, 1)) == {"a": "top", "b": "middle", "c": "bottom"}


def test_rps_skill():
    out = {"a": "top", "b": "bottom"}
    r = rps_skill(certain_probs(out), out, {"s1": certain_probs({"a": "bottom", "b": "top"})})
    assert r["rps"] == 0 and r["skill"] == {"s1": 1.0, "uniform": 1.0}
    # a model the baseline does not cover is uniform in it, so the baseline equals uniform here
    r = rps_skill(certain_probs({"a": "middle", "b": "middle"}), out, {"s1": {}})
    assert r["skill"]["s1"] == r["skill"]["uniform"] < 0


def test_hedged_probs():
    h = hedged_probs({"a": "top", "b": "middle", "c": "bottom"})
    assert h["a"] == {"top": .7, "middle": .25, "bottom": .05} and h["b"] == {"top": .15, "middle": .7, "bottom": .15}
    assert h["c"] == {"top": .05, "middle": .25, "bottom": .7}


@pytest.mark.parametrize("runs,sd", [(2, 4), (2, 8), (3, 4), (3, 8)])
def test_hedged_season1_copy_has_no_skill_against_calibrated_baseline(runs, sd):
    # Seasons drawn from the same per-model run distribution (no drift). A hedged copy of the Season 1 tiers carries no
    # drill information: it beats the certain Season 1 forecast clearly, but is not clearly positive against the
    # calibrated one once the small-sample correction is in (the plain bootstrap gave it +0.05 to +0.10 at 2 runs).
    rng, sizes, vs_cal, vs_certain = random.Random(7), (3, 4, 3), [], []
    for season in range(60):
        s1 = {f"m{i}": [rng.gauss(i, sd) for _ in range(runs)] for i in range(10)}
        s2 = {m: [rng.gauss(int(m[1:]), sd) for _ in range(runs)] for m in s1}
        t1, out = live_tiers(s1, sizes), live_tiers(s2, sizes)
        r = rps_skill(hedged_probs(t1), out,
                      {"calibrated": season_baseline_probs(s1, sizes, n=300, seed=season), "certain": certain_probs(t1)})
        vs_cal.append(r["skill"]["calibrated"])
        vs_certain.append(r["skill"]["certain"])
    assert statistics.fmean(vs_certain) > .1
    assert statistics.fmean(vs_cal) - 2 * statistics.stdev(vs_cal) / math.sqrt(len(vs_cal)) < 0


def _synthetic(rng, ability):
    """10 models; decision items in 4 families, schedule drills all in one family, budget drills in 2."""
    dec = {m: {f"d{j}": float(rng.random() < .2 + .06 * a) for j in range(24)} for m, a in ability.items()}
    dec["m3"]["d0"] = None  # gap-excluded
    del dec["m4"]["d1"]  # never administered
    sched = {m: {f"s{j}": abs(rng.gauss(0, 3 - .25 * a)) for j in range(4)} for m, a in ability.items()}
    sched["m0"]["s0"] = float("inf")
    budget = {m: {f"b{j}": (rng.randint(0, a + 2), 10, 12) for j in range(4)} for m, a in ability.items()}
    clusters = {**{f"d{j}": f"fam{j % 4}" for j in range(24)}, **{f"s{j}": "sched" for j in range(4)},
                **{f"b{j}": f"bfam{j % 2}" for j in range(4)}}
    return dec, sched, budget, clusters


def test_forecast_end_to_end():
    rng = random.Random(3)
    ability = {f"m{i}": i for i in range(10)}
    dec, sched, budget, clusters = _synthetic(rng, ability)
    sizes = default_tier_sizes(10)
    p = forecast(dec, sched, budget, sizes, n=500, seed=0, clusters=clusters)
    assert set(p) == set(ability) and all(sum(v.values()) == pytest.approx(1) for v in p.values())
    assert p["m9"]["top"] > .5 and p["m0"]["bottom"] > .5
    assert sum(v["top"] for v in p.values()) == pytest.approx(3)
    assert p == forecast(dec, sched, budget, sizes, n=500, seed=0, clusters=clusters)
    # pooled budget ratios as fixed inputs; no clusters (item bootstrap within each stratum)
    fixed = {m: pooled_budget_score(list(b.values())) for m, b in budget.items()}
    q = forecast(dec, sched, fixed, sizes, n=300)
    assert q["m9"]["top"] > .5
    # graded against a live outcome with both Season 1 baselines and uniform
    s1 = {m: [a + rng.gauss(0, 2) for _ in range(3)] for m, a in ability.items()}
    s2 = {m: [a + rng.gauss(0, 2) for _ in range(3)] for m, a in ability.items()}
    r = rps_skill(p, live_tiers(s2, sizes), {"season1_calibrated": season_baseline_probs(s1, sizes, n=500),
                                            "season1_certain": certain_probs(live_tiers(s1, sizes))})
    assert set(r["skill"]) == {"season1_calibrated", "season1_certain", "uniform"} and r["skill"]["uniform"] > 0
    assert pooled_budget_score([(3, 10, 10), (5, 10, 10)]) == .4 and pooled_budget_score([(2, 0, 10)]) == 0
    assert pooled_budget_score([(12, 6, 12)]) == .5 and pooled_budget_score([]) == 0


def test_model_with_no_administered_decision_item_is_dropped_with_a_warning():
    rng = random.Random(3)
    ability = {f"m{i}": i for i in range(10)}
    dec, sched, budget, clusters = _synthetic(rng, ability)
    dec["m5"] = {i: None for i in dec["m5"]}  # every item gapped before any action
    with pytest.warns(UserWarning, match="m5"):
        p = forecast(dec, sched, budget, default_tier_sizes(10), n=200, clusters=clusters)
    assert "m5" not in p and sum(v["top"] for v in p.values()) == pytest.approx(default_tier_sizes(9)[0])
    with pytest.warns(UserWarning, match="m5"):
        assert "m5" not in tier_probs_bootstrap(dec, default_tier_sizes(10), n=100)
    with pytest.warns(UserWarning, match="m5"):
        assert "m5" not in mcb_set(dec, n=100)
    # graded with uniform probabilities for the dropped model
    out = live_tiers({m: [a] for m, a in ability.items()}, default_tier_sizes(10))
    assert rps_skill(p, out, {})["rps"] > 0
