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
        from kami_drills.polluted import polluted_v2
        suite = {"v2-memory": memory_v2, "v2-schedule": schedule_v2, "v2-polluted": polluted_v2}
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
    s.add_argument("--suite", choices=["v1", "v2-memory", "v2-schedule", "v2-polluted"], default="v1",
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

    args = p.parse_args(argv)
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
