"""Per-model summaries and paired comparisons from Inspect logs of this task.

Paired test as in kamiarena analysis/paired.py (Miller 2024: paired differences, clustered on the drill): per drill,
each model's score is its mean over tries; a sign-flip permutation on per-drill differences, Holm-corrected across pairs.
"""
import glob
import itertools
import json
import random
from collections import defaultdict
from pathlib import Path

from inspect_ai.log import read_eval_log

from kami_drills import STACK, SUITE_VERSION

PRICES = json.loads((Path(__file__).parent.parent / "results" / "prices-2026-10-16.json").read_text())["models"]


def price_call(model, usage, prices=PRICES):
    """USD for one call at pinned OpenRouter list prices, from its token usage; None for an unpriced model.
    input_tokens excludes cache reads and writes (Inspect subtracts them); output_tokens includes reasoning
    (OpenRouter's completion_tokens does), so reasoning is not priced twice."""
    table = prices.get(model)
    if not table or usage is None:
        return None
    read, write = usage.input_tokens_cache_read or 0, usage.input_tokens_cache_write or 0
    prompt_tokens = usage.input_tokens + read + write
    p = dict(table["pricing"])
    # long-prompt tiers apply; time-of-day windows (deepseek's UTC peak) do not: the base listed price is used
    for o in p.pop("overrides", []):
        if "min_prompt_tokens" in o and prompt_tokens >= o["min_prompt_tokens"]:
            p.update(o)
    p = {k: float(v) for k, v in p.items() if isinstance(v, (str, int, float))}
    # a missing write price: Anthropic's 5-minute write rate (1.25x input); elsewhere writes bill as input
    write_price = p.get("input_cache_write", p["prompt"] * (1.25 if model.startswith("anthropic/") else 1))
    return (usage.input_tokens * p["prompt"] + read * p.get("input_cache_read", p["prompt"]) + write * write_price
            + usage.output_tokens * p["completion"])


def log_files(paths):
    out = []
    for p in paths:
        out += sorted(glob.glob(str(Path(p) / "*.eval"))) if Path(p).is_dir() else [p]
    return out


def collect(logs):
    """Group samples by model; every (log, epoch) is one try. Errored samples are left out, as in the source analysis."""
    runs = {}
    for log in logs:
        model = log.eval.model.removeprefix("openrouter/")
        meta = log.eval.metadata or {}
        r = runs.setdefault(model, {"tries": defaultdict(dict), "category": {}, "logs": [],
                                    "suite_version": meta.get("suite_version"), "stack": meta.get("stack")})
        r["logs"].append({"file": Path(log.location).name if log.location else None, "created": log.eval.created,
                          "revision": log.eval.revision.commit if log.eval.revision else None,
                          "model_args": log.eval.model_args})
        for s in log.samples or []:
            if s.error:
                continue
            score = s.scores["drill_rule"]
            cost = None
            # OpenRouter's per-call `cost` field is missing on many calls, so every call is also priced from its tokens;
            # the larger of the two counts. Neither available: cost stays unknown.
            for e in s.events:
                if e.event != "model":
                    continue
                logged = e.call and (e.call.response or {}).get("usage", {}).get("cost")
                found = [c for c in (price_call(model, e.output.usage), logged) if isinstance(c, (int, float))]
                if found:
                    cost = (cost or 0) + max(found)
            r["tries"][(len(r["logs"]), s.epoch)][s.id] = {"pass": score.value == "C", "gap": bool(score.metadata.get("misses")),
                                                           "cost": cost}
            r["category"][s.id] = s.metadata["category"]
    for r in runs.values():
        r["tries"] = [r["tries"][k] for k in sorted(r["tries"])]
    return runs


def bootstrap_ci(values, n=10000, seed=0):
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(values) for _ in values) / len(values) for _ in range(n))
    return means[int(.025 * n)], means[int(.975 * n) - 1]


def summarise(model, r):
    tries = r["tries"]
    drills = sorted(set.intersection(*(set(t) for t in tries)))
    per = {i: sum(t[i]["pass"] for t in tries) / len(tries) for i in drills}
    attempts = [t[i] for t in tries for i in drills]
    cats = defaultdict(list)
    for i in drills:
        cats[r["category"][i]].append(per[i])
    lo, hi = bootstrap_ci(list(per.values()))
    costs = [a["cost"] for t in tries for a in t.values() if a["cost"] is not None]
    return {
        "model": model, "suite_version": r["suite_version"] or SUITE_VERSION, "stack": r["stack"] or STACK,
        "suite_from_log": r["suite_version"] is not None, "drills": len(drills), "tries": len(tries),
        "pass": sum(per.values()) / len(drills), "ci95": [lo, hi],
        "pass_all_tries": sum(all(t[i]["pass"] for t in tries) for i in drills),
        "tries_disagree": sum(0 < per[i] < 1 for i in drills),
        "gap_rate": sum(a["gap"] for a in attempts) / len(attempts),
        "cost_usd": sum(costs) if costs else None, "category": {c: sum(v) / len(v) for c, v in sorted(cats.items())},
        "category_n": {c: len(v) for c, v in sorted(cats.items())},
        "per_drill": {i: [t[i]["pass"] for t in tries] for i in drills},
        "per_drill_gap": {i: [t[i]["gap"] for t in tries] for i in drills},
        "logs": r["logs"],
    }


def signflip_p(diffs, n=20000, seed=0):
    rng = random.Random(seed)
    obs = abs(sum(diffs))
    hits = sum(abs(sum(d if rng.random() < .5 else -d for d in diffs)) >= obs - 1e-12 for _ in range(n))
    return (hits + 1) / (n + 1)


def holm(ps):
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    adj, running = [0.0] * len(ps), 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(ps) - rank) * ps[i]))
        adj[i] = running
    return adj


def paired(summaries):
    score = {s["model"]: {i: sum(v) / len(v) for i, v in s["per_drill"].items()} for s in summaries}
    if len(score) < 2:
        return []
    drills = sorted(set.intersection(*(set(v) for v in score.values())))
    pairs = list(itertools.combinations(sorted(score, key=lambda m: -sum(score[m].values())), 2))
    rows = []
    for a, b in pairs:
        diffs = [score[a][i] - score[b][i] for i in drills]
        rows.append((a, b, sum(diffs) / len(drills), signflip_p([d for d in diffs if d])))
    adj = holm([r[3] for r in rows])
    return sorted(({"a": a, "b": b, "diff": d, "p": p, "q": q, "drills": len(drills)}
                   for (a, b, d, p), q in zip(rows, adj)), key=lambda r: r["p"])


def load(paths, override=()):
    """`override` logs replace, try by try, the drills they hold in the same model's `paths` logs (a re-run of fixed
    drills); try k of the override pairs with try k of the base, in log then epoch order."""
    runs = collect(read_eval_log(f) for f in log_files(paths))
    for model, o in collect(read_eval_log(f) for f in log_files(override)).items():
        r = runs.get(model)
        if not r or len(r["tries"]) != len(o["tries"]):
            raise ValueError(f"override for {model}: {len(o['tries'])} tries, base has {len(r['tries']) if r else 0}")
        r["tries"] = [{**t, **f} for t, f in zip(r["tries"], o["tries"])]
        r["category"].update(o["category"])
        r["logs"] += o["logs"]
    return sorted((summarise(m, r) for m, r in runs.items()), key=lambda s: -s["pass"])


if __name__ == "__main__":
    # self-check: identical models never differ; a clear gap is detected
    assert signflip_p([]) == 1.0 and signflip_p([1.0] * 12) < .01 and holm([.01, .04]) == [.02, .04]
    assert bootstrap_ci([1.0] * 5) == (1.0, 1.0)
    print("ok")
