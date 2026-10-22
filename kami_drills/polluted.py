"""Scoring helper for polluted-context variants (docs/v2-polluted.md)."""


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
