"""On-trajectory multi-step replay for the v2 budget drill (docs/v2-cadence.md, proposal sections 4.2 and 4.5).

An episode starts partway through a public v1 drill's recorded prefix and follows that recorded transcript forward. It
never invents a tool output: every answer is a recorded one. State-changing calls must match the next recorded
state-changing call (same tool, same normalised arguments) and are answered with its recorded result. A recorded
state change whose result was a revert or error may be skipped. Any other different state-changing call ends the
episode ("off_trajectory"). Reads keep v1 semantics: recorded reads plus _global_reads.
Nothing here is imported by task.py, so the v1 suite and its judge hash are unchanged.
"""
import json
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.model import ChatMessageUser, GenerateConfig, execute_tools, get_model
from inspect_ai.scorer import Score, mean, scorer, stderr
from inspect_ai.solver import Generate, TaskState, solver
from inspect_ai.tool import ToolDef
from inspect_ai.tool._tool_params import ToolParams

from kami_drills import cadence
from kami_drills import task as kd

DONE_TOOLS = frozenset({"done", "end_session", "set_next_wake"})  # end the free arm; the budget arm answers "keep going"
NUDGES = 3  # budget arm: how many times a reply with no tool call is answered with "keep working" before it counts as stopped
ARM_NOTE = {"free": "Work through the pending tasks. Call `done` when you are finished.",
            "budget": "You have a budget of {n} tool calls and there is no `done` tool: use the budget on useful work."}
OUTCOMES = ("done", "stopped", "budget_exhausted", "off_trajectory", "trajectory_end")


def norm_args(args):
    """Canonical key arguments: no account label, no None or schema-default value (a written-out default is the same
    call), "my account" spellings dropped, id lists as sorted sets, numbers and numeric strings alike, text trimmed and
    lower-cased. Everything else must be equal: this is deliberately strict."""
    def scalar(x):
        try:
            return "n:%g" % float(x) if not isinstance(x, bool) else str(x)
        except (TypeError, ValueError):
            return str(x).strip().lower()
    out = {}
    for k, v in (args or {}).items():
        if k == "account" or v is None or (k in kd.OWN and v in (-1, "main", "")):
            continue
        out[k] = sorted(scalar(x) for x in v) if isinstance(v, list) and not any(isinstance(x, (dict, list)) for x in v) \
            else json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else scalar(v)
    return out


def same_call(tool, a, tool2, b):
    drop = lambda t, n: {k: v for k, v in n.items() if v != kd.DEFAULTS.get(t, {}).get(k, object())}
    return tool == tool2 and drop(tool, norm_args(a)) == drop(tool, norm_args(b))


def trajectory(prefix):
    """Flat [{"tool", "args", "text"}] of the tool calls in a recorded message list, in order, each with its result."""
    results = {m["tool_call_id"]: str(m["content"]) for m in prefix if m["role"] == "tool_result"}
    return [{"tool": tc["name"], "args": tc.get("args") or {}, "text": results.get(tc["id"], ""),
             "error": kd.is_error_text(results.get(tc["id"], ""))}
            for m in prefix if m["role"] == "assistant" for tc in m.get("tool_calls") or []]


class Episode:
    def __init__(self, drill, arm):
        self.src = json.loads((kd.DRILLS / f"{drill['source_v1_id']}.json").read_text())
        self.drill, self.arm = drill, arm
        self.prefix = self.src["prefix"][:drill["start"]]
        self.path = trajectory(self.src["prefix"][drill["start"]:])
        self.budget = kd.MAX_TOOL_CALLS  # both arms get v1's 50-call session cap; the budget arm just has no `done`
        self.pos, self.calls, self.outcome = 0, [], None

    def _actions(self):
        return [i for i in range(self.pos, len(self.path)) if cadence.is_state_changing(self.path[i]["tool"])]

    def _match_action(self, tool, args):
        """Index of the recorded state change this call matches: the next one, or a later one when every state change
        before it was a recorded revert/error (those may be skipped). None if no match."""
        for i in self._actions():
            if same_call(tool, args, self.path[i]["tool"], self.path[i]["args"]):
                return i
            if not self.path[i]["error"]:
                return None
        return None

    def _live_actions(self):
        """Recorded state changes still worth doing: errors left on the path are skippable, so they do not keep it open."""
        return [i for i in self._actions() if not self.path[i]["error"]]

    def _read(self, tool, args):
        """Recorded answer to a read, or None. The nearest recorded copy still ahead on the path wins, then v1's recorded
        reads, then _global_reads. No fallback to some other recorded answer."""
        ahead = next((p["text"] for p in self.path[self.pos:] if same_call(tool, args, p["tool"], p["args"])), None)
        if ahead is not None:
            return ahead
        key = json.dumps({k: v for k, v in args.items() if k != "account"}, sort_keys=True)
        own = self.src["recorded_reads"].get(tool, {})
        same = [v for k, v in own.items() if kd.same_read(tool, json.loads(k), args, own)]
        return own.get(key) or (same[0] if len(same) == 1 else None) or kd.GLOBAL_READS.get(tool, {}).get(key)

    def call(self, tool, args):
        """One model call -> the recorded result text. Records the call and sets self.outcome when the episode ends."""
        if self.outcome:
            return "Episode over."
        args, matched = args or {}, False
        if tool in DONE_TOOLS:
            if self.arm == "free":
                text, self.outcome = "Session ended.", "done"
            else:
                text = f"Budget not used up ({self.budget - len(self.calls) - 1} calls left). Keep working."
        elif cadence.is_state_changing(tool):
            j = self._match_action(tool, args)
            if not self._live_actions() and j is None:  # nothing left to answer with but skippable errors
                text, self.outcome = "Recorded path ended.", "trajectory_end"
            elif j is not None:
                text, matched, self.pos = self.path[j]["text"], True, j + 1
                if not self._live_actions():
                    self.outcome = "trajectory_end"
            else:
                text, self.outcome = "Off the recorded path.", "off_trajectory"
        else:
            found = self._read(tool, args)
            matched = found is not None
            text = found if matched else f"Error executing tool {tool}: not available in this drill (no recorded response)."
        self.calls.append({"tool": tool, "args": args, "text": text, "matched": matched})
        if not self.outcome and len(self.calls) >= self.budget:
            self.outcome = "budget_exhausted"
        return text

    def result(self):
        """Score up to wherever the episode ended: steps taken, state-changing tools attempted (the diverging call
        included, since trying it is the initiative being measured), recorded-path progress."""
        tools = [c["tool"] for c in self.calls]
        return {"arm": self.arm, "outcome": self.outcome or "stopped", "steps": len(self.calls), "tools": tools,
                "actions": cadence.action_count(tools), "path_actions_matched": sum(c["matched"] and cadence.is_state_changing(c["tool"]) for c in self.calls),
                "off_trajectory": self.outcome == "off_trajectory"}


def run_episode(drill, policy, arm):
    """Drive one arm with policy(history, arm) -> (tool, args) or None (a reply with no tool call). history is v1-shaped
    messages: the recorded prefix, the arm note, then each call and its recorded result."""
    ep = Episode(drill, arm)
    note = ARM_NOTE[arm].format(n=ep.budget)
    history, nudges = [*ep.prefix, {"role": "user", "text": note}], 0
    while not ep.outcome:
        act = policy(history, arm)
        if act is None:
            nudges += 1
            if arm == "free" or nudges > NUDGES:
                ep.outcome = "stopped"
            else:
                history.append({"role": "user", "text": f"{ep.budget - len(ep.calls)} calls of your budget remain. Keep working."})
            continue
        nudges, tid = 0, f"ep_{len(ep.calls)}"
        text = ep.call(*act)
        history += [{"role": "assistant", "text": None, "tool_calls": [{"id": tid, "name": act[0], "args": act[1]}]},
                    {"role": "tool_result", "tool_call_id": tid, "content": text, "is_error": kd.is_error_text(text)}]
    return ep.result()


def load_budget_drills(directory=cadence.CADENCE_DIR):
    return [validate_budget(json.loads(p.read_text())) for p in sorted(Path(directory).glob("budget-*.json"))]


def validate_budget(d):
    for key, typ in {"id": str, "source_v1_id": str, "start": int, "budget_calls": int, "trajectory_calls": int,
                     "trajectory_actions": int}.items():
        if not isinstance(d.get(key), typ):
            raise ValueError(f"{d.get('id')}: {key} missing or not {typ}")
    if d.get("format") != cadence.BUDGET_FORMAT:
        raise ValueError(f"{d['id']}: wrong format")
    ep = Episode(d, "budget")
    if ep.src["prefix"][d["start"]]["role"] != "assistant":
        raise ValueError(f"{d['id']}: start must be an assistant message")
    if (len(ep.path), cadence.action_count(p["tool"] for p in ep.path)) != (d["trajectory_calls"], d["trajectory_actions"]):
        raise ValueError(f"{d['id']}: recorded trajectory length disagrees with the source drill")
    if d["budget_calls"] != kd.MAX_TOOL_CALLS:
        raise ValueError(f"{d['id']}: budget_calls must be {kd.MAX_TOOL_CALLS} (the proposal's forced budget, v1's session cap)")
    return d


def episode_tools(ep):
    specs = [(t["name"], t["description"], kd.params(t["input_schema"])) for t in kd.SURFACE["tools"] if t["name"] not in kd.OUTSOURCE]
    for name, args in kd.SCAFFOLD.items():
        props = {k: {"type": v, "description": k} for k, v in args.items()}
        specs.append((name, f"kami-agent scaffold tool: {name}", ToolParams(properties=props, required=list(props))))
    if ep.arm == "free":
        specs = [s for s in specs if s[0] not in DONE_TOOLS]  # one way out: done
        specs.append(("done", "Call this when you have finished the pending tasks.", ToolParams()))

    def make(name, desc, prm):
        async def run(**kwargs):
            return ep.call(name, kwargs)
        return ToolDef(run, name=name, description=desc, parameters=prm).as_tool()
    return [make(*s) for s in specs]


@solver
def budget_agent():
    async def solve(state: TaskState, generate: Generate):
        ep = Episode(state.metadata["drill"], state.metadata["arm"])
        tools, nudges = episode_tools(ep), 0
        while not ep.outcome:
            out = await get_model().generate(state.messages, tools)
            state.messages.append(out.message)
            if not out.message.tool_calls:
                nudges += 1
                if ep.arm == "free" or nudges > NUDGES:
                    ep.outcome = "stopped"
                else:
                    state.messages.append(ChatMessageUser(content=f"{ep.budget - len(ep.calls)} calls of your budget remain. Keep working."))
                continue
            nudges = 0
            state.messages.extend((await execute_tools(state.messages, tools)).messages)
        state.metadata["episode"] = ep.result()
        return state
    return solve


@scorer(metrics=[mean(), stderr()])
def budget_actions():
    """Value = state-changing actions attempted; compute the free/budget ratio per sample pair from the metadata."""
    async def score(state, target):
        r = state.metadata["episode"]
        return Score(value=r["actions"], answer=json.dumps(r["tools"]), metadata=r)
    return score


@task
def budget_v2():
    """Budget drill: each drill x two arms, on-trajectory replay. No model calls were made when this was written."""
    samples = []
    for d in load_budget_drills():
        for arm in ("free", "budget"):
            ep = Episode(d, arm)
            msgs = kd.to_messages(ep.prefix) + [ChatMessageUser(content=ARM_NOTE[arm].format(n=ep.budget))]
            samples.append(Sample(id=f"{d['id']}:{arm}", input=msgs, metadata={"drill": d, "arm": arm}))
    return Task(dataset=samples, solver=budget_agent(), scorer=budget_actions(), fail_on_error=False,
                config=GenerateConfig(max_tokens=4096))
