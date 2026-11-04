"""kami-drills command line: a thin wrapper over Inspect. `inspect view` is the deep-dive for any log."""
import argparse
import json
import os
import runpy
import sys
from pathlib import Path

from kami_drills import STACK, SUITE_VERSION

ROOT = Path(__file__).parent.parent
EXAMPLES = """examples:
  kami-drills list
  kami-drills check
  kami-drills run --model openrouter/anthropic/claude-haiku-5.5 --epochs 2
  kami-drills report logs/
  kami-drills report old_logs/ --override fixed_drill_logs/
  kami-drills profile plays.json --specs specs/v1.1
  kami-drills spec check drills_v2/spec
  kami-drills spec from-miner candidates.jsonl --runs "$KAMI_DRILLS_S1_RUNS" --out out/specs
"""
CATS = {"levelup": "level", "memory": "memory", "onboarding": "onboard", "progression": "progress",
        "repeat_failed": "repeat", "risk": "risk", "stale_state": "stale", "stalling": "stall"}


def bold(text):
    return f"\033[1m{text}\033[0m" if sys.stdout.isatty() and not os.environ.get("NO_COLOR") else text


def table(header, rows, left=1):
    """Aligned columns: the first `left` columns are text (left-aligned), the rest numbers (right-aligned)."""
    widths = [max(len(str(c)) for c in col) for col in zip(header, *rows)]
    fmt = lambda r: "  ".join(str(c).ljust(w) if i < left else str(c).rjust(w)
                              for i, (c, w) in enumerate(zip(r, widths))).rstrip()
    print(bold(fmt(header)))
    for r in rows:
        print(fmt(r))


def fail(msg):
    print(f"kami-drills: {msg}", file=sys.stderr)
    sys.exit(2)


def drills():
    return [json.loads(p.read_text()) for p in sorted((ROOT / "drills").glob("*.json")) if not p.name.startswith("_")]


def cmd_list(args):
    rows = [{k: d.get(k) for k in ("id", "category", "difficulty", "summary")} for d in drills()]
    if args.json:
        return print(json.dumps(rows, indent=1))
    width = os.get_terminal_size().columns if sys.stdout.isatty() else 160
    cut = lambda s, n: s if len(s) <= n else s[: max(n - 1, 0)] + "…"
    lead = max(len(r["id"]) for r in rows) + 24
    table(["drill", "category", "level", "situation"],
          [[r["id"], r["category"], r["difficulty"], cut((r["summary"] or {}).get("situation", ""), width - lead)] for r in rows], left=4)


def cmd_check(args):
    ok = True
    tests = sorted((ROOT / "tests").glob("test_*.py"))
    # v1 checks are plain scripts; v2 tests use pytest (dev dependency, installed by `uv run`)
    py = [t for t in tests if "import pytest" in t.read_text()]
    for test in [*(t for t in tests if t not in py), ROOT / "kami_drills" / "report.py"]:
        try:
            runpy.run_path(str(test), run_name="__main__")
        except AssertionError as e:
            ok = False
            print(f"FAIL {test.name}: {e}")
    if py:
        import subprocess
        if subprocess.run([sys.executable, "-m", "pytest", "-q", *map(str, py)]).returncode != 0:
            ok = False
            print("FAIL pytest (needs the dev group: uv sync --group dev)")
    print("all checks passed" if ok else "checks failed")
    return 0 if ok else 1


def show(summaries, verbose=False, pairs=()):
    pct = lambda x: f"{x:.0%}"
    table(["model", "pass", "95% range", "consistency", "gap rate", "approx. cost", "tries"],
          [[s["model"], pct(s["pass"]), f"{s['ci95'][0]:.0%}–{s['ci95'][1]:.0%}",
            f"{s['pass_all_tries']}/{s['drills']}" if s["tries"] > 1 else "-", pct(s["gap_rate"]),
            f"${s['cost_usd']:.2f}" if s["cost_usd"] is not None else "-", s["tries"]] for s in summaries])
    cats = sorted({c for s in summaries for c in s["category"]})
    n = summaries[0]["category_n"]
    print()
    table(["by category"] + [f"{CATS.get(c, c)} ({n.get(c, 0)})" for c in cats],
          [[s["model"]] + [pct(s["category"].get(c, 0)) for c in cats] for s in summaries])
    if pairs:
        shown = [p for p in pairs if verbose or p["q"] < .05]
        print(f"\npaired comparison: {len(pairs)} pairs, {sum(p['q'] < .05 for p in pairs)} with Holm q < .05")
        for p in shown:
            print(f"  {p['a']} vs {p['b']}  {p['diff']:+.1%}  p={p['p']:.4f}  q={p['q']:.3f}")
    print(f"\nsuite {summaries[0]['suite_version']}  stack " + ", ".join(f"{k} {v}" for k, v in summaries[0]["stack"].items()))
    print("consistency = drills passed on every try; gap rate = attempts that met a call with no recorded response; "
          "cost = approx., OpenRouter list prices (docs/method.md)")


def cmd_run(args):
    from inspect_ai import eval as inspect_eval

    from kami_drills.report import collect, summarise
    from kami_drills.task import kamidrills

    suite = {"v1": kamidrills}
    if args.suite != "v1":  # v2 suites: scored by their own scorers, so a plain per-drill table, not the v1 report
        from kami_drills.cadence import schedule_v2
        from kami_drills.memory import memory_v2
        from kami_drills.episode import budget_v2
        from kami_drills.polluted import polluted_v2
        suite = {"v2-memory": memory_v2, "v2-schedule": schedule_v2, "v2-polluted": polluted_v2, "v2-budget": budget_v2}
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True))  # Inspect reads .env too; load it first so the key check sees it
    if args.model.startswith("openrouter/") and not os.environ.get("OPENROUTER_API_KEY"):
        fail("OPENROUTER_API_KEY is not set; export it, or put it in a .env file in this directory")
    known = {d["id"] for d in drills()}
    ids = [i for i in (args.drills or "").split(",") if i]
    if unknown := sorted(set(ids) - known) if args.suite == "v1" else []:
        fail(f"unknown drill id(s): {', '.join(unknown)} (see kami-drills list)")
    provider = args.model.removeprefix("openrouter/").split("/")[0]
    # their strict tool mode requires every parameter, and the harness surface has optional ones
    model_args = {"strict_tools": False} if provider in ("openai", "meta") else {}
    logs = inspect_eval(suite[args.suite](), model=args.model, model_args=model_args, epochs=args.epochs, sample_id=ids or None,
                        log_dir=args.log_dir, display="full" if args.verbose else "none")
    log = logs[0]
    if log.status != "success" or all(s.error for s in log.samples or []):
        print(f"kami-drills: eval {log.status}, no scored samples; see inspect view --log-dir {args.log_dir}", file=sys.stderr)
        return 1
    if args.suite != "v1":
        rows = [[smp.id, smp.epoch, next(iter(smp.scores.values())).value] for smp in log.samples or [] if smp.scores]
        table(["drill", "try", "score"], rows, left=1)
        print(f"log: {log.location}")
        return 0
    summary = summarise(args.model.removeprefix("openrouter/"), collect([log])[args.model.removeprefix("openrouter/")])
    if args.json:
        print(json.dumps(summary, indent=1, default=str))
    else:
        show([summary], args.verbose)
        print(f"log: {log.location}  (inspect view --log-dir {args.log_dir})")
    return 0


def cmd_report(args):
    from kami_drills.report import load, log_files, paired

    if not log_files(args.logs):
        fail(f"no .eval logs in {' '.join(args.logs)}")
    try:
        summaries = load(args.logs, args.override or ())
    except ValueError as e:
        fail(str(e))
    pairs = paired(summaries)
    if args.out:
        write_results(Path(args.out), summaries, pairs)
    if args.json:
        print(json.dumps({"models": summaries, "paired": pairs}, indent=1, default=str))
    else:
        show(summaries, args.verbose, pairs)
    return 0


def write_results(out, summaries, pairs):
    """results/<suite>/: summary.md, paired.txt, models/<provider>__<model>.json (pass per drill per try)."""
    (out / "models").mkdir(parents=True, exist_ok=True)
    s0 = summaries[0]
    tries = max(s["tries"] for s in summaries)
    md = [f"# kami-drills {s0['suite_version']}: {len(summaries)} models × {s0['drills']} drills × {tries} tries", "",
          "Stack: " + ", ".join(f"{k} {v}" for k, v in s0["stack"].items()) + ".", "",
          "| model | mean pass | 95% CI (bootstrap over drills) | pass on every try | drills where tries disagree | gap rate | approx. cost |",
          "|---|---|---|---|---|---|---|"]
    for s in summaries:
        cost = f"${s['cost_usd']:.2f}" if s["cost_usd"] is not None else "–"
        md.append(f"| {s['model']} | {s['pass']:.0%} | {s['ci95'][0]:.0%}–{s['ci95'][1]:.0%} | {s['pass_all_tries']}/{s['drills']} "
                  f"| {s['tries_disagree']} | {s['gap_rate']:.0%} | {cost} |")
    cats = list(s0["category"])
    md += ["", "| model | " + " | ".join(f"{c} ({s0['category_n'][c]})" for c in cats) + " |", "|---" * (len(cats) + 1) + "|"]
    md += [f"| {s['model']} | " + " | ".join(f"{s['category'][c]:.0%}" for c in cats) + " |" for s in summaries]
    md += ["", f"Paired comparison ({len(pairs)} pairs, sign-flip permutation on per-drill differences, Holm-adjusted): "
           f"{sum(p['q'] < .05 for p in pairs)} pair(s) with q < .05. Full list: [paired.txt](paired.txt).", "",
           "Mean pass: per drill, the mean over tries, then the mean over drills. 95% CI: percentile bootstrap over drills "
           "(10,000 resamples, seed 0). Gap rate: share of attempts that met at least one call with no recorded response. "
           "Approx. cost: every model call priced from its token usage at pinned OpenRouter list prices "
           "([prices-2026-10-16.json](../prices-2026-10-16.json)), or the call's logged OpenRouter cost where that is larger; "
           "see docs/method.md. Per-model files in [models/](models/) give pass "
           "per drill per try.", "",
           "Logs: " + ", ".join(sorted({f"{l['revision']}" for s in summaries for l in s["logs"]})) + " (eval revisions)"
           + ("" if all(s["suite_from_log"] for s in summaries) else
              "; these logs carry no suite metadata, so the suite version and stack above are those of this package."), ""]
    (out / "summary.md").write_text("\n".join(md))
    lines = [f"{pairs[0]['drills']} drills, {len(pairs)} pairs; p = sign-flip permutation, q = Holm-adjusted"]
    lines += [f"{p['a']:40s} vs {p['b']:40s} {p['diff']:+6.1%}  p={p['p']:.4f}  q={p['q']:.3f}{'  *' if p['q'] < .05 else ''}"
              for p in pairs]
    (out / "paired.txt").write_text("\n".join(lines) + "\n")
    for s in summaries:
        s = {**s, "suite_version_source": "log metadata" if s["suite_from_log"] else
             "this package (the log carries no suite metadata)"}
        keep = ("model", "suite_version", "suite_version_source", "stack", "drills", "tries", "pass", "ci95", "pass_all_tries", "tries_disagree",
                "gap_rate", "cost_usd", "category", "per_drill", "per_drill_gap", "logs")
        (out / "models" / f"{s['model'].replace('/', '__')}.json").write_text(
            json.dumps({k: s[k] for k in keep}, indent=1, default=str) + "\n")


def cmd_spec_check(args):
    """Validate v2 specs and run the reference plays, the degenerate battery and the adapter checks (offline, free)."""
    from kami_drills import spec as sp
    from kami_drills.adapters import harness_4_7
    from kami_drills.spec.check import check_bank

    try:
        specs = sp.load(args.paths)
    except (OSError, ValueError) as e:
        fail(str(e))
    from kami_drills.spec.check import expected_failure
    reports = check_bank(specs, [harness_4_7])  # grouped items (item + control, opportunity + trap) are gated together
    for s, r in zip(specs, reports):
        r["expected"] = [f for f in r["failures"] if expected_failure(s, f)]
        r["failures"] = [f for f in r["failures"] if not expected_failure(s, f)]
    if args.json:
        print(json.dumps(reports, indent=1, default=str))
    else:
        table(["drill", "role", "null", "oracle", "mistake", "lazy passes", "kh-4.7.0", "result"],
              [[r["id"], s.get("role") or s.get("variants", {}).get("origin", "-"), _pf(r["plays"].get("null")),
                _pf(r["plays"].get("oracle")), _pf(next((v for k, v in r["plays"].items() if k.startswith("recorded")), None)),
                sum(r["battery"].values()), (r["adapters"] or [{}])[0].get("status", "-"),
                "FAIL" if r["failures"] else "expected" if r["expected"] else "ok"] for s, r in zip(specs, reports)], left=1)
        for r in reports:
            for f in r["failures"]:
                print(f"  {r['id']}: {f}")
        bad = sum(bool(r["failures"]) for r in reports)
        print(f"{len(reports)} specs: {bad} failing, {sum(bool(r['expected']) for r in reports)} with declared (smoke/retired/"
              f"v1-equivalent) lazy passes only")
    return 1 if any(r["failures"] for r in reports) else 0


def _pf(play):
    return "-" if not play else f"{play['score']:g}{'' if play['pass'] is None else ' pass' if play['pass'] else ' fail'}"


def cmd_spec_pseudonymize(args):
    """Keyed pseudonymization of wallet addresses and third-party accounts (opt-in; spec/pseudonymize.py)."""
    from kami_drills.spec import pseudonymize
    from kami_drills.spec.check import check
    key = os.environ.get(args.key_env)
    if not key:
        fail(f"${args.key_env} is not set")
    try:
        report = pseudonymize.run(args.paths, key.encode(), args.out, check)
    except (OSError, ValueError) as e:
        fail(str(e))
    refused = [(i, r) for i, r in report if "refused" in r]
    print(f"{len(report) - len(refused)} spec(s) written to {args.out}: "
          f"{sum(r.get('addresses', 0) for _, r in report)} addresses, {sum(r.get('accounts', 0) for _, r in report)} third-party accounts")
    for i, r in refused:
        print(f"  refused {i}: verdict changed {r['refused']}")
    return 1 if refused else 0


def cmd_spec_from_v1(args):
    """Refresh converted v1 specs in place from the frozen drills/ files (native prefix, recorded reads, stamps)."""
    from kami_drills import spec as sp
    from kami_drills.spec import from_v1

    for path in args.specs:
        overlay = json.loads(Path(path).read_text())
        v1 = json.loads((ROOT / "drills" / f"{overlay['provenance']['source']['v1_id']}.json").read_text())
        out = from_v1.convert(v1, overlay)
        errs = sp.validate(out)
        if errs:
            fail(f"{path}: " + "; ".join(errs))
        Path(path).write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
        print(f"{path}: {out['content_hash']}")
    return 0


def cmd_spec_from_miner(args):
    from kami_drills.spec import from_miner

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for spec, notes in from_miner.convert_file(args.candidates, args.runs):
        if spec is None:
            print(f"skip {notes[0]}")
            continue
        (out / f"{spec['id']}.json").write_text(json.dumps(spec, indent=1, ensure_ascii=False) + "\n")
        n += 1
        if notes:
            print(f"{spec['id']}: " + "; ".join(notes))
    print(f"{n} spec(s) written to {out}")
    return 0


def cmd_mine(args):
    import time
    from kami_drills.miner import bank, split
    from kami_drills.miner.pipeline import mine
    from kami_drills.miner.stats import render
    dirs = []
    for d in map(Path, args.runs):
        if (d / "transcripts").is_dir():
            dirs.append(d)
        elif d.is_dir():
            dirs += sorted(x for x in d.iterdir() if (x / "transcripts").is_dir())
        else:
            fail(f"not a run directory: {d}")
    if not dirs:
        fail("no run directories (a run directory holds transcripts/)")
    key = split.load_key(args.key_file)
    if args.bank and key is None:
        fail(f"--bank needs the split key ({split.KEY_ENV} or {args.key_file or split.KEY_FILE})")
    t = time.time()
    result = mine(dirs, jobs=args.jobs, per_run=args.per_run, max_model_share=args.max_model_share,
                  experimental=[x for x in (args.experimental or "").split(",") if x])
    ex = [i for i in args.examples.split(",") if i]
    written = split.write(result, args.out, key, render=lambda w: render(result, w, ex, time.time() - t))
    if written["leaks"]:
        print(f"LEAK CHECK FAILED, nothing written: {written['leaks'][:5]}")
        return 1
    if args.stats:
        Path(args.stats).write_text(written["stats"])
    pub, private = written["rows"]
    if args.bank:
        man = bank.freeze(written, args.bank, Path(args.bank).name, key)
        print(f"bank {args.bank}: {len(man['public'])} public specs, {man['heldout']['count']} held out; "
              f"root {man['root'][:23]}")
    n_pub = sum(not r.get("redacted") for r in pub)
    print(f"{len(result['candidates'])} items + {len(result['controls'])} controls + {len(result['traps'])} traps from {len(dirs)} runs: "
          + (f"{n_pub} public, {len(private)} held out -> {args.out}" if key else
             f"no split key: unsplit -> {written['paths']['unsplit']}")
          + f" (sha256[:16] {written['sha256_16']}); leak check clean")
    return 0


def cmd_profile(args):
    """Per-skill-area profile (docs/v2-profile.md): models x areas, the equal-weight aggregate, n and bootstrap CIs."""
    from kami_drills import profile as pf
    from kami_drills import spec as sp

    args.min_n = pf.MIN_N if args.min_n is None else args.min_n
    try:
        specs = sp.load(args.specs)
        rows = [r for f in args.results for r in pf.tries(json.loads(Path(f).expanduser().read_text()))]
        out = pf.profile(specs, rows, seed=args.seed, min_n=args.min_n)
    except (OSError, ValueError) as e:
        fail(str(e))
    if not out["models"]:
        fail("no scored drill has a recorded try in " + " ".join(args.results))
    if args.json:
        print(json.dumps(out, indent=1))
        return 0
    n = out["n_by_area"]
    head = [f"{a} (n={n[a]}{', thin' if n[a] < args.min_n else ''})" for a in pf.AREAS]
    ci = lambda c: f"[{c['ci95'][0]:.2f}-{c['ci95'][1]:.2f}]" if c["ci95"] else "[thin]"
    cell = lambda c, a: "-" if c["score"] is None else f"{c['score']:.2f} {ci(c)}" + ("" if c["n"] == n[a] else f" n={c['n']}")
    f2 = lambda v: "-" if v is None else f"{v:.2f}"
    ms = sorted(out["models"].items(), key=lambda kv: (kv[1]["equal_weight"] is None, -(kv[1]["equal_weight"] or 0),
                                                       -kv[1]["equal_weight_all"]))
    table(["model", "equal-weight", "equal-weight all", "drill-weighted", *head],
          [[m, f2(p["equal_weight"]), f2(p["equal_weight_all"]), f2(p["drill_weighted"]),
            *(cell(p["areas"][a], a) for a in pf.AREAS)] for m, p in ms])
    thin = [a for a in pf.AREAS if n[a] < args.min_n]
    print(f"\nequal-weight: mean of the area scores with n >= {args.min_n}, each counting the same"
          f"{' (thin areas left out: ' + ', '.join(thin) + ')' if thin else ''}. equal-weight all: every area. "
          f"drill-weighted: mean over all {sum(n.values())} scored drills. [ ]: bootstrap 95% CI over the area's drills; "
          f"[thin]: n < {args.min_n}, too few drills for a CI, treat as anecdotal.")
    print("not scored: " + (", ".join(f"{k} {v}" for k, v in sorted(out["excluded"].items())) or "none")
          + ". Controls and traps are gate-only; tier B items are reported beside a score, never in it.")
    if out["not_administered"]:
        print(f"scored drills with no recorded try: {len(out['not_administered'])}")
    if out["r_with_rest"]:
        print("area r with the mean of the other areas, across models: "
              + ", ".join(f"{a} {'-' if r is None else f'{r:+.2f}'}" for a, r in out["r_with_rest"].items()))
    if args.bars:
        for m, p in ms:
            print(f"\n{m}")
            for a in [*pf.AREAS, "equal_weight"]:
                v = p[a] if a == "equal_weight" else p["areas"][a]["score"]
                print(f"  {a:12s} {'' if v is None else '#' * round(20 * v):20s} {'-' if v is None else f'{v:.2f}'}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="kami-drills", description="Offline decision drills for Kamigotchi agents (Inspect).",
                                epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"kami-drills {SUITE_VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="command")

    s = sub.add_parser("list", help="list the drills")
    s.add_argument("--json", action="store_true", help="machine-readable output")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("check", help="oracle passes all, null fails all, replay tests (no model calls)")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("run", help="run the drills against a model and print a summary")
    s.add_argument("--model", required=True, help="Inspect model name, e.g. openrouter/<provider>/<model>")
    s.add_argument("--suite", choices=["v1", "v2-memory", "v2-schedule", "v2-polluted", "v2-budget"], default="v1",
                   help="which suite (default v1, the frozen one; v2 suites are experimental)")
    s.add_argument("--epochs", type=int, default=1, help="tries per drill (default 1; results use 2)")
    s.add_argument("--drills", help="comma-separated drill ids (default all)")
    s.add_argument("--log-dir", default="logs", help="where Inspect writes the log (default logs/)")
    s.add_argument("--json", action="store_true", help="machine-readable output")
    s.add_argument("-v", "--verbose", action="store_true", help="show Inspect progress")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("report", help="summary table and paired comparison from logs")
    s.add_argument("logs", nargs="+", help=".eval files or directories of them")
    s.add_argument("--override", nargs="+", metavar="LOG", help="logs whose drills replace those drills in the same "
                   "model's logs, try by try (a re-run of fixed drills)")
    s.add_argument("--out", help="also write summary.md, paired.txt and models/*.json here")
    s.add_argument("--json", action="store_true", help="machine-readable output")
    s.add_argument("-v", "--verbose", action="store_true", help="list every pair, not only q < .05")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("profile", help="per-skill-area profile of recorded tries: areas, equal-weight aggregate, n, CIs")
    s.add_argument("results", nargs="+", help="plays.json ({model: {drill: [tries]}}), a list of try rows, or "
                   "results/<suite>/models/*.json (v1 verdicts as recorded)")
    s.add_argument("--specs", nargs="+", default=[str(ROOT / "specs" / "v1.1")], help="spec files, directories or a bank "
                   "root (default specs/v1.1)")
    s.add_argument("--min-n", type=int, default=None, help="areas with fewer scored drills are thin: no CI, left out of "
                   "the headline aggregate (default: kami_drills.profile.MIN_N, 5)")
    s.add_argument("--seed", type=int, default=0, help="bootstrap seed (default 0)")
    s.add_argument("--bars", action="store_true", help="also print a text bar chart per model")
    s.add_argument("--json", action="store_true", help="machine-readable output")
    s.set_defaults(fn=cmd_profile)

    s = sub.add_parser("spec", help="v2 harness-agnostic drill specs (experimental)")
    ss = s.add_subparsers(dest="spec_cmd", required=True, metavar="action")
    c = ss.add_parser("check", help="validate specs; run oracle/null/mistake plays, the lazy battery and adapter checks (no model)")
    c.add_argument("paths", nargs="+", help="spec files or directories")
    c.add_argument("--json", action="store_true", help="machine-readable output")
    c.set_defaults(fn=cmd_spec_check)
    c = ss.add_parser("from-v1", help="refresh converted v1 specs from drills/ (native prefix, recorded reads, stamps)")
    c.add_argument("specs", nargs="+", help="converted spec files (their provenance.source.v1_id names the v1 drill)")
    c.set_defaults(fn=cmd_spec_from_v1)
    c = ss.add_parser("pseudonymize", help="opt-in: keyed pseudonyms for wallet addresses and third-party accounts (verdicts kept)")
    c.add_argument("paths", nargs="+", help="spec files, directories or a bank root")
    c.add_argument("--key-env", required=True, help="environment variable holding the pseudonymization key (never a file in the repo)")
    c.add_argument("--out", required=True, help="directory for the pseudonymized specs (inputs are not modified)")
    c.set_defaults(fn=cmd_spec_pseudonymize)
    c = ss.add_parser("from-miner", help="convert miner candidates (jsonl) to specs, best effort")
    c.add_argument("candidates", help="candidates*.jsonl from kami-drills mine")
    c.add_argument("--runs", help="Season 1 run directory (snapshots, transcripts); without it state comes from the candidate only")
    c.add_argument("--out", required=True, help="directory for the spec files")
    c.set_defaults(fn=cmd_spec_from_miner)


    s = sub.add_parser("mine", help="v2 miner: candidate drills from run directories (docs/v2-miner.md)")
    s.add_argument("runs", nargs="+", help="run directories (holding transcripts/), or directories of them")
    s.add_argument("--out", required=True, help="candidates.jsonl (public rows; held-out items as opaque stubs); "
                   "writes <stem>.heldout.jsonl and <stem>.rejections.jsonl beside it (private)")
    s.add_argument("--stats", help="also write stats.md here")
    s.add_argument("--examples", default="", help="comma-separated candidate ids to show in stats.md")
    s.add_argument("--jobs", type=int, default=os.cpu_count() or 1, help="parallel runs (output is identical)")
    s.add_argument("--per-run", type=int, default=3, help="max candidates per rule from one source run (default 3)")
    s.add_argument("--max-model-share", type=float, default=0.3,
                   help="max share of a rule's candidates from one source model, at least 2 (default 0.3)")
    s.add_argument("--key-file", help=f"split key file (default: $KAMI_DRILLS_SPLIT_KEY, else .split-key at the repo root); "
                   "never commit it")
    s.add_argument("--experimental", help="comma-separated rule families that are researched, not shipped (node-choice); "
                   "off by default, never for a bank")
    s.add_argument("--bank", help="also freeze a bank here (e.g. bank/v2.0-rc8): public specs + manifest; private/ is gitignored")
    s.set_defaults(fn=cmd_mine)

    args = p.parse_args(argv)
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
