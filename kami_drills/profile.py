"""Per-skill-area profile of recorded drill tries (docs/v2-profile.md). Each scored drill maps to one area
(skill_areas.json); an area's score is the mean drill score with a bootstrap 95% CI over its drills, and the aggregate
is the equal-weight mean of the area scores. Scores come from the one spec scorer (the rescoring replay path)."""
import json
import statistics
from collections import Counter
from pathlib import Path

from kami_drills.scoring_v2 import cluster_bootstrap_ci
from kami_drills.spec import from_v1

MAPPING = json.loads(Path(__file__).with_name("skill_areas.json").read_text())
AREAS = list(MAPPING["areas"])
MIN_N = 5  # an area with fewer scored drills is flagged thin
CONTROL = "control or trap (gate-only)"


def family(spec):
    """The miner detector for a mined item (without @version), else the v1 category."""
    det = ((spec.get("provenance") or {}).get("mined_by") or {}).get("detector")
    return det.split("@")[0] if det else spec.get("category")


def area(spec):
    hit = MAPPING["drills"].get(spec["id"]) or MAPPING["families"].get(family(spec))
    if hit is None:
        raise ValueError(f"{spec['id']}: family {family(spec)!r} has no skill area in kami_drills/skill_areas.json")
    return hit["area"]


def excluded(spec):
    """Why a spec gets no score, else None. The gate's split: controls and traps are gate-only, tier B is reported beside
    the forecast and never in it, smoke and retired drills never rank."""
    if spec.get("variants", {}).get("origin") == "trap_control":
        return CONTROL
    if "retired" in (spec.get("status"), spec.get("role")):
        return "retired"
    if spec.get("role") == "smoke":
        return "smoke"
    if spec.get("tier") == "B":
        return "tier B (unscored)"
    return None


def tries(doc):
    """Recorded tries as rows {model, drill, try, steps | score} from plays.json ({model: {drill: [try]}}), a flat list
    of rows (tests/fixtures/v1_rc2_plays.json) or a results/<suite>/models/*.json file (its v1 verdicts, not replayed)."""
    if isinstance(doc, list):
        return doc
    if "per_drill" in doc:
        return [{"model": doc["model"], "drill": d, "try": t, "score": float(v)} for d, vs in doc["per_drill"].items()
                for t, v in enumerate(vs)]
    return [{**p, "model": m, "drill": d, "try": t} for m, ds in doc.items() for d, ps in ds.items() for t, p in enumerate(ps)]


def drill_scores(specs, rows):
    """{model: {drill: mean score over its tries}}. Tries with steps are replayed through the 4.7.0 adapter against the
    spec (from_v1.equivalence, as scripts/v11/rescore.py does); rows that carry a score keep it."""
    by = {}
    for r in rows:
        by.setdefault(r["drill"], []).append(r)
    out = {}
    for s in specs:
        rs = by.get(s["id"], [])
        got = [(r["model"], r["score"]) for r in rs if "steps" not in r]
        got += [(r["model"], r["score"]) for r in from_v1.equivalence(s, [r for r in rs if "steps" in r])]
        per = {}
        for m, v in got:
            per.setdefault(m, []).append(v)
        for m, vs in per.items():
            out.setdefault(m, {})[s["id"]] = sum(vs) / len(vs)
    return out


def _corr(a, b):
    try:
        return statistics.correlation(a, b)
    except statistics.StatisticsError:  # fewer than 2 models, or a constant area
        return None


def profile(specs, rows, n_boot=10000, seed=0, min_n=MIN_N):
    area_of = {s["id"]: area(s) for s in specs if excluded(s) != CONTROL}  # every drill maps, scored or not; controls have no area
    scored = [s for s in specs if not excluded(s)]
    scores = drill_scores(scored, rows)
    models = {}
    for m, ds in sorted(scores.items()):
        cells = {}
        for a in AREAS:
            items = {d: v for d, v in ds.items() if area_of[d] == a}
            lo_hi = cluster_bootstrap_ci(items, {d: d for d in items}, n_boot, seed)[:2] if len(items) > 1 else None  # one drill: no CI
            cells[a] = {"n": len(items), "score": statistics.mean(items.values()) if items else None,
                        "ci95": list(lo_hi) if lo_hi else None, "thin": len(items) < min_n}
        present = [c["score"] for c in cells.values() if c["score"] is not None]
        models[m] = {"areas": cells, "equal_weight": statistics.mean(present), "areas_in_equal_weight": len(present),
                     "drill_weighted": statistics.mean(ds.values()), "drills": len(ds)}
    used = {d for ds in scores.values() for d in ds}
    names = sorted(models)
    col = {a: [models[m]["areas"][a]["score"] for m in names] for a in AREAS}
    full = [a for a in AREAS if None not in col[a]]
    rest = {a: [statistics.mean(col[b][k] for b in full if b != a) for k in range(len(names))] for a in full} if len(full) > 1 else {}
    return {
        "areas": MAPPING["areas"],
        "n_by_area": {a: sum(area_of[s["id"]] == a for s in scored if s["id"] in used) for a in AREAS},
        "min_n": min_n,
        "models": models,
        "area_correlation": {a: {b: _corr(col[a], col[b]) for b in full} for a in full},
        "r_with_rest": {a: _corr(col[a], r) for a, r in rest.items()},
        "excluded": dict(Counter(excluded(s) for s in specs if excluded(s))),
        "not_administered": sorted(s["id"] for s in scored if s["id"] not in used),
        "method": {"area": "mean drill score (a drill's score: mean over its tries)",
                   "ci95": f"percentile bootstrap over the area's drills, {n_boot} resamples, seed {seed}",
                   "equal_weight": "mean of the area scores, every area counts the same",
                   "drill_weighted": "mean over every scored drill (areas with more drills count more)",
                   "unscored": "controls and traps are gate-only; tier B, smoke and retired drills get no score"},
    }
