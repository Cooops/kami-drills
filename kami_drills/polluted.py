"""Scoring helper and Inspect task for polluted-context variants (docs/v2-polluted.md)."""
import json
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.model import GenerateConfig

from kami_drills import task as kd

POLLUTED = Path(__file__).parent.parent / "drills_v2" / "polluted"


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
def polluted_v2(policy: str = "model", early_stop: bool = True):
    """The 12 polluted variants under v1's replay engine, rule and scorer (nothing in drills/ or task.py changes)."""
    samples = []
    for p in sorted(POLLUTED.glob("*.json")):
        s = json.loads(p.read_text())
        msgs = kd.to_messages(s["prefix"], 0, s.get("keep_lines", []))
        samples.append(Sample(id=s["id"], input=msgs, metadata={"spec": s, "category": s["category"], "fork_len": len(msgs)}))
    return Task(dataset=samples, solver=kd.drill_agent(policy, early_stop), scorer=kd.drill_rule(), fail_on_error=False,
                config=GenerateConfig(max_tokens=4096))
