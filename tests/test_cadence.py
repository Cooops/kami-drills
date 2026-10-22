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


def test_examples_present_spread_and_labelled_prototypes():
    assert len(ALL) == 7 and len(DRILLS) == 2 and len(SLEEP) == 5
    assert all(d["prototype"] is True for d in ALL)
    genuine = [d for d in ALL if d["origin"] == "session_end"]
    assert [d["id"] for d in genuine] == ["schedule-harvest-9811-session-end"]  # the only genuine session end in the public v1 prefixes
    assert all(d["origin"] == "synthetic_variant" and d["variant_of"] == genuine[0]["id"] for d in ALL if d not in genuine)
    assert len({json.dumps(d["state"], sort_keys=True) for d in ALL}) == len(ALL)  # no duplicated state (the old 9787-a/b pair)
    oracles = sorted(d["oracle_minutes"] for d in ALL)
    assert oracles[-1] / oracles[0] > 100


@pytest.mark.parametrize("d", ALL, ids=lambda d: d["id"])
def test_oracle_passes_with_zero_loss_and_always_5_fails(d):
    s = cadence.score_schedule(d, d["oracle_minutes"])
    assert s["passed"] and s["raw_loss"] == pytest.approx(0, abs=1e-3)
    assert not cadence.score_schedule(d, 5)["passed"] or d["oracle_minutes"] < 10


def best_pass_rate(policies):
    return max(sum(cadence.score_schedule(d, p(d))["passed"] for d in ALL) for p in policies) / len(ALL)


GRID = [m * 1.1 ** k for k in range(0, 80) for m in (1,)]  # 1 .. ~2000 minutes


def test_no_constant_or_kind_keyed_policy_passes_more_than_about_half():
    assert best_pass_rate([lambda d, c=c: c for c in GRID]) <= 0.5
    kinds = [d.get("kind") for d in ALL]
    # one constant per drill kind, chosen with hindsight: the "90 min if pending else 360" family
    by_kind = sum(max(sum(cadence.score_schedule(d, c)["passed"] for d in ALL if d["kind"] == k) for c in GRID) for k in set(kinds))
    assert by_kind / len(ALL) <= 0.5


def test_every_situation_ends_in_the_same_neutral_prompt_with_no_rule_hints():
    assert all(d["situation"].endswith(cadence.CLOSING) for d in ALL)
    heads = " ".join(d["situation"][: -len(cadence.CLOSING)].lower() for d in ALL)
    for hint in ("pending", "worth starting", "fully rested", "everything you meant", "nothing is due", "only worth"):
        assert hint not in heads


def test_old_drill_without_closing_prompt_is_rejected():
    d = copy.deepcopy(DRILLS[0])
    d["situation"] += " Harvesting the kami is the pending work."
    with pytest.raises(ValueError):
        cadence.validate_schedule(d)
    g = copy.deepcopy(next(x for x in ALL if x["origin"] == "session_end"))
    g["source_end_msg"] = 1  # a tool call, not a session end
    with pytest.raises(ValueError):
        cadence.validate_schedule(g)


def test_pending_oracle_is_the_cooldown_end_as_in_v1_stale_cooldown_retry():  # rest-to-full is not waited on
    assert {d["id"]: d["oracle_minutes"] for d in DRILLS} == pytest.approx({"schedule-cooldown-900s": 15, "schedule-cooldown-124s": 2.0667}, abs=1e-3)


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


def test_always_24h_passes_only_the_idle_day():
    assert [d["id"] for d in ALL if cadence.score_schedule(d, 1440)["passed"]] == ["schedule-harvest-idle"]


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


def test_resting_oracle_ignores_hp_like_v1():
    # v1 stale-cooldown-retry-start: kami 9787, RESTING 71/90, ~100 s cooldown -> wait it out, then harvest_start (not rest to full)
    assert cadence.oracle_minutes({"kami": {"hp": 71, "hp_total": 90, "cooldown_s": 100}}) == pytest.approx(100 / 60)
    assert cadence.oracle_minutes({"kami": {"hp": 1, "hp_total": 90, "cooldown_s": 100}}) == cadence.oracle_minutes({"kami": {"hp": 90, "hp_total": 90, "cooldown_s": 100}})


def test_policy_states_the_thresholds_identically_in_every_prompt():
    assert "250" in cadence.POLICY and "30%" in cadence.POLICY and cadence.POLICY.count("Policy:") == 1
    assert all(d["situation"].endswith(f"{cadence.POLICY} {cadence.CLOSING}") for d in ALL)
    assert all(d["params"] == {"hp_risk_fraction": 0.3, "collect_target_musu": 250} for d in SLEEP)
    d = copy.deepcopy(SLEEP[0])
    d["situation"] = d["situation"].replace(cadence.POLICY + " ", "")
    with pytest.raises(ValueError):
        cadence.validate_schedule(d)


def test_genuine_drill_states_the_collect_v1_requires():
    g = next(d for d in ALL if d["origin"] == "session_end")
    assert "just collected" in g["situation"] and g["state"]["harvest"]["accrued_musu"] == 0 and g["pending_work"] == []


def test_idle_day_has_a_live_harvest():
    d = next(d for d in ALL if d["id"] == "schedule-harvest-idle")
    assert d["state"]["harvest"]["rate_per_hr"] > 0 and 250 / d["state"]["harvest"]["rate_per_hr"] * 60 > 1440


def test_schema_accepts_examples_and_records_the_parameters():
    for d in DRILLS:
        assert d["format"] == cadence.SCHEDULE_FORMAT and "tolerance_band" in d and "pass_threshold" in d
        assert d["source_v1_id"] and d["tool"] in HARNESS_TOOLS | {"set_next_wake"}


@pytest.mark.parametrize("edit", [
    lambda d: d.pop("tolerance_band"), lambda d: d.update(tolerance_band=0.5), lambda d: d.pop("source_v1_id"),
    lambda d: d.update(pending_work=[]), lambda d: d.update(oracle_minutes=1.0),
    lambda d: d["state"]["kami"].update(cooldown_s=0), lambda d: d.update(pending_work=["harvest_stop"]), lambda d: d.update(format="v1")])
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


def test_state_changing_classifies_every_harness_tool():
    descs = {t["name"]: t["description"] for t in json.loads((cadence.ROOT / "kami_drills" / "surface" / "harness_tools.json").read_text())["tools"]}
    assert cadence.STATE_CHANGING | cadence.READ_ONLY | {n for n in HARNESS_TOOLS if n.startswith("lens_")} == HARNESS_TOOLS
    assert not cadence.STATE_CHANGING & cadence.READ_ONLY and cadence.READ_ONLY <= HARNESS_TOOLS
    # every tool that sends a transaction (the harness says so in its description) is state-changing; none of those is read-only
    tx = {n for n, t in descs.items() if "Validates before signing" in t or "Owner wallet" in t or "Operator wallet" in t or "owner-signed transaction" in t}
    assert tx <= cadence.STATE_CHANGING and len(tx) > 40
    for n in ("newbie_vendor_buy", "fund_operator", "listing_buy", "pool_swap", "craft_item", "burn_items", "scavenge_claim_and_reveal",
              "register_account", "harvest_start", "chat_send", "gacha_use", "take_trade", "portal_deposit"):
        assert cadence.is_state_changing(n)
    for n in ("lens_kami", "pool_swap_quote", "quest_state", "set_next_wake", "workspace_read", "no_such_tool"):
        assert not cadence.is_state_changing(n)


def test_pooled_budget_score():
    r = lambda f, b, p: {"free": {"matched_actions": f}, "budget": {"matched_actions": b}, "path_actions": p}
    # pooled: min(8/10, 1) x 10/12 = min(8, 10) / 12
    assert cadence.pooled_budget_score([r(6, 6, 6), r(2, 4, 6)]) == pytest.approx(8 / 12)
    assert cadence.pooled_budget_score([r(0, 0, 6)]) == 0 and cadence.pooled_budget_score([]) == 0
    assert cadence.pooled_budget_score([r(6, 3, 6)]) == .5  # acting more when free than when forced is not more persistence


def test_action_ratio():
    free = ["lens_kami", "harvest_start", "lens_account"]
    budget = ["feed_kami", "harvest_start", "harvest_stop", "lens_kami", "complete_quest"]
    assert cadence.action_ratio(free, budget) == pytest.approx(1 / 4)
    assert cadence.action_ratio(budget, budget) == 1
    assert cadence.action_ratio(free, ["lens_kami"]) is None  # budget arm acted never: undefined, not infinite
