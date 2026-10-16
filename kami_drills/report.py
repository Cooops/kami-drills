"""Per-model summaries and paired comparisons from Inspect logs of this task.

Paired test as in kamiarena analysis/paired.py (Miller 2024: paired differences, clustered on the drill): per drill,
each model's score is its mean over tries; a sign-flip permutation on per-drill differences, Holm-corrected across pairs.
"""
import glob
import itertools
import random
from collections import defaultdict
from pathlib import Path

from inspect_ai.log import read_eval_log

from kami_drills import STACK, SUITE_VERSION


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
        r = runs.setdefault(model, {"tries": defaultdict(dict), "category": {}, "cost": None, "logs": [],
                                    "suite_version": meta.get("suite_version"), "stack": meta.get("stack")})
        r["logs"].append({"file": Path(log.location).name if log.location else None, "created": log.eval.created,
                          "revision": log.eval.revision.commit if log.eval.revision else None,
                          "model_args": log.eval.model_args})
        for s in log.samples or []:
            if s.error:
                continue
            score = s.scores["drill_rule"]
            r["tries"][(len(r["logs"]), s.epoch)][s.id] = {"pass": score.value == "C", "gap": bool(score.metadata.get("misses"))}
            r["category"][s.id] = s.metadata["category"]
            # OpenRouter reports the billed cost per call; other providers do not, and cost stays unknown
            for e in s.events:
                cost = e.event == "model" and e.call and (e.call.response or {}).get("usage", {}).get("cost")
                if isinstance(cost, (int, float)):
                    r["cost"] = (r["cost"] or 0) + cost
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
    return {
        "model": model, "suite_version": r["suite_version"] or SUITE_VERSION, "stack": r["stack"] or STACK,
        "suite_from_log": r["suite_version"] is not None, "drills": len(drills), "tries": len(tries),
        "pass": sum(per.values()) / len(drills), "ci95": [lo, hi],
        "pass_all_tries": sum(all(t[i]["pass"] for t in tries) for i in drills),
        "tries_disagree": sum(0 < per[i] < 1 for i in drills),
        "gap_rate": sum(a["gap"] for a in attempts) / len(attempts),
        "cost_usd": r["cost"], "category": {c: sum(v) / len(v) for c, v in sorted(cats.items())},
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


def load(paths):
    runs = collect(read_eval_log(f) for f in log_files(paths))
    return sorted((summarise(m, r) for m, r in runs.items()), key=lambda s: -s["pass"])


if __name__ == "__main__":
    # self-check: identical models never differ; a clear gap is detected
    assert signflip_p([]) == 1.0 and signflip_p([1.0] * 12) < .01 and holm([.01, .04]) == [.02, .04]
    assert bootstrap_ci([1.0] * 5) == (1.0, 1.0)
    print("ok")
