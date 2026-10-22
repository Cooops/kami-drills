"""Offline tests for on-trajectory replay. No model calls. Run: uv run pytest -q tests/test_episode.py"""
import copy
import hashlib
import json

import pytest

from kami_drills import cadence, episode as ep
from kami_drills import task as kd

DRILLS = ep.load_budget_drills()
IDS = [d["id"] for d in DRILLS]


def path_of(d):
    e = ep.Episode(d, "free")
    return e.path


def followed_actions(d):
    """State changes a path-follower reaches: up to the last one that did not revert (trailing reverts are skippable, so the
    episode ends before them)."""
    acts = [c for c in path_of(d) if cadence.is_state_changing(c["tool"])]
    return next((i + 1 for i in reversed(range(len(acts))) if not acts[i]["error"]), 0)


def follower(d, stop_after=None, then=None):
    """Scripted policy that replays the recorded calls in order, per arm (optionally `then` after stop_after calls)."""
    state = {}

    def policy(history, arm):
        calls, n = state.setdefault(arm, [iter(path_of(d)), 0])[0], state[arm]
        if stop_after is not None and n[1] >= stop_after:
            return then
        n[1] += 1
        c = next(calls, None)
        return (c["tool"], c["args"]) if c else None
    return policy


def test_three_drills_with_real_continuation():
    assert len(DRILLS) == 3 and all(d["trajectory_actions"] >= 10 for d in DRILLS)
    assert {d["source_v1_id"] for d in DRILLS} <= {p.stem for p in kd.DRILLS.glob("*.json")}


def test_matching_rule():
    same = ep.same_call
    assert same("feed_kami", {"kami_id": 9811}, "feed_kami", {"kami_id": "9811", "account": "main"})  # type/label noise
    assert same("harvest_stop", {"kami_ids": [2, 1]}, "harvest_stop", {"kami_ids": [1, 2]})  # id lists are sets
    assert same("feed_kami", {"kami_id": 1, "food_item_id": None}, "feed_kami", {"kami_id": 1})
    assert same("move_to_room", {"room": " Shop "}, "move_to_room", {"room": "shop"})
    assert not same("feed_kami", {"kami_id": 1}, "feed_kami", {"kami_id": 2})
    assert not same("feed_kami", {"kami_id": 1}, "revive_kami", {"kami_id": 1})
    assert not same("feed_kami", {"kami_id": 1}, "feed_kami", {"kami_id": 1, "food_item_id": 5})  # an extra argument differs
    d = next(t for t in kd.SURFACE["tools"] if kd.DEFAULTS[t["name"]])["name"]
    k, v = next(iter(kd.DEFAULTS[d].items()))
    assert same(d, {k: v}, d, {})  # a written-out schema default is the same call


@pytest.mark.parametrize("d", DRILLS, ids=IDS)
def test_follower_gets_ratio_one_and_never_leaves_the_path(d):
    r = cadence.run_budget_pair(d, follower(d))
    assert r["action_ratio"] == 1 and r["off_trajectory_rate"] == 0
    assert r["free"]["actions"] == r["budget"]["actions"] == followed_actions(d) <= d["trajectory_actions"]
    assert r["actions"] == {"free": followed_actions(d), "budget": followed_actions(d)} and r["path_actions"] == d["trajectory_actions"]
    assert r["free"]["outcome"] == r["budget"]["outcome"] == "trajectory_end"
    assert r["steps"]["free"] == r["steps"]["budget"] <= d["trajectory_calls"]


def test_matched_calls_return_recorded_results():
    d = DRILLS[0]
    e = ep.Episode(d, "free")
    first = next(c for c in e.path if c["tool"] in cadence.STATE_CHANGING)
    assert e.call(first["tool"], first["args"]) == first["text"]


def test_divergence_on_state_change_ends_and_is_recorded():
    d = DRILLS[0]
    e = ep.Episode(d, "budget")
    nxt = next(c for c in e.path if c["tool"] in cadence.STATE_CHANGING)
    other = "harvest_stop" if nxt["tool"] != "harvest_stop" else "feed_kami"
    e.call(other, {"kami_ids": [1]})
    r = e.result()
    assert r["outcome"] == "off_trajectory" and r["off_trajectory"] and r["actions"] == 1 and r["path_actions_matched"] == 0
    assert e.call("lens_account", {}) == "Episode over." and len(e.calls) == 1
    pair = cadence.run_budget_pair(d, lambda h, arm: (other, {"kami_ids": [1]}))
    assert pair["off_trajectory_rate"] == 1 and pair["action_ratio"] == 1 and pair["steps"] == {"free": 1, "budget": 1}


def test_unrecorded_read_is_an_error_not_an_invention():
    e = ep.Episode(DRILLS[0], "free")
    text = e.call("lens_node", {"node_index": 987654})
    assert kd.is_error_text(text) and "no recorded response" in text and e.outcome is None


def test_forced_budget_is_the_50_call_cap_not_the_path_length():
    assert all(d["budget_calls"] == 50 for d in DRILLS) and any(d["trajectory_calls"] < 50 for d in DRILLS)
    reads = lambda h, arm: ("lens_account", {})
    r = cadence.run_budget_pair(DRILLS[0], reads)
    assert r["budget"]["outcome"] == r["free"]["outcome"] == "budget_exhausted" and r["budget"]["steps"] == r["free"]["steps"] == 50
    assert r["action_ratio"] is None  # no state-changing action in the budget arm


def test_done_ends_free_arm_and_is_refused_in_budget_arm():
    d = DRILLS[0]
    r = cadence.run_budget_pair(d, follower(d, stop_after=2, then=("done", {})))
    assert r["free"]["outcome"] == "done" and r["free"]["steps"] == 3
    assert r["budget"]["outcome"] != "done" and r["budget"]["steps"] > 3  # "Budget not used up", keeps going
    e = ep.Episode(d, "budget")
    assert "Keep working" in e.call("done", {}) and e.outcome is None


def test_free_arm_doing_less_gives_ratio_below_one():
    d = DRILLS[0]
    first_action = next(i for i, c in enumerate(path_of(d)) if c["tool"] in cadence.STATE_CHANGING)
    quitter, full = follower(d, first_action + 1, ("done", {})), follower(d)
    r = cadence.run_budget_pair(d, lambda h, arm: (quitter if arm == "free" else full)(h, arm))
    assert r["action_ratio"] == pytest.approx(1 / followed_actions(d)) and r["actions"]["free"] == 1


def test_silent_budget_arm_is_nudged_then_stopped():
    r = ep.run_episode(DRILLS[0], lambda h, a: None, "budget")
    assert r["outcome"] == "stopped" and r["steps"] == 0


def test_validate_catches_wrong_trajectory_length():
    bad = copy.deepcopy(DRILLS[0])
    bad["trajectory_calls"] += 1
    with pytest.raises(ValueError):
        ep.validate_budget(bad)
    bad = {**DRILLS[0], "budget_calls": DRILLS[0]["trajectory_calls"]}  # the path length is not the budget
    with pytest.raises(ValueError):
        ep.validate_budget(bad)


def test_task_builds_offline():
    t = ep.budget_v2()
    assert len(t.dataset) == 2 * len(DRILLS)


def test_v1_loader_and_suite_hash_unaffected():
    v1 = sorted(p.name for p in kd.DRILLS.glob("*.json") if not p.name.startswith("_"))
    assert len(v1) == 47 and not any(n.startswith("budget-") for n in v1)
    h = "sha256:" + hashlib.sha256((cadence.ROOT / "kami_drills" / "task.py").read_bytes()).hexdigest()
    assert {json.loads(p.read_text())["provenance"]["judge_hash"]["kami_drills/task.py"]
            for p in kd.DRILLS.glob("*.json") if not p.name.startswith("_")} == {h}
    assert "episode" not in open(kd.HERE / "task.py").read()


def test_recorded_reverts_can_be_skipped_without_ending_the_episode():
    d = next(d for d in DRILLS if any(c["error"] and cadence.is_state_changing(c["tool"]) for c in path_of(d)))
    path = path_of(d)
    acts = [i for i, c in enumerate(path) if cadence.is_state_changing(c["tool"])]
    k = next(n for n, i in enumerate(acts) if path[i]["error"] and n + 1 < len(acts) and not path[acts[n + 1]]["error"])
    e = ep.Episode(d, "budget")
    for i in acts[:k]:  # follow up to the revert, skip it, take the next real action
        e.call(path[i]["tool"], path[i]["args"])
    nxt = path[acts[k + 1]]
    assert not same_as(path[acts[k]], nxt)
    assert e.call(nxt["tool"], nxt["args"]) == nxt["text"] and e.outcome != "off_trajectory"
    # a real (non-error) state change cannot be skipped
    e = ep.Episode(d, "budget")
    first_ok = next(i for i in acts if not path[i]["error"])
    other = next(path[i] for i in acts if not path[i]["error"] and not same_as(path[i], path[first_ok]))
    for i in acts:
        if i == first_ok:
            break
        e.call(path[i]["tool"], path[i]["args"])
    e.call(other["tool"], other["args"])
    assert e.outcome == "off_trajectory"


def same_as(a, b):
    return ep.same_call(a["tool"], a["args"], b["tool"], b["args"])
