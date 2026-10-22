"""v2 cadence drills (docs/v2-cadence.md, proposal sections 2 and 4.2). Separate from v1: nothing here is imported by
task.py, and drills_v2/ is outside the directory the v1 loader reads, so the v1 suite and its judge hash are unchanged.
Offline; the schedule task is the only Inspect code here."""
import json
import math
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.model import ChatMessageSystem, ChatMessageUser, GenerateConfig, get_model
from inspect_ai.scorer import CORRECT, INCORRECT, Score, accuracy, scorer, stderr
from inspect_ai.solver import solver
from inspect_ai.tool import ToolDef
from inspect_ai.tool._tool_params import ToolParams

from kami_drills import task as kd

ROOT = Path(__file__).parent.parent
CADENCE_DIR = ROOT / "drills_v2" / "cadence"
SCHEDULE_FORMAT = "kami-drills-v2/cadence-schedule"
BUDGET_FORMAT = "kami-drills-v2/cadence-budget"
SCHEDULE_TOOL = "set_next_wake"  # v1 harness scaffold tool; argument minutes_from_now
# harness_tools.json has no ACT/PERCEIVE category, so classification is explicit and reviewed against the tool
# descriptions: READ_ONLY are the tools that send no transaction and change nothing (lens_*, get_*, quotes, status
# reads). Every other harness tool sends a transaction or otherwise changes account state. tests/test_cadence.py
# checks that every harness tool is classified and that no "Validates before signing" tool is read-only.
READ_ONLY = frozenset({
    "list_accounts", "get_gas_balance", "bridge_status", "get_tier", "get_all_strategies", "get_all_strategy_statuses",
    "get_strategy_status", "get_strategy_logs", "get_item_orderbook", "pool_swap_quote", "check_quest_completable",
    "quest_state", "get_expected_objective", "get_scavenge_points", "get_scavenge_droptable"})
HARNESS_TOOLS = frozenset(t["name"] for t in json.loads((ROOT / "kami_drills" / "surface" / "harness_tools.json").read_text())["tools"])
STATE_CHANGING = frozenset(n for n in HARNESS_TOOLS if n not in READ_ONLY and not n.startswith("lens_"))


def is_state_changing(tool):
    return tool in STATE_CHANGING


def oracle_minutes(state):
    """Minutes until the cooldown ends: the pending work is harvest_start on a resting kami, and v1's stale-cooldown-retry-start
    rule is to wait the cooldown out and then start (71/90 HP is not waited on to full). No HP term."""
    return state["kami"]["cooldown_s"] / 60


MAX_WAKE_MINUTES = 24 * 60  # harness maximum for set_next_wake
KINDS = ("pending", "nothing_due")
ORIGINS = ("session_end", "synthetic_variant")
# Every situation ends with this and nothing else: no hint about which kind of drill it is. The model infers it from the state.
HP_RISK_FRACTION, COLLECT_TARGET_MUSU = 0.3, 250
# The operator's policy, stated in every situation so the thresholds are facts the model reasons from, not guesses. The same
# sentence in every drill, so it carries no hint of the drill kind. It sits before the neutral closing.
POLICY = (f"Policy: restart a resting kami's harvest as soon as its cooldown ends; collect when accrued MUSU reaches {COLLECT_TARGET_MUSU}; "
          f"act before HP falls below {HP_RISK_FRACTION:.0%} of its maximum. The longest wake you can set is {MAX_WAKE_MINUTES} minutes.")
CLOSING = "You are at the end of a session. Call set_next_wake with minutes_from_now."


def nothing_due_oracle_minutes(state, params):
    """Minutes until the earliest useful event when no kami needs action: harvest accrual reaching
    params.collect_target_musu at the current rate, or HP draining to params.hp_risk_fraction * hp_total at the
    current drain rate (both linear extrapolations). Capped at the 24 h harness maximum. Quest completion has no clock
    in a kami state, so it is not an event here (documented limit)."""
    k, h = state["kami"], state["harvest"]
    events_h = [(k["hp"] - params["hp_risk_fraction"] * k["hp_total"]) / k["hp_drain_per_hr"]]
    if h["rate_per_hr"] > 0:
        events_h.append(max(params["collect_target_musu"] - h["accrued_musu"], 0) / h["rate_per_hr"])
    return min(min(events_h) * 60, MAX_WAKE_MINUTES)


def drill_oracle_minutes(drill):
    if drill.get("kind", "pending") == "nothing_due":
        return nothing_due_oracle_minutes(drill["state"], drill["params"])
    return oracle_minutes(drill["state"])


def schedule_loss(chosen_minutes, oracle, band=None):
    """|ln(chosen/oracle)|. band (a factor >= 1, default none) forgives the first ln(band): 2.0 = within 2x costs nothing."""
    if not chosen_minutes or chosen_minutes <= 0:
        return math.inf
    raw = abs(math.log(chosen_minutes / oracle))
    return raw if band is None else max(0.0, raw - math.log(band))


def score_schedule(drill, chosen_minutes):
    """Raw loss is always reported; `loss` is the banded one the threshold is applied to."""
    oracle = drill_oracle_minutes(drill)
    raw = schedule_loss(chosen_minutes, oracle)
    loss = schedule_loss(chosen_minutes, oracle, drill["tolerance_band"])
    return {"oracle_minutes": oracle, "raw_loss": raw, "loss": loss, "passed": loss <= drill["pass_threshold"]}


def _validate_nothing_due(d):
    k, h, p = d["state"].get("kami", {}), d["state"].get("harvest", {}), d.get("params")
    if p != {"hp_risk_fraction": HP_RISK_FRACTION, "collect_target_musu": COLLECT_TARGET_MUSU}:
        raise ValueError(f"{d['id']}: nothing_due params must equal the thresholds stated in POLICY")
    if d["pending_work"] or not isinstance(d.get("flags"), list):
        raise ValueError(f"{d['id']}: nothing_due needs empty pending_work and a flags list")
    if not {"hp", "hp_total", "hp_drain_per_hr", "cooldown_s"} <= set(k) or not {"accrued_musu", "rate_per_hr"} <= set(h):
        raise ValueError(f"{d['id']}: nothing_due state needs kami hp, hp_total, hp_drain_per_hr, cooldown_s and harvest accrued_musu, rate_per_hr")
    if k["cooldown_s"] != 0 or k["hp_drain_per_hr"] <= 0 or k["hp"] <= p["hp_risk_fraction"] * k["hp_total"] or h["accrued_musu"] >= p["collect_target_musu"]:
        raise ValueError(f"{d['id']}: not nothing_due: cooldown, HP at the risk line, or collect target already met")
    if abs(d["oracle_minutes"] - nothing_due_oracle_minutes(d["state"], p)) > 0.01:
        raise ValueError(f"{d['id']}: recorded oracle_minutes disagrees with the clock fields")


def validate_schedule(d):
    if d.get("kind", "pending") not in KINDS:
        raise ValueError(f"{d.get('id')}: kind must be one of {KINDS}")
    nothing_due = d.get("kind") == "nothing_due"
    for key, typ in {"id": str, "source_v1_id": str, "state": dict, "pending_work": list, "pass_threshold": (int, float)}.items():
        if not isinstance(d.get(key), typ):
            raise ValueError(f"{d.get('id')}: {key} missing or not {typ}")
    if d.get("format") != SCHEDULE_FORMAT or d.get("tool") != SCHEDULE_TOOL:
        raise ValueError(f"{d['id']}: wrong format or tool")
    if d.get("prototype") is not True or d.get("origin") not in ORIGINS or not d.get("situation", "").endswith(f"{POLICY} {CLOSING}"):
        raise ValueError(f"{d['id']}: needs prototype: true, origin in {ORIGINS}, and a situation ending in the policy line and the neutral closing prompt")
    if d["origin"] == "session_end":  # the original agent's own text-only stop in the public v1 prefix
        m = json.loads((ROOT / "drills" / f"{d['source_v1_id']}.json").read_text())["prefix"][d["source_end_msg"]]
        if m["role"] != "assistant" or m.get("tool_calls"):
            raise ValueError(f"{d['id']}: source_end_msg is not a session end (an assistant message with no tool call)")
    if "tolerance_band" not in d or not (d["tolerance_band"] is None or d["tolerance_band"] >= 1):
        raise ValueError(f"{d['id']}: tolerance_band must be recorded as null or a factor >= 1")
    if d["pass_threshold"] < 0 or (not nothing_due and not d["pending_work"]):
        raise ValueError(f"{d['id']}: need pass_threshold >= 0 and non-empty pending_work")
    if nothing_due:
        _validate_nothing_due(d)
        return d
    if d["pending_work"] != ["harvest_start"] or d["state"].get("kami", {}).get("state") != "RESTING" or not d["state"]["kami"].get("cooldown_s", 0) > 0:
        raise ValueError(f"{d['id']}: pending drills are harvest_start on a RESTING kami with cooldown_s > 0")
    if abs(d["oracle_minutes"] - oracle_minutes(d["state"])) > 0.01:
        raise ValueError(f"{d['id']}: recorded oracle_minutes disagrees with the clock fields")
    return d


def load_schedule_drills(directory=CADENCE_DIR):
    return [validate_schedule(json.loads(p.read_text())) for p in sorted(Path(directory).glob("schedule-*.json"))]


def action_count(tool_names):
    return sum(map(is_state_changing, tool_names))


def action_ratio(free_tools, budget_tools):
    """Budget-use metric: state-changing actions in the free-"done" arm over the forced-budget arm. Below 1 means the
    model does less once nothing forces it on (UltraHorizon's collapse). None when the budget arm took no action."""
    b = action_count(budget_tools)
    return action_count(free_tools) / b if b else None


def matched_actions(result):
    """Non-revert recorded state changes the model matched in one arm's episode result. Excludes a different call, a repeated
    recorded revert, reads, and act_sequence (it never matches a single recorded call: documented limit)."""
    return result["matched_actions"]


def pooled_budget_ratio(results):
    """Headline budget metric over run_budget_pair results: matched actions in the free arm over matched actions in the budget
    arm, pooled across drills. A one-call spammer matches nothing (None); a follower gets 1. None when the budget arms matched nothing."""
    b = sum(matched_actions(r["budget"]) for r in results)
    return sum(matched_actions(r["free"]) for r in results) / b if b else None


@scorer(metrics=[accuracy(), stderr()])
def schedule_scorer():
    async def score(state, target):
        calls = [tc for m in state.messages[state.metadata["fork_len"]:] for tc in getattr(m, "tool_calls", None) or []
                 if tc.function == SCHEDULE_TOOL]
        try:
            minutes = float(calls[0].arguments["minutes_from_now"]) if calls else None
        except (KeyError, TypeError, ValueError):
            minutes = None  # a malformed wake time is a fail, like no call at all
        r = score_schedule(state.metadata["drill"], minutes)
        return Score(value=CORRECT if r["passed"] else INCORRECT, answer=str(minutes), metadata=r)
    return score


@task
def schedule_v2(policy: str = "model"):
    """One decision per drill: the model calls set_next_wake once; the call is scored, not executed. policy=null skips the model."""
    async def wake(minutes_from_now):
        return "Next session scheduled."

    @solver
    def decide():
        async def solve(state, generate):
            if policy != "null":
                tool = ToolDef(wake, name=SCHEDULE_TOOL, description="kami-agent scaffold tool: set_next_wake",
                               parameters=ToolParams(properties={"minutes_from_now": {"type": "number", "description": "minutes_from_now"}},
                                                     required=["minutes_from_now"])).as_tool()
                state.messages.append((await get_model().generate(state.messages, [tool])).message)
            return state
        return solve

    samples = [Sample(id=d["id"], input=[ChatMessageSystem(content=kd.SYSTEM), ChatMessageUser(content=d["situation"])],
                      metadata={"drill": d, "category": "schedule_v2", "fork_len": 2}) for d in load_schedule_drills()]
    return Task(dataset=samples, solver=decide(), scorer=schedule_scorer(), fail_on_error=False, config=GenerateConfig(max_tokens=4096))


def run_budget_pair(drill, model):
    """Run both arms of a budget drill offline on its recorded trajectory (kami_drills/episode.py). `model` is a policy,
    model(history, arm) -> (tool, args) or None; history is v1-shaped messages. Reports per arm: raw `actions` (state-changing
    calls attempted, reverts and off-path calls included), `matched_actions` (non-revert recorded state changes matched) and
    `coverage` (matched over path_actions). `path_actions` is what a perfect follower can reach: the path's non-revert state
    changes. `matched_ratio` is free over budget matched; `action_ratio` is the raw-count ratio and can be gamed by repeating
    a call. Also the off-trajectory rate over the two arms and the steps in each."""
    from kami_drills.episode import run_episode
    free, budget = (run_episode(drill, model, arm) for arm in ("free", "budget"))
    mf, mb = matched_actions(free), matched_actions(budget)
    return {"action_ratio": action_ratio(free["tools"], budget["tools"]), "matched_ratio": mf / mb if mb else None,
            "actions": {"free": free["actions"], "budget": budget["actions"]},
            "matched_actions": {"free": mf, "budget": mb}, "path_actions": free["path_actions"],
            "coverage": {"free": mf / free["path_actions"], "budget": mb / free["path_actions"]},
            "off_trajectory_rate": (free["off_trajectory"] + budget["off_trajectory"]) / 2,
            "steps": {"free": free["steps"], "budget": budget["steps"]}, "free": free, "budget": budget}
