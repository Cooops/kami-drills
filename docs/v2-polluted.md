# Polluted-context variants (v2 proposal 4.4)

Sinha et al. ([arXiv 2509.09677](https://arxiv.org/abs/2509.09677)) show that a model's own earlier errors in its
context lower later accuracy. Live agents run on contexts full of their own reverts, so this measures the decay.

## What a variant is

The same decision as a v1 drill, with one or two real failed tool calls (call plus error reply) inserted after
"Session start.". Rule, replay and oracle are untouched. The clean control is the v1 drill, unchanged.

- **Error source.** Copied verbatim from a sibling v1 drill's public prefix in the same source run (same dataset
  and agent directory, or same hosted run). Nothing is invented. Calls whose id already exists in the target prefix,
  and replies over 1500 characters, are skipped.
- **Selection.** 12 drills, weighted to risk, memory and progression (the categories that separate models in
  `results/v1.0.0-rc2/`); drills at 0% or 100% mean pass are left out.
- **Files.** `drills_v2/polluted/<id>--polluted.json`. The `polluted` block holds `source_drill`, `injected` (tool,
  args, error text, origin drill, session file, prefix index) and `variant_hash`. `keep_lines` is shifted to match.
- **Isolation.** The v1 loader reads `drills/*.json` only, so variants are never run by `kami-drills run`.

## Scoring

A variant passes by the v1 rule for its base drill. Per model,
`kami_drills.polluted.polluted_delta(clean_pass, polluted_pass)` takes `drill id -> per-try flags` for each arm
and returns polluted minus clean pass (mean over tries, then over drills, shared drills only). Negative means the
pollution hurt.

## Regenerate

`uv run python scripts/make_polluted_variants.py` is deterministic. `tests/test_polluted.py` checks reruns are
identical, every injected error exists verbatim in v1 data, and the v1 suite hash is unchanged.

Not yet done: no model has run on the variants; running them needs a loader flag for `drills_v2/polluted/`.
