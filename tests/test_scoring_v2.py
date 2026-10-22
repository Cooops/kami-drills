"""v2 scoring checks (hand-computed): uv run pytest -q tests/test_scoring_v2.py"""
import pytest

from kami_drills.scoring_v2 import (MAX_SCHEDULE_LOSS, cadence_score, choose_k, cluster_bootstrap_ci, composite,
                                    cost_per_action, gap_code, gap_sensitivity, heldout_split, is_heldout, item_stats,
                                    mcb_set, pass_at_k, pass_hat_k, rho_cluster_ci, rps, rps_vs_baseline, select_items,
                                    tier_probs_bootstrap, uniform_rps)


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
    assert rps_vs_baseline(.1, .4) == pytest.approx(.75) and rps_vs_baseline(.1, 0) is None
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
