"""kami-drills v2 memory family (docs/v2-memory.md). Specs live in drills_v2/memory/, which the v1 loader never reads.

A v2 spec points at a public v1 drill (`source_drill`) and says what to show and what to score; `materialise` turns it
into v1-shaped drill dicts that kami_drills.task's replay engine runs unchanged (task.py and the v1 suite hash are
untouched). Pure scoring functions take a list of calls [{"tool", "args", "text"}] made after the fork.
"""
import json
import re
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.model import ChatMessageAssistant, ChatMessageTool, GenerateConfig
from inspect_ai.scorer import CORRECT, INCORRECT, Score, accuracy, scorer, stderr
from inspect_ai.util import store

from kami_drills import task as kd

ROOT = Path(__file__).parent.parent
V1 = ROOT / "drills"
V2 = ROOT / "drills_v2" / "memory"
NOTE_PATH = "notes/lessons.md"
KINDS = {"dont_repeat": {"source_drill", "base_len", "failure", "note"},
         "stale_note": {"source_drill", "base_len", "notes"},
         "notes_first": {"source_drill", "base_len", "fact", "note"}}
ARMS = {"dont_repeat": ("a", "b", "c"), "stale_note": ("plain", "dated_newer"), "notes_first": ("only",)}
# shortcut: read-only by name; anything else (incl. act_sequence) counts as state-changing. Revisit if the surface grows.
READ_ONLY = {"workspace_read", "workspace_list", "get_status", "wait", "set_next_wake", "end_session", "quest_state",
             "check_quest_completable", "list_accounts"}


def load(directory=V2):
    return [json.loads(p.read_text()) for p in sorted(Path(directory).glob("*.json"))]


def v1(drill_id):
    return json.loads((V1 / f"{drill_id}.json").read_text())


def error_class(text):
    """Normalised failure reason: lower-cased revert/validation message, digits collapsed. None if text is not an error."""
    if not kd.is_error_text(text):
        return None
    m = (re.search(r"revert: (.+?)(?:: Reverted|'\})", text) or re.search(r"no transaction sent: (.+)", text)
         or re.match(r"Error executing tool \w+: (.+)", text))
    return re.sub(r"\d+", "N", m.group(1).splitlines()[0].lower()).strip(" .:") if m else "error"


def state_changing(tool):
    return tool not in READ_ONLY and not tool.startswith(("lens_", "get_"))


def validate(spec):
    """Schema check; returns a list of problems (empty = ok). Also checks the spec against its source drill."""
    kind = spec.get("kind")
    if kind not in KINDS:
        return [f"unknown kind {kind!r}"]
    bad = [f"missing {k}" for k in sorted(KINDS[kind] | {"id", "format"}) if k not in spec]
    if bad or not (V1 / f"{spec['source_drill']}.json").exists():
        return bad or [f"source_drill {spec['source_drill']!r} is not a v1 drill"]
    src = v1(spec["source_drill"])
    if not 0 < spec["base_len"] <= len(src["prefix"]):
        bad.append("base_len outside the source prefix")
    if kind == "dont_repeat" and not bad:
        call, result = src["prefix"][spec["base_len"]], src["prefix"][spec["base_len"] + 1]
        f = spec["failure"]
        if call["tool_calls"][0]["name"] != f["tool"] or error_class(str(result["content"])) != f["error_class"]:
            bad.append("failure does not match the source transcript at base_len")
    if kind == "stale_note" and set(spec["notes"]) != {"plain", "dated_newer"}:
        bad.append("notes needs plain and dated_newer")
    return bad


def _note_read(note, n):
    return [{"role": "assistant", "text": None, "tool_calls": [{"id": f"note_{n}", "name": "workspace_read",
                                                                  "args": {"path": NOTE_PATH}}]},
            {"role": "tool_result", "tool_call_id": f"note_{n}", "content": note, "is_error": False}]


def materialise(spec, arm):
    """One arm of one spec as a v1-shaped drill dict (prefix, replay, recorded_reads, rule, oracle)."""
    src, kind, cut = v1(spec["source_drill"]), spec["kind"], spec["base_len"]
    out = {k: src[k] for k in ("replay", "recorded_reads", "rule", "oracle")}
    out.update(id=f"{spec['id']}:{arm}", category="memory_v2", v2={"kind": kind, "arm": arm, "spec": spec["id"]})
    if kind == "dont_repeat":
        out["prefix"] = src["prefix"] if arm == "a" else src["prefix"][:cut]  # a: failure excerpt stays in context
        if arm == "b":
            out["prefix"] = out["prefix"] + _note_read(spec["note"], 1)
        out["rule"] = {"require_flags": ["never"]}  # keeps v1's early stop off; the v2 scorer decides
    elif kind == "stale_note":
        out["prefix"] = src["prefix"][:cut] + _note_read(spec["notes"][arm], 1)
    else:  # notes_first: only the session-start message, and the workspace answers with the note
        out["prefix"] = src["prefix"][:cut]
        out["replay"] = [{"tool": "workspace_read", "args": {}, "response": spec["note"]},
                         {"tool": "workspace_list", "args": {}, "response": NOTE_PATH}] + out["replay"]
        out["rule"] = {"require_flags": ["never"]}
    return out


def score_dont_repeat(calls, failure):
    """Pass = the first state-changing call does not repeat the failure (same tool, same normalised error class)."""
    first = next((c for c in calls if state_changing(c["tool"])), None)
    return not (first and first["tool"] == failure["tool"] and error_class(first["text"]) == failure["error_class"])


def score_stale_note(calls, rule, flags=()):
    """Pass = the source drill's v1 rule (the world's answer) holds over the calls, whatever the note said."""
    trace = [{"tool": c["tool"], "args": c["args"], "ok": not kd.is_error_text(c["text"])} for c in calls]
    return not kd.rule_failures(rule, trace, set(flags))


def score_notes_first(calls):
    """Pass = a workspace_read comes before the first state-changing call (or no state change was attempted)."""
    read = next((i for i, c in enumerate(calls) if c["tool"] == "workspace_read"), None)
    change = next((i for i, c in enumerate(calls) if state_changing(c["tool"])), None)
    return read is not None and (change is None or read < change)


def arm_report(rows):
    """rows: [{"drill", "arm", "pass"}] (repeat tries allowed). Per-arm pass rate (mean over tries, then drills) and the
    deltas b-c (note alone) and a-c (transcript excerpt); positive = the arm made repeating less likely."""
    per = {}
    for r in rows:
        per.setdefault(r["arm"], {}).setdefault(r["drill"], []).append(float(r["pass"]))
    rate = {a: sum(sum(v) / len(v) for v in d.values()) / len(d) for a, d in per.items()}
    return {"pass": rate, **{f"{x}-c": rate[x] - rate["c"] for x in "ab" if x in rate and "c" in rate}}


def calls_of(state):
    results = {m.tool_call_id: str(m.content) for m in state.messages if isinstance(m, ChatMessageTool)}
    return [{"tool": tc.function, "args": tc.arguments or {}, "text": results.get(tc.id, "Error")}
            for m in state.messages[state.metadata["fork_len"]:] if isinstance(m, ChatMessageAssistant)
            for tc in m.tool_calls or []]


@scorer(metrics=[accuracy(), stderr()])
def memory_rule():
    async def score(state, target):
        spec, v2, calls = state.metadata["spec"], state.metadata["spec"]["v2"], calls_of(state)
        src = next(s for s in load() if s["id"] == v2["spec"])
        ok = {"dont_repeat": lambda: score_dont_repeat(calls, src["failure"]),
              "stale_note": lambda: score_stale_note(calls, spec["rule"], store().get("flags", set())),
              "notes_first": lambda: score_notes_first(calls)}[v2["kind"]]()
        return Score(value=CORRECT if ok else INCORRECT, answer=json.dumps([c["tool"] for c in calls]),
                        metadata={"arm": v2["arm"], "spec": v2["spec"], "kind": v2["kind"]})
    return score


@task
def memory_v2(policy: str = "model"):
    """STUB: wired to v1's replay engine but not yet run end to end (no model calls were made when it was written)."""
    samples = []
    for spec in load():
        for arm in ARMS[spec["kind"]]:
            m = materialise(spec, arm)
            msgs = kd.to_messages(m["prefix"])
            samples.append(Sample(id=m["id"], input=msgs, metadata={"spec": m, "category": "memory_v2", "fork_len": len(msgs)}))
    return Task(dataset=samples, solver=kd.drill_agent(policy, early_stop=False), scorer=memory_rule(),
                   fail_on_error=False, config=GenerateConfig(max_tokens=4096))
