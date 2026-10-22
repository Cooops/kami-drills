"""v2 scoring checks (hand-computed): uv run pytest -q tests/test_scoring_v2.py"""
import pytest

from kami_drills.scoring_v2 import (cluster_bootstrap_ci, gap_code, gap_sensitivity, heldout_split, item_stats,
                                    pass_at_k, pass_hat_k, rps, rps_vs_baseline, uniform_rps)


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
    ok, gap = {"gap": False}, {"gap": True}
    assert gap_code({"calls": [ok, ok]}) == "clean" and gap_code({"calls": []}) == "clean"
    assert gap_code({"calls": [gap, ok]}) == "not_administered"
    assert gap_code({"calls": [ok, gap]}) == "scored"
    assert gap_code({"calls": [gap, ok, gap]}) == "not_administered"


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
    cat = {f"a{i}": "A" for i in range(6)} | {f"b{i}": "B" for i in range(3)}
    tune, held = heldout_split(list(cat), cat, seed=1)
    assert len(held) == 3 and len(tune) == 6 and not set(tune) & set(held)
    assert sum(cat[d] == "A" for d in held) == 2 and sum(cat[d] == "B" for d in held) == 1
    assert (tune, held) == heldout_split(list(reversed(list(cat))), cat, seed=1)


def test_item_stats():
    m = {"hard": [1, 0, 0, 0], "mid": [1, 1, 0, 0], "easy": [1, 1, 1, 0], "all": [1, 1, 1, 1], "flip": [0, 0, 1, 1]}
    st = item_stats(m)
    assert st["all"]["low_info"] and st["all"]["point_biserial"] is None
    assert not st["mid"]["low_info"] and st["hard"]["pass_rate"] == .25
    assert st["flip"]["reversed"] and not st["mid"]["reversed"]
    # rest scores: easy [3,2,2,2] correlates positively; mid [3,2,3,2] against [1,1,0,0] is 0
    assert st["easy"]["point_biserial"] > 0 and st["mid"]["point_biserial"] == 0
