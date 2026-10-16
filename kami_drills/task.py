"""kami-drills: forked real transcripts, replayed tools, deterministic scoring. Replay engine and judge.

Run:  uv run kami-drills run --model <provider/model>
      uv run inspect eval kami_drills/task.py --model <provider/model> --epochs 2
      uv run inspect eval kami_drills/task.py -T policy=oracle   (scripted correct play, must pass)
      uv run inspect eval kami_drills/task.py -T policy=null     (does nothing, must fail)
"""
import json
import re
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.model import GenerateConfig
from inspect_ai.dataset import Sample
from inspect_ai.model import (ChatMessageAssistant, ChatMessageSystem, ChatMessageTool, ChatMessageUser,
                              ModelOutput, execute_tools, get_model)
from inspect_ai.scorer import CORRECT, INCORRECT, Score, Target, accuracy, scorer, stderr
from inspect_ai.solver import Generate, TaskState, solver
from inspect_ai.tool import ToolCall, ToolDef
from inspect_ai.tool._tool_params import ToolParams
from inspect_ai.util import store

from kami_drills import STACK, SUITE_VERSION

HERE = Path(__file__).parent
DRILLS = HERE.parent / "drills"  # built specs, one per drill
SURFACE = json.loads((HERE / "surface" / "harness_tools.json").read_text())
SYSTEM = (HERE / "surface" / "system.txt").read_text().strip() + "\n\n" + SURFACE["standing_text"]
MAX_TURNS = 30
MAX_TOOL_CALLS = 50  # kami-agent session_tool_cap
GLOBAL_READS = json.loads((DRILLS / "_global_reads.json").read_text())
END_TOOLS = {"end_session", "set_next_wake"}
# kami-agent's scaffold tools (src/kami_agent/tools/scaffold.py, 0.7.5); arguments are not modelled beyond names
SCAFFOLD = {
    "workspace_write": {"path": "string", "content": "string"}, "workspace_read": {"path": "string"},
    "workspace_list": {}, "workspace_delete": {"path": "string"}, "set_next_wake": {"minutes_from_now": "number"},
    "get_status": {}, "end_session": {"reason": "string"}, "wait": {"seconds": "number"},
}


def trim(prefix, max_tokens, keep=()):
    """Keep the session-start messages (before the first assistant turn: brief, plan), every evidence line the drill
    declares (`keep`, 1-based transcript lines, with their call/result partners), and the longest tail that starts at an
    assistant turn and fits max_tokens (~4 chars/token). Gaps get an omission note. 0 keeps the full history."""
    size = lambda ms: sum(len(json.dumps(m)) for m in ms) // 4
    if not max_tokens or size(prefix) <= max_tokens:
        return prefix
    head_end = next((i for i, m in enumerate(prefix) if m["role"] == "assistant"), len(prefix))
    kept = set(range(head_end))
    call_of = {}  # tool_call_id -> index of the assistant message that made it
    for i, m in enumerate(prefix):
        for tc in m.get("tool_calls") or []:
            call_of[tc["id"]] = i
    def close(idx):  # an assistant turn needs all its results; a result needs its call (and that call's other results)
        todo, out = list(idx), set()
        while todo:
            i = todo.pop()
            if i in out or not 0 <= i < len(prefix):
                continue
            out.add(i)
            m = prefix[i]
            if m["role"] == "tool_result" and m.get("tool_call_id") in call_of:
                todo.append(call_of[m["tool_call_id"]])
            ids = {tc["id"] for tc in m.get("tool_calls") or []}
            todo += [k for k, r in enumerate(prefix) if r.get("tool_call_id") in ids]
        return out
    kept |= close([k - 1 for k in keep])
    budget = max_tokens - size([prefix[i] for i in sorted(kept)])
    starts = [i for i in range(head_end, len(prefix)) if prefix[i]["role"] == "assistant"]
    tail_start = next((i for i in starts if size([prefix[k] for k in range(i, len(prefix)) if k not in kept]) <= budget),
                      starts[-1] if starts else len(prefix))
    kept |= set(range(tail_start, len(prefix)))
    out, prev = [], -1
    for i in sorted(kept):
        if i != prev + 1:
            out.append({"role": "user", "text": "(Earlier messages from this session are omitted to save context.)"})
        out.append(prefix[i])
        prev = i
    return out


def to_messages(prefix, max_tokens=0, keep=()):
    msgs, pending, used, ids = [ChatMessageSystem(content=SYSTEM)], {}, set(), {}
    for m in trim(prefix, max_tokens, keep):
        if m["role"] == "user":
            msgs.append(ChatMessageUser(content=m["text"]))
        elif m["role"] == "assistant":
            for t in m.get("tool_calls") or []:  # recorded ids repeat across turns (call_1); providers need unique ids
                new = t["id"]
                while new in used:
                    new += "_"
                used.add(new)
                ids[t["id"]] = new
            tcs = [ToolCall(id=ids[t["id"]], function=t["name"], arguments=t.get("args") or {}) for t in m.get("tool_calls") or []]
            msgs.append(ChatMessageAssistant(content=m.get("text") or "", tool_calls=tcs or None))
            pending.update({t.id: t.function for t in tcs})
        elif m["role"] == "tool_result":
            tid = ids.get(m["tool_call_id"], m["tool_call_id"])
            msgs.append(ChatMessageTool(content=str(m["content"]), tool_call_id=tid, function=pending.get(tid)))
    return msgs


def replay_tool(name, description, params, spec):
    """One mocked tool: first matching replay row wins. A row may match on args (same pattern language as rules),
    require flags (`when`, all of them) or their absence (`unless`), require total waiting so far (`waited_gte`,
    seconds summed over every wait call) or less than it (`waited_lt`, e.g. a cooldown revert that holds only until the
    cooldown has been waited out), and set or clear flags after it fires."""
    async def run(**kwargs):
        if name == "act_sequence":  # no recorded act_sequence exists: answer each step as its single-action tool
            out = []
            for step in kwargs.get("steps") or []:
                tool, args = sequence_step(step)
                text = lookup(tool, args) if tool else f"unknown op {step.get('op')!r}"
                out.append({"op": step.get("op"), "status": "reverted" if is_error_text(text) else "success", "result": text})
            return json.dumps({"steps": out})
        return lookup(name, kwargs)

    def lookup(name, kwargs):
        flags = store().get("flags", set())
        if name == "wait":  # waits add up: "wait 60, re-check, wait 70" covers a 125 s cooldown
            store().set("waited_s", store().get("waited_s", 0) + float(kwargs.get("seconds") or 0))
        waited = store().get("waited_s", 0)
        for row in spec["replay"]:
            if row["tool"] != name or not args_match(kwargs, row.get("args", {})):
                continue
            if not set(as_list(row.get("when"))) <= flags or set(as_list(row.get("unless"))) & flags:
                continue
            if waited < row.get("waited_gte", 0) or waited >= row.get("waited_lt", float("inf")):
                continue
            store().set("flags", (flags | set(as_list(row.get("sets")))) - set(as_list(row.get("clears"))))
            return row["response"]
        key = json.dumps({k: v for k, v in kwargs.items() if k != "account"}, sort_keys=True)
        own = spec["recorded_reads"].get(name, {})  # reads only, keyed by args: never another call's answer
        if key in own:
            return own[key]
        same = [v for k, v in own.items() if same_read(name, json.loads(k), kwargs, own)]
        if len(same) == 1:
            return same[0]
        if own and not any(k.endswith("index") or k.endswith("_id") or k.endswith("_ids") for k in kwargs):
            return next(iter(own.values()))  # no target argument: e.g. lens_roster() vs lens_roster(full=false)
        recorded = GLOBAL_READS.get(name, {})
        if key in recorded:
            return recorded[key]
        if recorded and not any(k.endswith("index") or k.endswith("_id") for k in kwargs):
            return next(iter(recorded.values()))  # arg-free world read (market, config, phase): any recording is the same world
        if name in SCAFFOLD:
            return {"set_next_wake": "Next session scheduled.", "end_session": "Session ended."}.get(name, "ok")
        store().set("misses", store().get("misses", []) + [name])
        return f"Error executing tool {name}: not available in this drill (no recorded response)."
    return ToolDef(run, name=name, description=description, parameters=params).as_tool()


def params(schema):
    # Inspect requires a description per parameter; the harness documents params in the tool description instead.
    # ponytail: param name as description is a small deviation from the live tool surface; record it in METHODS.
    props = {k: {**v, "description": v.get("description") or k} for k, v in schema.get("properties", {}).items()}
    return ToolParams(properties=props, required=schema.get("required", []))


# OUTSOURCE class (kami-harness 4.7.0): the Kamibots strategy service. Hidden in Season 1 (the author runs Kamibots;
# drills and live runs must offer the same tools). Documented as a deviation from the default stack.
OUTSOURCE = {"get_tier", "get_all_strategies", "get_all_strategy_statuses", "get_strategy_status", "get_strategy_logs",
             "start_strategy", "stop_strategy", "kamibots_enable_strategies", "register_kamibots"}


def tools_for(spec):
    tools = [replay_tool(t["name"], t["description"], params(t["input_schema"]), spec)
             for t in SURFACE["tools"] if t["name"] not in OUTSOURCE]
    for name, args in SCAFFOLD.items():
        props = {k: {"type": v, "description": k} for k, v in args.items()}
        tools.append(replay_tool(name, f"kami-agent scaffold tool: {name}", ToolParams(properties=props, required=list(props)), spec))
    return tools


DEFAULTS = {t["name"]: {k: v["default"] for k, v in t["input_schema"].get("properties", {}).items() if "default" in v}
            for t in SURFACE["tools"]}
OWN = ("account_index", "account_key")


def same_read(name, recorded, called, own):
    """Two spellings of one read: schema defaults written out or omitted, and "my account" as -1/"main"/omitted vs the
    account's own index or label, which is unambiguous only when every recording of this tool targets one account."""
    norm = lambda a: {k: v for k, v in a.items() if k != "account" and DEFAULTS.get(name, {}).get(k, object()) != v}
    r, c = norm(recorded), norm(called)
    for f in OWN:
        targets = {json.dumps(json.loads(k).get(f)) for k in own} - {"null"}
        if c.get(f) in (None, -1, "main", "") and len(targets) <= 1:
            r.pop(f, None), c.pop(f, None)
    return r == c


def sequence_step(step):
    """act_sequence step -> the single-action tool and args it stands for (harness 4.7.0 step schema)."""
    op = step.get("op")
    if op == "feed":
        return "feed_kami", {"kami_id": step.get("kami_id"), "food_item_id": step.get("item_id")}
    if op in ("harvest_start", "harvest_stop"):
        args = {"kami_ids": step.get("kami_ids")}
        return op, ({**args, "node_index": step.get("node_index")} if op == "harvest_start" else args)
    if op == "liquidate":
        return "liquidate_kami", {"killer_kami_id": step.get("kami_id"), "victim_kami_id": step.get("victim_kami_id")}
    return None, {}


def build_trace(state):
    results = {m.tool_call_id: str(m.content) for m in state.messages if isinstance(m, ChatMessageTool)}
    trace = []
    for m in state.messages[state.metadata["fork_len"]:]:
        if not isinstance(m, ChatMessageAssistant):
            continue
        for tc in m.tool_calls or []:
            text = results.get(tc.id, "Error")
            trace.append({"tool": tc.function, "args": tc.arguments or {}, "ok": not is_error_text(text)})
            if tc.function == "act_sequence":  # rules see each step as the single action it stands for
                try:
                    rows = json.loads(text)["steps"]
                except (ValueError, KeyError, TypeError):
                    rows = []
                for step, row in zip((tc.arguments or {}).get("steps") or [], rows):
                    tool, args = sequence_step(step)
                    if tool:
                        trace.append({"tool": tool, "args": args, "ok": row.get("status") == "success"})
    return trace


def settled(rule, trace, flags):
    """True once no further call can change the verdict: a forbidden call already happened (fail), or every requirement
    is met and nothing left in the rule can still be violated (pass)."""
    hard_fail = ["forbidden call", "forbidden before", "end state has"]
    why = rule_failures(rule, trace, flags)
    if any(w.startswith(tuple(hard_fail)) for w in why):
        return True
    open_pairs = [p for p in rule.get("forbid_before", []) if first_index(trace, p["before"]) == len(trace)]
    if "forbid_before_any" in rule and first_index(trace, rule["before"]) == len(trace):
        open_pairs.append(True)
    return not why and not rule.get("forbid") and not rule.get("forbid_flags") and not open_pairs


@solver
def drill_agent(policy: str = "model", early_stop: bool = True):
    async def solve(state: TaskState, generate: Generate):
        spec = state.metadata["spec"]
        tools = tools_for(spec)
        mode = policy or "null"  # `-T policy=null` arrives as None (parsed as YAML null)
        if mode == "oracle":
            scripted = spec.get("oracle", [])
        elif mode.startswith("script:"):  # script:<file.json> = {drill_id: [[tool, args], ...]}, for testing named plays
            scripted = json.loads(Path(mode[7:]).read_text()).get(spec["id"], [])
        else:
            scripted = []
        for turn in range(MAX_TURNS):
            if mode == "null":
                break
            if mode == "oracle" or mode.startswith("script:"):
                if turn >= len(scripted):
                    break
                fn, args = scripted[turn]
                out = ModelOutput.for_tool_call("oracle", fn, args)
            else:
                out = await get_model().generate(state.messages, tools)
            state.messages.append(out.message)
            if not out.message.tool_calls:
                break
            result = await execute_tools(state.messages, tools)
            state.messages.extend(result.messages)
            calls = store().get("calls", 0) + len(out.message.tool_calls)
            store().set("calls", calls)
            if calls >= MAX_TOOL_CALLS:
                break
            if any(tc.function in END_TOOLS for tc in out.message.tool_calls):
                break
            if early_stop and settled(spec["rule"], build_trace(state), store().get("flags", set())):
                store().set("early_stop", turn + 1)
                break
        return state
    return solve


def is_error_text(text):
    """A JSON reply fails only if its own error field is set or its status says so (a quest read carries a
    'revert_reason' field that is not a failure); plain text fails on the harness's error/revert wording."""
    try:
        d = json.loads(text)
    except ValueError:
        t = text.lower()
        return t.startswith("error") or "validation failed" in t or "reverted" in t or "not available in this drill" in t
    if not isinstance(d, dict):
        return False
    return d.get("error") not in (None, "", False) or str(d.get("status", "")).lower() in ("reverted", "failed", "error")


def as_list(x):
    return [] if x is None else x if isinstance(x, list) else [x]


def value_match(actual, want):
    """want is a literal (equality) or operators: in, not, not_in, contains (list member or text substring), regex, gte/lte/gt/lt (numbers, numeric strings)."""
    ops = ("in", "not", "not_in", "contains", "regex", "gte", "lte", "gt", "lt")
    if isinstance(want, dict) and want and all(k in ops for k in want):  # several ops = all must hold (a range)
        num = lambda: float(actual) if isinstance(actual, (int, float, str)) and str(actual).replace(".", "", 1).lstrip("-").isdigit() else None
        checks = {"in": lambda v: actual in v, "not": lambda v: actual != v, "not_in": lambda v: actual not in v,
                  # list membership, or case-insensitive substring for text (e.g. a note's content)
                  "contains": lambda v: (v in actual) if isinstance(actual, list) else isinstance(actual, str) and str(v).lower() in actual.lower(),
                  "regex": lambda v: isinstance(actual, str) and re.search(v, actual, re.I | re.S) is not None,
                  "gte": lambda v: num() is not None and num() >= v, "lte": lambda v: num() is not None and num() <= v,
                  "gt": lambda v: num() is not None and num() > v, "lt": lambda v: num() is not None and num() < v}
        return all(checks[op](v) for op, v in want.items())
    return actual == want


def args_match(args, want):
    return all(value_match(args.get(k), v) for k, v in want.items())


def matches(call, pat):
    """pat: {"tool": name | [names], "args": {...}, "ok": true (only calls that did not error)}; a list of pats = any of."""
    if isinstance(pat, list):
        return any(matches(call, p) for p in pat)
    if isinstance(pat, str):
        pat = {"tool": pat}
    return (call["tool"] in as_list(pat["tool"]) and args_match(call["args"], pat.get("args", {}))
            and ratio_match(call["args"], pat.get("ratio")) and (not pat.get("ok") or call["ok"]))


def ratio_match(args, r):
    """r: {"num": arg, "den": arg, <ops>}, e.g. a unit price (MUSU offered / units asked) against the shown market."""
    if not r:
        return True
    try:
        ratio = float(args.get(r["num"])) / float(args.get(r["den"]))
    except (TypeError, ValueError, ZeroDivisionError):
        return False
    return value_match(ratio, {k: v for k, v in r.items() if k not in ("num", "den")})


def first_index(trace, pats):
    return next((i for i, c in enumerate(trace) if any(matches(c, p) for p in pats)), len(trace))


def rule_failures(rule, trace, flags=frozenset()):
    why = []
    if set(rule.get("require_flags", [])) - set(flags):  # end state the correct play reaches (replay flags)
        why.append(f"end state missing {sorted(set(rule['require_flags']) - set(flags))}")
    if set(rule.get("forbid_flags", [])) & set(flags):
        why.append(f"end state has {sorted(set(rule['forbid_flags']) & set(flags))}")
    if any(matches(c, p) for c in trace for p in rule.get("forbid", [])):
        why.append("forbidden call")
    pairs = list(rule.get("forbid_before", []))
    if "forbid_before_any" in rule:  # legacy single pair; `before` holds tool names
        pairs.append({"forbid": rule["forbid_before_any"], "before": rule["before"]})
    for pair in pairs:
        cut = first_index(trace, pair["before"])
        if any(matches(c, p) for c in trace[:cut] for p in pair["forbid"]):
            why.append(f"forbidden before {pair['before']}")
    groups = [rule["require_any"]] if "require_any" in rule else []
    groups += rule.get("require_all", [])  # every group needs at least one matching call
    if any(not any(matches(c, p) for c in trace for p in g) for g in groups):
        why.append("no required action")
    for req in rule.get("require_count", []):
        if sum(any(matches(c, p) for p in req["any"]) for c in trace) < req["n"]:
            why.append(f"fewer than {req['n']} of {req['any']}")
    if "require_sequence" in rule:  # each step is a pattern or a list of alternatives, matched in order
        seq = iter(trace)
        if not all(any(matches(c, step) for c in seq) for step in rule["require_sequence"]):
            why.append("required sequence missing")
    return why


@scorer(metrics=[accuracy(), stderr()])
def drill_rule():
    async def score(state: TaskState, target: Target):
        # score every attempted call after the fork, including ones the tool layer rejected on bad arguments
        rule = state.metadata["spec"]["rule"]
        trace = build_trace(state)
        why = rule_failures(rule, trace, store().get("flags", set()))
        return Score(value=INCORRECT if why else CORRECT, answer=json.dumps([c["tool"] for c in trace]),
                     explanation="; ".join(why) or "pass", metadata={"misses": store().get("misses", []), "calls": len(trace),
                               "early_stop": store().get("early_stop"), "flags": sorted(store().get("flags", set()))})
    return score


@task
def kamidrills(policy: str = "model", max_prefix_tokens: int = 0, early_stop: bool = True):
    """Full histories by default: trimming to 16k changed verdicts for gpt-6.1-sol (92% run-to-run vs 78% full-vs-trim,
    analysis/out/trim_validation.md), so it fails the pre-set >=90% agreement rule and is off."""
    specs = [json.loads(p.read_text()) for p in sorted(DRILLS.glob("*.json")) if not p.name.startswith("_")]
    samples = []
    for s in specs:
        msgs = to_messages(s["prefix"], max_prefix_tokens, s.get("keep_lines", []))
        samples.append(Sample(id=s["id"], input=msgs, metadata={"spec": s, "category": s["category"], "fork_len": len(msgs)}))
    return Task(dataset=samples, solver=drill_agent(policy, early_stop), scorer=drill_rule(), fail_on_error=False,
                metadata={"suite_version": SUITE_VERSION, "stack": STACK},
                # kami-agent 0.7.5 manifest defaults: max_tokens 4096; temperature and reasoning effort left to the provider
                config=GenerateConfig(max_tokens=4096))
