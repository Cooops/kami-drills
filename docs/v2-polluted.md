# Polluted-context variants (v2 proposal 4.4)

Sinha et al. ([arXiv 2509.09677](https://arxiv.org/abs/2509.09677)) show that a model's own earlier errors in its
context lower later accuracy. Live agents run on contexts full of their own reverts, so this measures the decay.

Two families, kept apart:

- **Static robustness** (`drills_v2/polluted/`, 12 files): robustness to earlier failures **from the same source run**.
  The errors are real v1 data but were made by whichever agent produced the source run, not by the model under test.
  This is not self-conditioning, and its results must not be reported as such.
- **Own-error** (`kami_drills.polluted.own_error_variants`): the self-conditioning design. See below.

Prototype: the static files are built from KamiBench-era v1 data; the real bank comes from Season 1 after close.

## What a variant is

The same decision as a v1 drill, with one or two real failed tool calls (call plus error reply) from the same source run inserted after
"Session start.". Rule, replay and oracle are untouched. The clean control is the v1 drill, unchanged.

- **Error source.** Copied verbatim from a sibling v1 drill's public prefix in the same source run (same dataset
  and agent directory, or same hosted run). Nothing is invented. Calls whose id already exists in the target prefix,
  replies over 1500 characters, failures of hidden OUTSOURCE tools (e.g. `get_strategy_status`) and infrastructure
  errors (RPC pool full, timeouts, rate limits) are skipped. Many files share the same one or two errors
  (`lens_account` not in mirror, a missing `rooms.csv`); that is a known weakness of this family.
- **Selection.** 12 drills, weighted to risk, memory and progression (the categories that separate models in
  `results/v1.0.0-rc2/`); drills at 0% or 100% mean pass are left out.
- **Files.** `drills_v2/polluted/<id>--polluted.json`. The `polluted` block holds `source_drill`, `injected` (tool,
  args, error text, origin drill, session file, prefix index) and `variant_hash`, plus `family` (`static_robustness`) and a `label` saying what it measures. `keep_lines` is shifted to match.
- **Isolation.** The v1 loader reads `drills/*.json` only, so variants are never run by `kami-drills run`.

## Scoring

A variant passes by the v1 rule for its base drill. Per model,
`kami_drills.polluted.polluted_delta(clean_pass, polluted_pass)` takes `drill id -> per-try flags` for each arm
and returns polluted minus clean pass (mean over tries, then over drills, shared drills only). Negative means the
pollution hurt.

## Own-error variants (self-conditioning)

1. Run the model on the clean v1 drills (`kami-drills run`), keeping the Inspect eval log.
2. `own_error_variants(log)` reads that log and builds one variant for **every** drill in it. Each variant inserts,
   after "Session start.", the model's own failed calls (call plus error reply; hidden-tool and infrastructure
   failures dropped, duplicates removed, at most two) taken from **other** drills, never the scored one, so the model
   does not see "I tried X and it failed" for the item being scored. Donor drills are taken in id order starting after
   the scored drill (wrapping); a donor call matching something the scored drill's rule forbids is skipped. Nothing
   depends on whether the model passed or failed the scored drill, so there is no selection on the outcome (building
   variants only for failed drills would inflate the delta by regression to the mean). A drill gets no variant only
   if no other drill has an eligible failure.
3. Run them as a second pass for the same model: `polluted_v2(specs=variants.values())`, then `polluted_delta`,
   keying the polluted results by `variant["polluted"]["source_drill"]`. Compare against the clean results of the
   same drills, including the ones the model passed.

Limit: a donor failure comes from a different decision, so it tests "failures earlier in my context", not "repeating
my own mistake here". It is still the model's own behaviour, unlike the static family.

It reads a log and makes no model call; `tests/test_polluted.py` checks on a synthetic log that no injected error comes from the scored drill and that the variants do not depend on pass/fail. Not yet run on a real
model's log.

## Regenerate

`uv run python scripts/make_polluted_variants.py` is deterministic. `tests/test_polluted.py` checks reruns are
identical, every injected error exists verbatim in v1 data and is eligible (no hidden tool, no infra error), and the v1 suite hash is unchanged.

Not yet done: no model has run on the variants. Run the static ones with `kami-drills run --suite v2-polluted`.
