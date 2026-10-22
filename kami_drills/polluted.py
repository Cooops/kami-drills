"""Scoring helper, Inspect task and per-model own-error variants for polluted-context drills (docs/v2-polluted.md)."""
import json
import re
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.model import ChatMessageAssistant, ChatMessageTool, GenerateConfig

from kami_drills import task as kd

ROOT = Path(__file__).parent.parent
POLLUTED = ROOT / "drills_v2" / "polluted"
INFRA = re.compile(r"pool reached max|failed to insert tx|timed? ?out|rate.?limit|too many requests|connection", re.I)
MAX_ERRORS, MAX_CHARS = 2, 1500  # per variant; skip bulky error replies


def eligible_error(tool, text):
    """A failure worth injecting: the tool is on the live surface (not hidden OUTSOURCE) and the error is the agent's
    own doing, not infrastructure (RPC pool, timeouts, rate limits)."""
    return tool not in kd.OUTSOURCE and not INFRA.search(text) and len(text) <= MAX_CHARS


def own_error_variants(clean_eval_log, drills=None, max_errors=MAX_ERRORS):
    """Self-conditioning design: one model's clean-drill eval log -> {variant id: v1-shaped drill} that replays that
    model's OWN failed calls (call + error reply, in order) right after "Session start." of the same drill.
    Run the result as a second pass for the same model (`polluted_v2(specs=...)`) and compare with `polluted_delta`.
    Needs sample ids = v1 drill ids and metadata fork_len (what drill_agent logs). Drills where the model made no
    eligible failed call are left out. Reads a log; makes no model call."""
    drills = drills or {d["id"]: d for d in (json.loads(p.read_text()) for p in sorted((ROOT / "drills").glob("*.json"))
                                              if not p.name.startswith("_"))}
    out = {}
    for smp in clean_eval_log.samples or []:
        if smp.id not in drills:
            continue
        msgs = smp.messages[smp.metadata["fork_len"]:]
        results = {m.tool_call_id: str(m.content) for m in msgs if isinstance(m, ChatMessageTool)}
        seen, inj = set(), []
        for tc in (tc for m in msgs if isinstance(m, ChatMessageAssistant) for tc in m.tool_calls or []):
            text, key = results.get(tc.id, ""), json.dumps([tc.function, tc.arguments], sort_keys=True)
            if kd.is_error_text(text) and eligible_error(tc.function, text) and key not in seen:
                seen.add(key)
                inj.append((tc, text))
        if not inj:
            continue
        inj = inj[:max_errors]
        d, new = drills[smp.id], []
        for n, (tc, text) in enumerate(inj):  # fresh ids: recorded v1 ids (call_1) repeat across turns
            new += [{"role": "assistant", "text": None, "tool_calls": [{"id": f"own_{n}", "name": tc.function, "args": tc.arguments or {}}]},
                    {"role": "tool_result", "tool_call_id": f"own_{n}", "content": text, "is_error": True}]
        v = dict(d, id=d["id"] + "--own-error", prefix=d["prefix"][:1] + new + d["prefix"][1:],
                 keep_lines=[k + len(new) if k >= 1 else k for k in d.get("keep_lines", [])])
        v["polluted"] = {"family": "own_error", "source_drill": d["id"], "inserted_messages": len(new),
                         "injected": [{"tool": tc.function, "args": tc.arguments or {}, "error": text} for tc, text in inj]}
        out[v["id"]] = v
    return out


def polluted_delta(clean_pass, polluted_pass):
    """One model's pass-rate change under pollution: polluted minus clean, negative = hurt.

    Each argument maps v1 drill id -> per-try pass flags (the `per_drill` shape in results/*/models/*.json).
    v1 rule: mean over tries per drill, then mean over drills; only drills present in both count.
    """
    ids = sorted(set(clean_pass) & set(polluted_pass))
    if not ids:
        raise ValueError("no drills in common")
    mean = lambda tries: sum(tries) / len(tries)
    return sum(mean(polluted_pass[i]) - mean(clean_pass[i]) for i in ids) / len(ids)


@task
def polluted_v2(policy: str = "model", early_stop: bool = True, specs=None):
    """The static robustness variants (or `specs`, e.g. own_error_variants(...).values()) under v1's replay engine, rule
    and scorer (nothing in drills/ or task.py changes)."""
    samples = []
    for s in specs or [json.loads(p.read_text()) for p in sorted(POLLUTED.glob("*.json"))]:
        msgs = kd.to_messages(s["prefix"], 0, s.get("keep_lines", []))
        samples.append(Sample(id=s["id"], input=msgs, metadata={"spec": s, "category": s["category"], "fork_len": len(msgs)}))
    return Task(dataset=samples, solver=kd.drill_agent(policy, early_stop), scorer=kd.drill_rule(), fail_on_error=False,
                config=GenerateConfig(max_tokens=4096))
