"""v2 scoring (experimental, pure functions). Nothing here is used by v1 reports; Season 1 is graded on v1.

Definitions follow docs/v2-proposal.md sections 5 and 6; the gap rule is docs/v2-gap-coding.md.
"""
import hashlib
import math
import random
import statistics

from kami_drills.cadence import STATE_CHANGING, action_count

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
    """attempt["calls"]: the calls after the fork in order, each {"tool": name, "gap": bool}; gap is True when the replay
    answered 'not available in this drill' (task.py records these in the score's `misses`). A scorable action is a
    non-gap call to a state-changing tool (cadence.STATE_CHANGING); reads never count, gapped or not.
    not_administered: a gap came before any scorable action. scored: a gap came after one. clean: no gap."""
    seen_action = False
    gap_after_action = False
    for call in attempt["calls"]:
        if call["gap"]:
            if not seen_action:
                return "not_administered"
            gap_after_action = True
        elif call["tool"] in STATE_CHANGING:
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


def is_heldout(drill_id, category, frac=1 / 3):
    """Held-out membership from sha256(category/drill_id) alone, so it is fixed before any selection and does not move
    when other drills are added or retired. Per category the held-out share is frac in expectation, not exactly."""
    h = int(hashlib.sha256(f"{category}/{drill_id}".encode()).hexdigest(), 16)
    return h % 10000 < frac * 10000


def heldout_split(drill_ids, category, frac=1 / 3):
    """Split by is_heldout ({drill_id: category}). Returns (tuning, heldout), both sorted."""
    held = {d for d in drill_ids if is_heldout(d, category[d], frac)}
    return sorted(set(drill_ids) - held), sorted(held)


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


def select_items(stats, size):
    """Registered selection rule (proposal 4.7): drop low_info and reversed items (reversed go to audit), then keep the
    `size` items with the highest internal (rest-score) discrimination from item_stats. Only drill responses are used;
    correlation with live outcomes never enters selection."""
    ok = [i for i, s in stats.items() if not s["low_info"] and not s["reversed"] and s["point_biserial"] is not None]
    return sorted(sorted(ok), key=lambda i: -stats[i]["point_biserial"])[:size]


def _z(values):
    """{key: x} -> {key: (x - mean) / population sd}; all 0 when the values are constant."""
    m, sd = statistics.fmean(values.values()), statistics.pstdev(values.values())
    return {k: (v - m) / sd if sd else 0.0 for k, v in values.items()}


MAX_SCHEDULE_LOSS = math.log(24 * 60)  # |ln| of a 1-day wake against a 1-minute oracle: the harness range


def cadence_score(schedule_losses, action_ratios):
    """Cadence per model = mean of two z-scores across models: z(-median schedule loss) and z(min(action ratio, 1)).
    schedule_losses: {model: [cadence.schedule_loss per schedule drill]}; a missing or invalid wake (inf) counts as
    MAX_SCHEDULE_LOSS. action_ratios: {model: free/budget state-changing action ratio} (cadence.action_ratio over the
    budget drills, pooled); capped at 1 because acting more when free than when forced is not more persistence, and
    None (no action even under the budget) counts as 0."""
    sched = _z({m: -statistics.median(min(x, MAX_SCHEDULE_LOSS) for x in ls) for m, ls in schedule_losses.items()})
    budget = _z({m: min(r or 0.0, 1.0) for m, r in action_ratios.items()})
    return {m: (sched[m] + budget[m]) / 2 for m in sched}


def composite(decision, cadence, form="zsum"):
    """Registered forecast score per model. zsum (registered): z(decision) + z(cadence), equal weight.
    product (exploratory only): decision * cadence, which needs both on a non-negative scale (e.g. pass@1 and the
    capped action ratio), not the z-based cadence_score."""
    if form == "zsum":
        zd, zc = _z(decision), _z(cadence)
        return {m: zd[m] + zc[m] for m in decision}
    if form == "product":
        if min(decision.values()) < 0 or min(cadence.values()) < 0:
            raise ValueError("product form needs non-negative inputs")
        return {m: decision[m] * cadence[m] for m in decision}
    raise ValueError(f"unknown form {form!r}")


def default_tier_sizes(n_models):
    """Registered tier sizes: top and bottom each floor(0.3 * N + 0.5) (halves round up; N = 15 gives 5/5/5), middle the
    rest. Integer arithmetic, so no float rounding."""
    k = (3 * n_models + 5) // 10
    return k, n_models - 2 * k, k


def _resample(items, clusters, rng, strata=None):
    """One bootstrap draw of item ids, stratified: within each stratum ({item: stratum}; one stratum if None), as many
    clusters ({item: cluster}; an item without one is its own) as the stratum holds are drawn with replacement. A
    stratum with a single cluster draws its items instead, since one cluster carries no between-cluster variance."""
    by_stratum = {}
    for i in items:
        by_stratum.setdefault((strata or {}).get(i), {}).setdefault((clusters or {}).get(i, i), []).append(i)
    pick = []
    for groups in by_stratum.values():
        groups = list(groups.values())
        if len(groups) == 1:
            groups = [[i] for i in groups[0]]
        pick += [i for _ in groups for i in rng.choice(groups)]
    return pick


def _mean_over(scores, pick):
    """Mean of {item: score} over the picked ids, skipping items the model was not administered (absent or None); the
    mean over all its administered items when the draw holds none of them."""
    vals = [scores[i] for i in pick if scores.get(i) is not None]
    return statistics.fmean(vals or [v for v in scores.values() if v is not None])


def _boot_means(per_model_item_scores, n, seed, clusters, score_fn=None, strata=None):
    """Yields ({model: score over one shared resample of items}, rng). Items are the union over models; a model's
    missing or None items are not administered and skipped (`_mean_over`). The score is the mean unless
    score_fn(picked_item_ids) -> {model: score} is given (e.g. the composite)."""
    rng = random.Random(seed)
    items = sorted(set().union(*per_model_item_scores.values()))
    for _ in range(n):
        pick = _resample(items, clusters, rng, strata)
        yield (score_fn(pick) if score_fn else
               {m: _mean_over(s, pick) for m, s in per_model_item_scores.items()}), rng


def _tier_shares(models, tier_sizes, draws, n):
    """Ranks the models in each of n draws ({model: score}, rng), higher first and ties broken at random, cuts the
    ranking into tier_sizes and returns {model: {tier: share of draws}}."""
    if sum(tier_sizes) != len(models):
        raise ValueError(f"tier sizes {tier_sizes} do not add up to {len(models)} models")
    labels = [t for t, k in zip(TIERS, tier_sizes) for _ in range(k)]
    counts = {m: dict.fromkeys(TIERS, 0) for m in models}
    for scores, rng in draws:
        for m, t in zip(sorted(models, key=lambda m: (-scores[m], rng.random())), labels):
            counts[m][t] += 1
    return {m: {t: c / n for t, c in counts[m].items()} for m in models}


def tier_probs_bootstrap(per_model_item_scores, tier_sizes=(3, 4, 3), n=10000, seed=0, clusters=None, score_fn=None,
                         strata=None):
    """P(top/middle/bottom) per model: resample items (clusters if given, within strata if given), score models (mean,
    or score_fn over the resampled item ids), rank them each replicate (ties broken at random), cut the ranking into
    tier_sizes. per_model_item_scores: {model: {item: score}}."""
    return _tier_shares(sorted(per_model_item_scores), tier_sizes, _boot_means(per_model_item_scores, n, seed, clusters, score_fn, strata), n)


def pooled_ratio(pairs):
    """Pooled budget ratio of [(free_matched, budget_matched)]: total matched actions (cadence.matched_actions) in the
    free arms over the total in the forced-budget arms, as cadence.pooled_budget_ratio; None (scored 0) when the budget
    arms matched nothing."""
    b = sum(p[1] for p in pairs)
    return sum(p[0] for p in pairs) / b if b else None


def forecast(decision_items, schedule_losses, budget_ratios, tier_sizes, n=10000, seed=0, clusters=None):
    """Registered forecast, proposal 6.1 steps 2-5. Returns {model: {tier: P}}.
    decision_items: {model: {item: pass@1}}; an absent or None item is not administered and excluded.
    schedule_losses: {model: {drill: cadence.schedule_loss}}; absent or inf counts as MAX_SCHEDULE_LOSS.
    budget_ratios: {model: pooled budget ratio or None}, held fixed across replicates; or {model: {drill: (free_actions,
    budget_actions)}}, pooled per replicate over the resampled budget drills (`pooled_ratio`).
    clusters: {item or drill id: snapshot family}. Stratified cluster bootstrap: decision, schedule and budget items are
    resampled separately (`_resample`), and both scores, the composite and the ranking are recomputed each replicate."""
    models = sorted(decision_items)
    per_model = {m: {} for m in models}
    for kind, data in (("decision", decision_items), ("schedule", schedule_losses), ("budget", budget_ratios)):
        for m in models:
            if isinstance(data[m], dict):
                per_model[m].update({(kind, i): v for i, v in data[m].items()})
    ids = set().union(*per_model.values())
    fam = {k: (k[0], clusters.get(k[1], k[1])) for k in ids} if clusters else None

    def score(pick):
        by = {"decision": [], "schedule": [], "budget": []}
        for kind, i in pick:
            by[kind].append(i)
        dec = {m: _mean_over(decision_items[m], by["decision"]) for m in models}
        sched = {m: [schedule_losses[m].get(i, math.inf) for i in by["schedule"]] for m in models}
        budget = {m: pooled_ratio([r[i] for i in by["budget"] if i in r]) if isinstance(r := budget_ratios[m], dict)
                  else r for m in models}
        return composite(dec, cadence_score(sched, budget))

    return tier_probs_bootstrap(per_model, tier_sizes, n, seed, fam, score, {k: k[0] for k in ids})


def season_baseline_probs(per_model_run_outcomes, tier_sizes, n=10000, seed=0):
    """Calibrated Season 1 baseline: P(tier) per model from resampling each model's own live runs with replacement,
    ranking the resampled means (ties at random) and cutting at tier_sizes. per_model_run_outcomes: {model: [outcome
    per run]}. Calibrated to run-to-run noise only, not to change between seasons."""
    models = sorted(per_model_run_outcomes)
    rng = random.Random(seed)
    draws = (({m: statistics.fmean(rng.choices(r, k=len(r))) for m, r in sorted(per_model_run_outcomes.items())}, rng)
             for _ in range(n))
    return _tier_shares(models, tier_sizes, draws, n)


def live_tiers(per_model_run_outcomes, tier_sizes):
    """Outcome tier per model: rank by mean outcome over its runs, ties broken by model id, cut at tier_sizes."""
    means = {m: statistics.fmean(r) for m, r in per_model_run_outcomes.items()}
    if sum(tier_sizes) != len(means):
        raise ValueError(f"tier sizes {tier_sizes} do not add up to {len(means)} models")
    labels = [t for t, k in zip(TIERS, tier_sizes) for _ in range(k)]
    return dict(zip(sorted(means, key=lambda m: (-means[m], m)), labels))


def certain_probs(tiers):
    """{model: tier} -> a forecast putting probability 1 on that tier."""
    return {m: {t: float(t == tier) for t in TIERS} for m, tier in tiers.items()}


def rps_skill(forecast_probs, outcome_tiers, baselines):
    """Mean RPS over the models in outcome_tiers, and skill 1 - RPS / RPS_baseline against each named baseline
    ({name: {model: probs}}) plus "uniform". A model a baseline does not cover (new this season) gets uniform in it.
    Returns {"rps": forecast mean RPS, "skill": {name: skill or None}}."""
    uniform = {t: 1 / len(TIERS) for t in TIERS}

    def mean_rps(probs):
        return statistics.fmean(rps(probs.get(m, uniform), t) for m, t in outcome_tiers.items())

    f = mean_rps(forecast_probs)
    return {"rps": f, "skill": {name: rps_vs_baseline(f, mean_rps(p)) for name, p in {**baselines, "uniform": {}}.items()}}


def mcb_set(per_model_item_scores, n=10000, seed=0, clusters=None, alpha=.05):
    """Multiple comparisons with the best by paired item bootstrap: model m stays in the set unless its mean is below
    the best of the others in at least 1 - alpha of replicates. Returns the sorted set.
    shortcut: per-model percentile, not Hsu's simultaneous critical value; upgrade if the set is used for a claim
    beyond 'plausible winners'."""
    models = sorted(per_model_item_scores)
    behind = dict.fromkeys(models, 0)
    for means, _ in _boot_means(per_model_item_scores, n, seed, clusters):
        for m in models:
            behind[m] += means[m] < max(means[o] for o in models if o != m)
    return [m for m in models if behind[m] / n < 1 - alpha]


def rho_cluster_ci(drill_score, live_runs, n=10000, seed=0):
    """Spearman rho between drill score and mean live outcome per model, with a 95% CI that resamples models (the
    cluster: runs of one model share one drill score). drill_score: {model: x}; live_runs: {model: [outcome per run]}.
    Returns (rho, lo, hi); degenerate replicates (constant ranks) are skipped."""
    models = sorted(drill_score)
    live = {m: statistics.fmean(live_runs[m]) for m in models}
    rho = statistics.correlation([drill_score[m] for m in models], [live[m] for m in models], method="ranked")
    rng = random.Random(seed)
    reps = []
    for _ in range(n):
        pick = [rng.choice(models) for _ in models]
        try:
            reps.append(statistics.correlation([drill_score[m] for m in pick], [live[m] for m in pick], method="ranked"))
        except statistics.StatisticsError:
            pass
    reps.sort()
    return rho, reps[int(.025 * len(reps))], reps[int(.975 * len(reps)) - 1]


def choose_k(tries, ratio=.1, k_max=4):
    """Smallest K in 2..k_max with E[within-item variance]/K <= ratio * between-item variance (Miller's rule).
    tries: {item: [bool per try]} from a pilot with >= 2 tries per item. k_max when the rule is never met."""
    within = statistics.fmean(statistics.variance(map(int, t)) for t in tries.values())
    between = statistics.variance(statistics.fmean(map(int, t)) for t in tries.values())
    return next((k for k in range(2, k_max + 1) if within / k <= ratio * between), k_max)


def cost_per_action(cost_usd, tool_names):
    """Reported beside the score: dollars per state-changing call (cadence.STATE_CHANGING); None when there were none."""
    acts = action_count(tool_names)
    return cost_usd / acts if acts else None
