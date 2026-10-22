"""Offline tests for the v2 cadence format. Run: uv run pytest -q tests/test_cadence.py"""
import copy
import json
import math

import pytest

from kami_drills import cadence

ALL = cadence.load_schedule_drills()
DRILLS = [d for d in ALL if d.get("kind", "pending") == "pending"]
SLEEP = [d for d in ALL if d.get("kind") == "nothing_due"]
HARNESS_TOOLS = {t["name"] for t in json.loads((cadence.ROOT / "kami_drills" / "surface" / "harness_tools.json").read_text())["tools"]}


def test_examples_present_and_oracle_is_about_one_to_three_hours():
    assert 3 <= len(DRILLS) <= 5
    assert all(45 <= cadence.oracle_minutes(d["state"]) <= 180 for d in DRILLS)


@pytest.mark.parametrize("d", DRILLS, ids=lambda d: d["id"])
def test_always_5_min_and_always_24h_fail(d):
    assert not cadence.score_schedule(d, 5)["passed"]
    assert not cadence.score_schedule(d, 24 * 60)["passed"]


@pytest.mark.parametrize("d", DRILLS, ids=lambda d: d["id"])
def test_oracle_passes_with_zero_loss(d):
    s = cadence.score_schedule(d, cadence.oracle_minutes(d["state"]))
    assert s["passed"] and s["raw_loss"] == 0


PINNED = {"schedule-rest-17194": 106.25, "schedule-rest-9787-a": 59.375, "schedule-rest-9787-b": 59.375}


def test_pending_oracles_pinned_and_kind_defaults_to_pending():
    assert {d["id"]: cadence.score_schedule(d, 60)["oracle_minutes"] for d in DRILLS} == pytest.approx(PINNED)
    assert all("kind" not in d for d in DRILLS) and len(DRILLS) == 3


def test_nothing_due_drills_present_with_hours_long_oracles():
    assert 3 <= len(SLEEP) <= 5
    assert all(4 * 60 <= d["oracle_minutes"] < 24 * 60 and d["flags"] and not d["pending_work"] for d in SLEEP)


@pytest.mark.parametrize("d", SLEEP, ids=lambda d: d["id"])
def test_nothing_due_5_min_fails_oracle_passes_24h_fails_when_event_is_earlier(d):
    assert not cadence.score_schedule(d, 5)["passed"]
    assert cadence.score_schedule(d, d["oracle_minutes"] * 1.3)["passed"]
    assert cadence.score_schedule(d, d["oracle_minutes"])["raw_loss"] == pytest.approx(0, abs=1e-3)
    assert cadence.score_schedule(d, 1440)["passed"] == (math.log(1440 / d["oracle_minutes"]) <= d["pass_threshold"])
    assert not cadence.score_schedule(d, 1440)["passed"]  # every shipped oracle is under 12 h


def test_always_24h_passes_when_nothing_happens_for_a_day():
    state = {"kami": {"hp": 100, "hp_total": 100, "hp_drain_per_hr": 1, "cooldown_s": 0}, "harvest": {"accrued_musu": 0, "rate_per_hr": 0}}
    params = {"hp_risk_fraction": 0.3, "collect_target_musu": 250}
    assert cadence.nothing_due_oracle_minutes(state, params) == 1440  # capped; risk line is 70 h away
    d = {**copy.deepcopy(SLEEP[0]), "state": state, "params": params, "oracle_minutes": 1440}
    assert cadence.score_schedule(d, 1440)["passed"] and not cadence.score_schedule(d, 5)["passed"]


def test_nothing_due_oracle_takes_the_earlier_event():
    state = {"kami": {"hp": 100, "hp_total": 100, "hp_drain_per_hr": 10, "cooldown_s": 0}, "harvest": {"accrued_musu": 0, "rate_per_hr": 50}}
    assert cadence.nothing_due_oracle_minutes(state, {"hp_risk_fraction": 0.3, "collect_target_musu": 250}) == 300  # collect at 5 h, risk at 7 h
    assert cadence.nothing_due_oracle_minutes(state, {"hp_risk_fraction": 0.3, "collect_target_musu": 1000}) == 420


def test_kind_validated():
    for edit in (lambda d: d.update(kind="bogus"), lambda d: d["params"].pop("hp_risk_fraction"), lambda d: d.update(pending_work=["x"]),
                 lambda d: d["state"]["kami"].update(cooldown_s=60), lambda d: d.update(oracle_minutes=1.0), lambda d: d.pop("flags")):
        d = copy.deepcopy(SLEEP[0])
        edit(d)
        with pytest.raises(ValueError):
            cadence.validate_schedule(d)
    bad = copy.deepcopy(DRILLS[0])
    bad["kind"] = "bogus"
    with pytest.raises(ValueError):
        cadence.validate_schedule(bad)


def test_loss_is_symmetric_in_log_space():
    assert cadence.schedule_loss(30, 60) == pytest.approx(cadence.schedule_loss(120, 60)) == pytest.approx(math.log(2))


def test_band_forgives_inside_and_charges_only_the_excess():
    assert cadence.schedule_loss(90, 60, band=2) == 0
    assert cadence.schedule_loss(240, 60, band=2) == pytest.approx(math.log(2))
    assert cadence.schedule_loss(240, 60) == pytest.approx(math.log(4))  # default: no band


def test_invalid_wake_fails():
    assert cadence.schedule_loss(0, 60) == math.inf and cadence.schedule_loss(None, 60) == math.inf


def test_report_carries_raw_loss_next_to_banded_loss():
    d = copy.deepcopy(DRILLS[0])
    d["tolerance_band"] = 2
    s = cadence.score_schedule(d, cadence.oracle_minutes(d["state"]) * 1.5)
    assert s["raw_loss"] == pytest.approx(math.log(1.5)) and s["loss"] == 0 and s["passed"]


def test_cooldown_longer_than_rest_sets_the_oracle():
    state = {"kami": {"hp": 80, "hp_total": 80, "hp_rate_per_hr": 19.2, "cooldown_s": 600}}
    assert cadence.oracle_minutes(state) == 10


def test_schema_accepts_examples_and_records_the_parameters():
    for d in DRILLS:
        assert d["format"] == cadence.SCHEDULE_FORMAT and "tolerance_band" in d and "pass_threshold" in d
        assert d["source_v1_id"] and d["tool"] in HARNESS_TOOLS | {"set_next_wake"}


@pytest.mark.parametrize("edit", [
    lambda d: d.pop("tolerance_band"), lambda d: d.update(tolerance_band=0.5), lambda d: d.pop("source_v1_id"),
    lambda d: d.update(pending_work=[]), lambda d: d.update(oracle_minutes=1.0),
    lambda d: d["state"]["kami"].update(hp_rate_per_hr=0), lambda d: d.update(format="v1")])
def test_schema_rejects(edit):
    d = copy.deepcopy(DRILLS[0])
    edit(d)
    with pytest.raises(ValueError):
        cadence.validate_schedule(d)


def test_v1_loader_ignores_drills_v2():
    from kami_drills import task
    v1 = sorted(p.name for p in task.DRILLS.glob("*.json") if not p.name.startswith("_"))
    assert len(v1) == 47 and not any(n.startswith("schedule-") for n in v1)
    assert cadence.CADENCE_DIR.is_dir() and task.DRILLS not in cadence.CADENCE_DIR.parents
    assert not list(task.DRILLS.rglob("schedule-*"))


def test_state_changing_names_are_real_harness_tools():
    assert cadence.STATE_CHANGING <= HARNESS_TOOLS


def test_action_ratio():
    free = ["lens_kami", "harvest_start", "lens_account"]
    budget = ["feed_kami", "harvest_start", "harvest_stop", "lens_kami", "complete_quest"]
    assert cadence.action_ratio(free, budget) == pytest.approx(1 / 4)
    assert cadence.action_ratio(budget, budget) == 1
    assert cadence.action_ratio(free, ["lens_kami"]) is None  # budget arm acted never: undefined, not infinite


def test_budget_runner_is_a_stub():
    with pytest.raises(NotImplementedError, match="multi-step replay"):
        cadence.run_budget_pair({}, "m")
