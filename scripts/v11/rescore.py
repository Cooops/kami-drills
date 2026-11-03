"""Re-score the recorded rc2 v1 tries (B-data/plays.json, 11 models x 2 tries x 47 drills) under v1.1 through the 4.7.0
adapter, and compare per-drill verdicts and the corrected point-biserial. Sensitivity only: the trajectories were
generated under v1 (v1 replay answers, v1 early stop, no stated objective)."""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import conv  # noqa: E402
import eq  # noqa: E402
from kami_drills.spec import from_v1  # noqa: E402

DIR = conv.WT / "specs/v1.1"
MAN = {m["id"]: m for m in json.loads((DIR / "MANIFEST.json").read_text())["drills"]}
models = sorted(eq.PLAYS)
resp = [(m, t) for m in models for t in range(2)]


def corr(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    return None if a.std() == 0 or b.std() == 0 else float(np.corrcoef(a, b)[0, 1])


def verdicts():
    v1, v11, eqv = {}, {}, {}
    for i in conv.all_ids():
        spec = json.loads((DIR / f"{i}.json").read_text())
        rows = {(r["model"], r["try"]): r for r in from_v1.equivalence(spec, eq.tries(i))}
        base = from_v1.convert(conv.load_v1(i), conv.overlay(conv.load_v1(i)))  # mechanical v1-equivalent conversion
        rb = {(r["model"], r["try"]): r for r in from_v1.equivalence(base, eq.tries(i))}
        v1[i] = [int(rows[k]["v1"]) for k in resp]
        v11[i] = [int(rows[k]["v2"]) for k in resp]
        eqv[i] = [int(rb[k]["v2"]) for k in resp]
    return v1, v11, eqv


def item_r(X, items):
    M = np.array([X[i] for i in items]).T  # respondents x items
    tot = M.sum(1)
    return {i: corr(M[:, j], tot - M[:, j]) for j, i in enumerate(items)}


if __name__ == "__main__":
    v1, v11, eqv = verdicts()
    ids = conv.all_ids()
    rank = [i for i in ids if MAN[i]["role"] == "ranking"]
    r_v1_all = item_r(v1, ids)
    r_v1_rank, r_v11_rank = item_r(v1, rank), item_r(v11, rank)
    out = {"per_drill": {}, "summary": {}}
    for i in ids:
        ch = [f"{m}#{t}:{'P' if a else 'F'}->{'P' if b else 'F'}" for (m, t), a, b in zip(resp, v1[i], v11[i]) if a != b]
        out["per_drill"][i] = {"role": MAN[i]["role"], "v1_pass": sum(v1[i]), "v11_pass": sum(v11[i]), "equiv_pass": sum(eqv[i]),
                               "equiv_match": sum(a == b for a, b in zip(v1[i], eqv[i])), "changes": ch,
                               "r_v1_all47": r_v1_all[i], "r_v1_rank": r_v1_rank.get(i), "r_v11_rank": r_v11_rank.get(i)}
    mean = lambda d: float(np.mean([v for v in d.values() if v is not None]))
    neg = lambda d: sorted(k for k, v in d.items() if v is not None and v < 0)
    out["summary"] = {
        "n_ranking": len(rank), "equivalence_match": sum(r["equiv_match"] for r in out["per_drill"].values()), "equivalence_n": 22 * len(ids),
        "verdict_changes_ranking": sum(len(out["per_drill"][i]["changes"]) for i in rank),
        "drills_changed_ranking": sum(bool(out["per_drill"][i]["changes"]) for i in rank),
        "mean_r_v1_all47": mean(r_v1_all), "reversed_v1_all47": neg(r_v1_all),
        "mean_r_v1_ranking_items": mean(r_v1_rank), "reversed_v1_ranking_items": neg(r_v1_rank),
        "mean_r_v11_ranking_items": mean(r_v11_rank), "reversed_v11_ranking_items": neg(r_v11_rank),
        "pass_rate_v1_ranking": float(np.mean([v1[i] for i in rank])), "pass_rate_v11_ranking": float(np.mean([v11[i] for i in rank])),
    }
    model_v1 = {m: np.mean([v1[i][2 * k + t] for i in rank for t in range(2)]) for k, m in enumerate(models)}
    model_v11 = {m: np.mean([v11[i][2 * k + t] for i in rank for t in range(2)]) for k, m in enumerate(models)}
    out["summary"]["model_mean_ranking"] = {m: [round(model_v1[m], 3), round(model_v11[m], 3)] for m in models}
    Path(sys.argv[1] if len(sys.argv) > 1 else "rescore_v11.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out["summary"], indent=1))
    for i in ids:
        d = out["per_drill"][i]
        fmt = lambda x: "  -  " if x is None else f"{x:+.2f}"
        print(f"{d['role'][:4]} {i[:42]:42s} v1 {d['v1_pass']:2d} eq {d['equiv_pass']:2d} v1.1 {d['v11_pass']:2d}  r {fmt(d['r_v1_rank'])} -> {fmt(d['r_v11_rank'])}  {' '.join(d['changes'])[:150]}")
