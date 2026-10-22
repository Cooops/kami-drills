"""v2 scoring (experimental, pure functions). Nothing here is used by v1 reports; Season 1 is graded on v1.

Definitions follow docs/v2-proposal.md sections 5 and 6; the gap rule is docs/v2-gap-coding.md.
"""
import random
import statistics

TIERS = ("top", "middle", "bottom")


def pass_hat_k(tries):
    """pass^k: all k tries pass. A list of bools gives a bool; a {item: tries} dict gives the share of items."""
    if isinstance(tries, dict):
        return sum(pass_hat_k(t) for t in tries.values()) / len(tries)
    return all(tries)


def pass_at_k(tries):
    """pass@k: at least one of k tries passes. Same input shapes as pass_hat_k."""
    if isinstance(tries, dict):
        return sum(pass_at_k(t) for t in tries.values()) / len(tries)
    return any(tries)


def cluster_bootstrap_ci(per_item_scores, clusters, n=10000, seed=0):
    """95% CI of the mean item score, resampling clusters (snapshot families) with replacement.
    Both arguments are dicts keyed by item id. Returns (lo, hi, cluster_count)."""
    groups = {}
    for item, score in per_item_scores.items():
        groups.setdefault(clusters[item], []).append(score)
    groups = list(groups.values())
    rng = random.Random(seed)
    means = []
    for _ in range(n):
        pick = [rng.choice(groups) for _ in groups]
        means.append(sum(sum(g) for g in pick) / sum(len(g) for g in pick))
    means.sort()
    return means[int(.025 * n)], means[int(.975 * n) - 1], len(groups)


def gap_code(attempt):
    """attempt["calls"]: the calls after the fork in order, each {"gap": bool}; gap is True when the replay answered
    'not available in this drill' (task.py records these in the score's `misses`).
    not_administered: a gap came before any scorable (non-gap) call. scored: a gap came after one. clean: no gap."""
    seen_action = False
    gap_after_action = False
    for call in attempt["calls"]:
        if call["gap"]:
            if not seen_action:
                return "not_administered"
            gap_after_action = True
        else:
            seen_action = True
    return "scored" if gap_after_action else "clean"


def gap_sensitivity(scores):
    """scores: [(gap_code, score)]. Returns (mean excluding not_administered, mean counting it as 0); None if empty."""
    kept = [s for c, s in scores if c != "not_administered"]
    zeroed = [0 if c == "not_administered" else s for c, s in scores]
    return (sum(kept) / len(kept) if kept else None), (sum(zeroed) / len(zeroed) if zeroed else None)


def rps(forecast_probs_by_tier, outcome_tier):
    """Ranked probability score over ordered tiers (top, middle, bottom); 0 is perfect. forecast_probs_by_tier is a dict."""
    p = [forecast_probs_by_tier[t] for t in TIERS]
    if abs(sum(p) - 1) > 1e-9:
        raise ValueError("forecast probabilities must sum to 1")
    cum_f = cum_o = total = 0.0
    for t, pk in list(zip(TIERS, p))[:-1]:
        cum_f += pk
        cum_o += t == outcome_tier
        total += (cum_f - cum_o) ** 2
    return total / (len(TIERS) - 1)


def uniform_rps(outcome_tier):
    return rps({t: 1 / len(TIERS) for t in TIERS}, outcome_tier)


def rps_vs_baseline(forecast_rps, baseline_rps):
    """Skill score 1 - rps/baseline: above 0 beats the baseline, 1 is perfect; None when the baseline is perfect."""
    return None if baseline_rps == 0 else 1 - forecast_rps / baseline_rps


def heldout_split(drill_ids, category, frac=1 / 3, seed=0):
    """Deterministic split, stratified by category ({drill_id: category}). Returns (tuning, heldout), both sorted.
    Each category holds out round(frac * size) drills."""
    by_cat = {}
    for d in sorted(drill_ids):
        by_cat.setdefault(category[d], []).append(d)
    held = []
    for cat in sorted(by_cat):
        ids = by_cat[cat]
        random.Random(f"{seed}/{cat}").shuffle(ids)
        held += ids[:round(frac * len(ids))]
    return sorted(set(drill_ids) - set(held)), sorted(held)


def item_stats(response_matrix):
    """response_matrix: {item: [0/1 per respondent]}, respondents in the same order for every item.
    Per item: pass rate, corrected point-biserial (correlation with the rest score, item removed; None when
    undefined), low_info (pass >= .95 or <= .05), reversed (correlation < 0)."""
    items = list(response_matrix)
    totals = [sum(response_matrix[i][r] for i in items) for r in range(len(response_matrix[items[0]]))]
    out = {}
    for i in items:
        x = response_matrix[i]
        rest = [t - v for t, v in zip(totals, x)]
        try:
            r = statistics.correlation(x, rest)
        except statistics.StatisticsError:  # constant item or constant rest score
            r = None
        rate = sum(x) / len(x)
        out[i] = {"pass_rate": rate, "point_biserial": r, "low_info": rate >= .95 or rate <= .05,
                  "reversed": r is not None and r < 0}
    return out
