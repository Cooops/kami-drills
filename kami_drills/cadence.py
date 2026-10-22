"""v2 cadence drills (docs/v2-cadence.md, proposal sections 2 and 4.2). Separate from v1: nothing here is imported by
task.py, and drills_v2/ is outside the directory the v1 loader reads, so the v1 suite and its judge hash are unchanged.
Pure stdlib; offline."""
import json
import math
from pathlib import Path

ROOT = Path(__file__).parent.parent
CADENCE_DIR = ROOT / "drills_v2" / "cadence"
SCHEDULE_FORMAT = "kami-drills-v2/cadence-schedule"
BUDGET_FORMAT = "kami-drills-v2/cadence-budget"
SCHEDULE_TOOL = "set_next_wake"  # v1 harness scaffold tool; argument minutes_from_now
# v1 has no ACT/PERCEIVE classification, so this is explicit: tools that send transactions (names from harness_tools.json)
STATE_CHANGING = frozenset({
    "harvest_start", "harvest_stop", "harvest_collect", "move_to_room", "travel_to_room", "feed_kami", "revive_kami",
    "level_up_kami", "level_to", "allocate_skills", "upgrade_skill", "level_and_allocate_batch",
    "feed_level_allocate_batch", "use_item_batch", "accept_quest", "complete_quest", "act_sequence"})


def oracle_minutes(state):
    """Minutes until the kami is both off cooldown and fully rested: the soonest moment a session has full work to do.
    Needs a resting kami regaining HP (hp_rate_per_hr > 0)."""
    k = state["kami"]
    rest_s = max(k["hp_total"] - k["hp"], 0) / k["hp_rate_per_hr"] * 3600
    return max(k["cooldown_s"], rest_s) / 60


def schedule_loss(chosen_minutes, oracle, band=None):
    """|ln(chosen/oracle)|. band (a factor >= 1, default none) forgives the first ln(band): 2.0 = within 2x costs nothing."""
    if not chosen_minutes or chosen_minutes <= 0:
        return math.inf
    raw = abs(math.log(chosen_minutes / oracle))
    return raw if band is None else max(0.0, raw - math.log(band))


def score_schedule(drill, chosen_minutes):
    """Raw loss is always reported; `loss` is the banded one the threshold is applied to."""
    oracle = oracle_minutes(drill["state"])
    raw = schedule_loss(chosen_minutes, oracle)
    loss = schedule_loss(chosen_minutes, oracle, drill["tolerance_band"])
    return {"oracle_minutes": oracle, "raw_loss": raw, "loss": loss, "passed": loss <= drill["pass_threshold"]}


def validate_schedule(d):
    for key, typ in {"id": str, "source_v1_id": str, "state": dict, "pending_work": list, "pass_threshold": (int, float)}.items():
        if not isinstance(d.get(key), typ):
            raise ValueError(f"{d.get('id')}: {key} missing or not {typ}")
    if d.get("format") != SCHEDULE_FORMAT or d.get("tool") != SCHEDULE_TOOL:
        raise ValueError(f"{d['id']}: wrong format or tool")
    if "tolerance_band" not in d or not (d["tolerance_band"] is None or d["tolerance_band"] >= 1):
        raise ValueError(f"{d['id']}: tolerance_band must be recorded as null or a factor >= 1")
    if d["pass_threshold"] < 0 or not d["pending_work"]:
        raise ValueError(f"{d['id']}: need pass_threshold >= 0 and non-empty pending_work")
    if set(d["state"].get("kami", {})) < {"hp", "hp_total", "hp_rate_per_hr", "cooldown_s"} or d["state"]["kami"]["hp_rate_per_hr"] <= 0:
        raise ValueError(f"{d['id']}: state.kami needs hp, hp_total, hp_rate_per_hr > 0, cooldown_s")
    if abs(d["oracle_minutes"] - oracle_minutes(d["state"])) > 0.01:
        raise ValueError(f"{d['id']}: recorded oracle_minutes disagrees with the clock fields")
    return d


def load_schedule_drills(directory=CADENCE_DIR):
    return [validate_schedule(json.loads(p.read_text())) for p in sorted(Path(directory).glob("schedule-*.json"))]


def action_count(tool_names):
    return sum(t in STATE_CHANGING for t in tool_names)


def action_ratio(free_tools, budget_tools):
    """Budget-use metric: state-changing actions in the free-"done" arm over the forced-budget arm. Below 1 means the
    model does less once nothing forces it on (UltraHorizon's collapse). None when the budget arm took no action."""
    b = action_count(budget_tools)
    return action_count(free_tools) / b if b else None


def run_budget_pair(drill, model):
    # The format and scorer above are complete; the runner is not. Two arms of a multi-call episode need replayed
    # state to change between calls, which v1's single-fork replay cannot do.
    raise NotImplementedError("needs multi-step replay (see proposal §4.5)")
