# kami-drills

Offline decision drills for agents that play [Kamigotchi](https://www.kamigotchi.io/).
Each drill forks a real agent transcript at the point where that agent made a mistake,
hands the history to the model under test, replays the recorded world, and scores what
the model does next with a fixed rule. No chain access, no live game, no model judge.

Suite **v1.0.0-rc1** (release candidate until the KamiArena Season 1 freeze): 47 drills
in 8 categories, built on the KamiBench stack by tokedo.

## What a drill is

A drill is one JSON file in [`drills/`](drills/), built from an authoring source in
[`defs/`](defs/):

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

## Quickstart

```
uv sync
uv run kami-drills check                      # no model calls; oracle 47/47, null 0/47, replay tests
export OPENROUTER_API_KEY=...                 # or put it in .env
uv run kami-drills run --model openrouter/anthropic/claude-haiku-5.5 --epochs 2
```

The task is an ordinary [Inspect](https://inspect.aisi.org.uk/) task, so the Inspect CLI
works too:

```
uv run inspect eval kami_drills/task.py --model openrouter/<provider>/<model> --epochs 2
uv run inspect view --log-dir logs            # per-sample transcripts, tool calls, verdicts
```

## CLI

```
kami-drills list [--json]                     # drills with category, difficulty, situation
kami-drills check                             # exits 1 if any check fails
kami-drills run --model M [--epochs N] [--drills id,id] [--log-dir DIR] [--json] [-v]
kami-drills report LOGDIR... [--out DIR] [--json] [-v]
```

`run` is quiet by default and prints one summary when the eval ends; `-v` shows
Inspect's progress display. For `openai/*` and `meta/*` models it passes
`-M strict_tools=false`: their strict tool mode requires every parameter to be present,
and the harness tool surface has optional parameters. `report --out DIR` writes the
`summary.md`, `paired.txt` and `models/*.json` found under [`results/`](results/).
Exit codes: 0 ok, 1 checks or eval failed, 2 usage error. Colour only on a terminal,
and never with `NO_COLOR` set.

```
$ kami-drills report logs/
model                          pass  95% range  consistency  gap rate    cost  tries
google/gemini-4-argon           71%    60%–83%        30/47       70%  $10.51      2
openai/gpt-6.1-sol              64%    50%–78%        29/47       62%  $13.49      2
...

by category                    level (1)  memory (6)  onboard (7)  progress (7)  repeat (10)  risk (5)  stale (10)  stall (1)
google/gemini-4-argon                50%         58%          79%           93%          50%       80%         75%       100%
...

paired comparison: 45 pairs, 1 with Holm q < .05
  google/gemini-4-argon vs deepseek/deepseek-v4-pro-0813  +24.5%  p=0.0009  q=0.043

suite v1.0.0-rc1  stack kami-harness 4.7.0 df7129f, kami-agent 0.7.5 8e444bb, kami-lens 1.0.5 e604290, gdd 3c6f4f9
```

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

## Results (v1.0.0-rc1)

10 models via OpenRouter, 47 drills, 2 tries each, 2026-10-16. Full table, per-category
scores, all 45 pairs and per-drill verdicts: [`results/v1.0.0-rc1/`](results/v1.0.0-rc1/).

| model | mean pass | 95% CI | pass on every try | gap rate | cost |
|---|---|---|---|---|---|
| google/gemini-4-argon | 71% | 60–83% | 30/47 | 70% | $10.51 |
| openai/gpt-6.1-sol | 64% | 50–78% | 29/47 | 62% | $13.49 |
| anthropic/claude-opus-5.5 | 63% | 50–76% | 26/47 | 63% | $76.95 |
| meta/muse-wonder | 63% | 50–76% | 26/47 | 72% | $31.64 |
| anthropic/claude-haiku-5.5 | 60% | 47–72% | 25/47 | 48% | $6.00 |
| openai/gpt-6-luna | 59% | 46–70% | 22/47 | 81% | $0.80 |
| google/gemini-3.6-flash | 54% | 40–67% | 23/47 | 66% | $3.68 |
| anthropic/claude-sonnet-5.5 | 54% | 41–68% | 23/47 | 54% | $34.47 |
| x-ai/grok-4.7 | 52% | 40–64% | 17/47 | 70% | $15.19 |
| deepseek/deepseek-v4-pro-0813 | 47% | 35–59% | 14/47 | 62% | $5.26 |

With 47 drills the intervals are wide and most neighbours overlap. One pair differs
after Holm correction: gemini-4-argon over deepseek-v4-pro-0813 (+24.5 points,
q = 0.043). The logs were produced in the KamiArena repo at the commit tagged
`drills-v1.0.0-rc1` (try 1) and a later docs-only commit (try 2); the drill files and
judge are those of this release.

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

## Limitations

- **Replay gaps.** A drill replays a recorded history, which contains only what the
  original agent looked at. When a model asks for something never recorded (daemon
  status, a quest the original agent never checked, another player's kami) it gets an
  explicit "not available in this drill" reply; nothing is invented. In the dry run
  about 60% of attempts hit at least one gap, mostly reads, and the v1.0.0-rc1 runs above
  range from 48% to 81% by model. Every model faces the same replay, so comparisons stay
  like-for-like, but models that explore more meet more gaps. The planned fix (v1.1)
  forks drills from runs that log a full state snapshot at every session start.
- **Drill vs live surface.** Drills present the stock harness 4.7.0 tool descriptions
  minus the 9 OUTSOURCE tools, and replay quest-list reads in the stock (raw) format the
  source runs recorded. KamiArena's live Season 1 agents run a patched hosted image
  (compact `lens_quests` view, owner-address reads). Inspect requires a description for
  every tool parameter; where the harness documents parameters in the tool description
  instead, the parameter name is used.
- **Scale.** 47 drills give wide per-model intervals; one try carries about ±14 points
  of noise per model, so results use two.
- **Rules are hand-written.** Each rule was checked against the oracle, the null play
  and scripted alternative plays, and reviewed; a correct play the author did not
  anticipate can still fail.

## What's in here

| Path | What it is |
|---|---|
| [`kami_drills/task.py`](kami_drills/task.py) | The Inspect task: replay engine, agent loop, scorer |
| [`kami_drills/build_specs.py`](kami_drills/build_specs.py) | Builds `drills/` from `defs/` and source transcripts; stamps provenance |
| [`kami_drills/surface/`](kami_drills/surface/) | Tool surface and prompts, from kami-agent 0.7.5 and kami-harness 4.7.0 |
| [`kami_drills/cli.py`](kami_drills/cli.py), [`report.py`](kami_drills/report.py) | The `kami-drills` command; summaries and paired comparison |
| [`drills/`](drills/) | Built drills (with provenance) and the shared world-read library |
| [`defs/`](defs/) | Drill authoring sources |
| [`tests/`](tests/) | Oracle/null policy checks and replay equivalence checks |
| [`results/`](results/) | Published results per suite version |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | How a run's mistakes become a drill |

## Credits and licence

Built on the [KamiBench](https://github.com/tokedo/KamiBench) stack by tokedo:
[kami-harness](https://github.com/tokedo/kami-harness),
[kami-agent](https://github.com/tokedo/kami-agent) and
[kami-lens](https://github.com/tokedo/kami-lens) (MIT). The tool surface and prompts in
`kami_drills/surface/` are copied from them at the pins above.

Code: [MIT](LICENSE). Drill data (transcript prefixes and recorded responses in
`drills/`): CC-BY-4.0, see [DATA_LICENSE.md](DATA_LICENSE.md).

## Disclosure

Maintained by Coopes with tokedo. Coopes works at Asphodel, the studio behind
Kamigotchi, and runs Kamibots, an automation service for the game; its strategy tools
(the OUTSOURCE class) are hidden from every drill. kami-drills and the hosted-run data
are Coopes's personal work, not Asphodel's.
