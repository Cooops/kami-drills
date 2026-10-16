# Method

Details behind the [README](../README.md).

## What a drill is

A drill is one JSON file in [`drills/`](../drills/), built from an authoring source in
[`defs/`](../defs/):

- **prefix**: the transcript up to the fork, verbatim. The system prompt and tool
  surface come from the pinned stack (see Provenance).
- **replay**: rows that answer the model's tool calls at and after the fork, matched on
  tool, arguments and replay flags (for example, `harvest_start` reverts until a
  `harvest_stop` has succeeded). Reads the original agent made before the fork are
  answered from the recording; anything never recorded gets an explicit
  "not available in this drill" reply.
- **rule**: the deterministic pass condition over the calls the model makes after the
  fork: required calls, forbidden calls, required order, and required end-state flags.
- **oracle**: a scripted correct play that must pass. A null play (no calls) must fail.

Categories: onboarding, progression, levelup, memory, repeat_failed, risk, stale_state,
stalling. `kami-drills list` shows every drill with its situation.

## Scoring

A try passes when the rule holds over every call the model attempts after the fork,
including calls the tool layer rejected. A try ends when the model stops calling tools,
calls `end_session` or `set_next_wake`, reaches 50 tool calls (kami-agent's session
cap) or 30 turns, or when no further call can change the verdict. Generation uses
kami-agent 0.7.5's defaults: `max_tokens` 4096, temperature and reasoning effort left to
the provider.

Reported per model: mean pass (per drill the mean over tries, then the mean over
drills), a 95% percentile bootstrap interval over drills, consistency (drills passed on
every try), and gap rate (attempts that met at least one unrecorded call). Models are
compared pairwise on the same drills with a sign-flip permutation test on per-drill
differences, Holm-adjusted across all pairs.

## Cost

Costs are approximate, at list prices. OpenRouter's per-call `cost` field is missing on
30–69% of calls in the published logs, so summing it under-counts (rc1 did). Instead every
model call is priced from its token usage (Inspect's `ModelEvent.output.usage`) against a
pinned table, [`results/prices-2026-10-16.json`](../results/prices-2026-10-16.json): OpenRouter's
`/api/v1/models` `pricing` for the 11 models, fetched 2026-10-16.

    input_tokens × prompt + input_tokens_cache_read × input_cache_read
      + input_tokens_cache_write × input_cache_write + output_tokens × completion

`input_tokens` excludes cache reads and writes; `output_tokens` already includes reasoning
tokens (OpenRouter's `completion_tokens` does), so reasoning is not priced twice. Long-prompt
tiers (`overrides` with `min_prompt_tokens`) apply on the call's total prompt tokens. A missing
cache-write price is 1.25 × prompt for an `anthropic/*` model (Anthropic's 5-minute write rate)
and prompt otherwise; no model in the 2026-10-16 table needed the Anthropic fallback. Where a
call also logged a `cost`, the larger of the two counts. `kami_drills.report.price_call` does
this; `tests/test_prices.py` checks it against a logged call.

On calls that logged a cost, token pricing matches it exactly for 9 of 11 models. Two differ:
gemini-3.8-flash was billed 10% under the table's list price (the list price is used), and
deepseek-v4-pro-0813 is served by several upstream providers whose prices differ from the
listed one in both directions; its listed UTC peak-hour windows are not applied (the base
price is). A model missing from the table falls back to logged costs only.

## Provenance

Every drill carries a `provenance` block:

- **credit**: "derived from KamiBench run 004/005/006, CC-BY-4.0" for drills forked from
  the [KamiBench datasets](https://huggingface.co/KamiBench); "KamiArena hosted runs
  (Coopes), CC-BY-4.0" for the 10 drills forked from two hosted GPT-6 Luna runs.
- **source**: dataset, session file, and fork line (the prefix is transcript lines
  1..fork; the model acts in place of the next line).
- **chain_block**: the block at the fork, from the last read in the prefix that states
  its own block (`meta.blockNumber` on lens reads, `block` on receipts). One drill,
  `onboard-bridge-pending`, has none: its prefix holds only an account-index error,
  a gas balance and an account list, so the field is null with that reason.
- **stack**: the pins the drills are presented under (kami-harness 4.7.0 `df7129f`,
  kami-agent 0.7.5 `8e444bb`, kami-lens 1.0.5 `e604290`, gdd `3c6f4f9`), plus the
  harness era the source transcript was recorded on.
- **tools_hash**: sha256 of the tool surface the model is shown (108 tools: the stock
  harness 4.7.0 surface minus the 9 OUTSOURCE tools, plus the 8 kami-agent scaffold
  tools), with the exact method, and the stock harness's published hash for reference.
- **judge_hash**: sha256 of `kami_drills/task.py`, which holds the replay engine and the
  scorer.

`uv run python -m kami_drills.build_specs --stamp` recomputes the provenance block.

## CLI

```
kami-drills list [--json]                     # drills with category, difficulty, situation
kami-drills check                             # exits 1 if any check fails
kami-drills run --model M [--epochs N] [--drills id,id] [--log-dir DIR] [--json] [-v]
kami-drills report LOGDIR... [--override LOG...] [--out DIR] [--json] [-v]
```

`run` is quiet by default and prints one summary when the eval ends; `-v` shows
Inspect's progress display. For `openai/*` and `meta/*` models it passes
`-M strict_tools=false`: their strict tool mode requires every parameter to be present,
and the harness tool surface has optional parameters. `report --override` takes logs of a re-run of some drills (for
example drills fixed in a new suite version): for each model, their results and costs replace
those drills' in the base logs, try k for try k. `results/v1.0.0-rc2/` was written with
`kami-drills report stepC stepD rc2_gem38 --override rc2_fixed --out results/v1.0.0-rc2`
(the KamiArena Season 1 working directory). A log run with `--epochs N` counts as N tries. `report --out DIR` writes the
`summary.md`, `paired.txt` and `models/*.json` found under [`results/`](../results/).
Exit codes: 0 ok, 1 checks or eval failed, 2 usage error. Colour only on a terminal,
and never with `NO_COLOR` set.

```
$ kami-drills report logs/
model                          pass  95% range  consistency  gap rate  approx. cost  tries
google/gemini-4-argon           74%    63%–85%        31/47       71%        $14.01      2
openai/gpt-6.1-sol              69%    56%–82%        31/47       63%        $15.00      2
...

by category                    level (1)  memory (6)  onboard (7)  progress (7)  repeat (10)  risk (5)  stale (10)  stall (1)
google/gemini-4-argon                50%         58%          79%           93%          50%      100%         80%       100%
...

paired comparison: 55 pairs, 1 with Holm q < .05
  google/gemini-4-argon vs deepseek/deepseek-v4-pro-0813  +26.6%  p=0.0006  q=0.033

suite v1.0.0-rc2  stack kami-harness 4.7.0 df7129f, kami-agent 0.7.5 8e444bb, kami-lens 1.0.5 e604290, gdd 3c6f4f9
```

## What's in here

| Path | What it is |
|---|---|
| [`kami_drills/task.py`](../kami_drills/task.py) | The Inspect task: replay engine, agent loop, scorer |
| [`kami_drills/build_specs.py`](../kami_drills/build_specs.py) | Builds `drills/` from `defs/` and source transcripts; stamps provenance |
| [`kami_drills/surface/`](../kami_drills/surface/) | Tool surface and prompts, from kami-agent 0.7.5 and kami-harness 4.7.0 |
| [`kami_drills/cli.py`](../kami_drills/cli.py), [`report.py`](../kami_drills/report.py) | The `kami-drills` command; summaries and paired comparison |
| [`drills/`](../drills/) | Built drills (with provenance) and the shared world-read library |
| [`defs/`](../defs/) | Drill authoring sources |
| [`tests/`](../tests/) | Oracle/null policy checks and replay equivalence checks |
| [`results/`](../results/) | Published results per suite version |
| [`CONTRIBUTING.md`](../CONTRIBUTING.md) | How a run's mistakes become a drill |
