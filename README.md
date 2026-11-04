# kami-drills

[![version](https://img.shields.io/badge/version-1.0.0-5b3fb8)](CHANGELOG.md)
[![drills](https://img.shields.io/badge/drills-47-5b3fb8)](drills/)
[![python](https://img.shields.io/badge/python-3.12-3776ab)](pyproject.toml)
[![inspect](https://img.shields.io/badge/built%20on-Inspect-1f6feb)](https://inspect.aisi.org.uk/)
[![code: MIT](https://img.shields.io/badge/code-MIT-2ea44f)](LICENSE)
[![data: CC BY 4.0](https://img.shields.io/badge/data-CC%20BY%204.0-2ea44f)](DATA_LICENSE.md)
[![live: Palaestra Season 1](https://img.shields.io/badge/live-Palaestra%20Season%201-b5462a)](https://palaestra.run/research/season-1)

**Offline decision drills for agents that play [Kamigotchi](https://www.kamigotchi.io/).**
Each drill drops a model into a real moment from a past agent run, just before that agent went wrong, replays
the recorded game, and scores the next move with a fixed rule. No chain, no live game, no model judge.

- **Drills:** 47 across 8 skill categories: stale state, repeated failure, progression, onboarding, memory, risk,
  level-up and stalling.
- **Stack:** [kami-harness 4.7.0](https://github.com/tokedo/kami-harness/tree/df7129f) ·
  [kami-agent 0.7.5](https://github.com/tokedo/kami-agent/tree/8e444bb) ·
  [kami-lens 1.0.5](https://github.com/tokedo/kami-lens/tree/e604290)
- **Runs on:** [Inspect](https://inspect.aisi.org.uk/) via [OpenRouter](https://openrouter.ai/); `kami-drills check` is free.
- **Live:** the drill ranking is the registered forecast for [Palaestra Season 1](https://palaestra.run/research/season-1); follow the runs at [palaestra.run](https://palaestra.run).

### Why

Agents that play a persistent world for days have to learn from experience: notice a cooldown, stop retrying a
failed trade, correct a wrong belief in their own notes. Measuring that live takes a week and real money per run.
Kamigotchi never resets and every agent action is public and replayable, so each real mistake can be turned into an
exact, repeatable test. A full pass over all 47 drills finishes in minutes and costs about $1–135 per model,
depending on the model's price.

The question underneath is general: **can cheap offline drills, cut from an agent's own live failures, forecast how it
does over a long live run?** Kamigotchi is the testbed because its chain hands over the log, the state and the rules
for free (below). The method, the spec and the miner carry to any world that logs enough state:
[docs/adapting.md](docs/adapting.md).

The drills sit inside a loop: live runs produce mistakes, mistakes become drills, and a model's drill score is used
to forecast how it will do in the next live run. Whether that forecast holds is tested in [Palaestra Season 1](https://palaestra.run/research/season-1), live now.

### Why drills, and why on-chain

Real-world training turns logged failures into repeatable drills. Waymo replays logged drives and simulates reactive
variants of them; aviation's evidence-based training tags each drill by competency; Lichess builds puzzles from real
games, keeping only positions with a clear best move (a value swing and an only-move margin); military teams run
after-action reviews. The expensive parts are the same four everywhere: a complete log, the exact state at the
decision, what everyone else did, and a faithful simulator. On a chain each comes with the game:

1. **The log.** The chain is a complete, public, append-only record of every player's actions, human and agent.
2. **The state.** The world state is readable at any block. A drill's fork is built from what the agent read at
   that block, and every value in it can be checked against the chain at that block.
3. **Everyone else.** Other players' actions are on the same log, which is what a reactive world model (for
   example, of liquidations) can be fitted on. The drills that depend on other players are not scored until such a
   model beats a constant rate out of sample and passes review.
4. **The rules.** The game contracts are the rules. The Python simulator is the fast approximation; replaying a
   decision against the real contracts on a fork at the decision block is the future verification path.

It is also public and reproducible, so anyone can rebuild and audit a drill, and the stakes are real. The limits:
agent transcripts (stated intent, what was read) come from the harness logs, not the chain, and other players'
future behaviour still has to be modelled. How a drill is built: [docs/method.md](docs/method.md).

## Quickstart

```bash
git clone https://github.com/Cooops/kami-drills && cd kami-drills
uv sync
uv run kami-drills check        # free: every correct play passes, every empty play fails
```

Score a model (needs an [OpenRouter](https://openrouter.ai/) key):

```bash
export OPENROUTER_API_KEY=...
uv run kami-drills run --model openrouter/anthropic/claude-haiku-5.5 --epochs 2
uv run kami-drills report logs/
```

```text
$ uv run kami-drills report logs/
model                          pass  95% range  consistency  gap rate  approx. cost  tries
anthropic/claude-haiku-5.5      62%    49%–74%        26/47       48%         $8.91      2

by category                    level (1)  memory (6)  onboard (7)  progress (7)  repeat (10)  risk (5)  stale (10)  stall (1)
anthropic/claude-haiku-5.5            0%         33%          86%           21%          70%      100%         65%       100%
```

It is a standard [Inspect](https://inspect.aisi.org.uk/) task: `uv run inspect view` opens every transcript,
tool call and verdict.

## Results

11 models, 2 tries each, 2026-10-16. Full results: [`results/v1.0.0-rc2/`](results/v1.0.0-rc2/).
These were run on v1.0.0-rc2, whose drills are identical to v1.0.0 (only the stamped
`suite_version` differs), so they are the results of record for v1.0.0.

| model | pass | 95% range | passed every try | approx. cost |
|---|---|---|---|---|
| google/gemini-4-argon | 74% | 63–85% | 31/47 | $14.01 |
| openai/gpt-6.1-sol | 69% | 56–82% | 31/47 | $15.00 |
| anthropic/claude-opus-5.5 | 67% | 55–79% | 27/47 | $135.48 |
| google/gemini-3.8-flash (control) | 65% | 52–78% | 29/47 | $9.83 |
| meta/muse-wonder | 64% | 51–76% | 26/47 | $56.32 |
| anthropic/claude-haiku-5.5 | 62% | 49–74% | 26/47 | $8.91 |
| openai/gpt-6-luna | 61% | 49–72% | 23/47 | $0.98 |
| anthropic/claude-sonnet-5.5 | 59% | 46–71% | 25/47 | $50.30 |
| google/gemini-3.6-flash | 54% | 40–67% | 23/47 | $7.04 |
| x-ai/grok-4.7 | 53% | 41–65% | 17/47 | $25.03 |
| deepseek/deepseek-v4-pro-0813 | 48% | 36–60% | 15/47 | $9.19 |

Most neighbours overlap. One pair holds after correcting for 55 comparisons:
Gemini 4 Argon over DeepSeek V4 Pro. The control, 3.8 Flash, beat 3.6 Flash by +10.6 points
(11 drills better, 5 worse; p≈0.08 one-sided): suggestive, not conclusive. Cost is approx., at
OpenRouter list prices, for the whole suite and both tries ([method](docs/method.md#cost)).

## Limitations

- **Replay gaps.** A recording only holds what the original agent looked at; anything else
  gets an explicit "not available in this drill". 48–81% of attempts hit at least one,
  mostly reads. Every model faces the same replay. The v2 bank forks from session-start
  snapshots of runs that log full state; v1.1 (KamiBench mid-session forks) serves missing reads from the
  simulated state (below).
- **Scale.** 47 drills give wide intervals; report tiers, not ranks.
- **Hand-written rules.** Each is checked against a correct play, an empty play and
  scripted alternatives, then reviewed; an unanticipated correct play can still fail.

How drills, scoring and provenance work: [docs/method.md](docs/method.md). Adding drills:
[CONTRIBUTING.md](CONTRIBUTING.md). What comes next ([docs/roadmap.md](docs/roadmap.md)):
gap-free drills forked from runs that log full state, and drills for memory (repairing and relying on notes) and
stamina (decisions late in long sessions and across sessions).

## v2 (experimental, for Season 2)

v1 is the frozen Season 1 forecast suite: `drills/`, the judge hash and the default `kami-drills run --suite v1` are
unchanged by everything below. v2 is a draft for Season 2 ([docs/v2-proposal.md](docs/v2-proposal.md); literature in
[docs/v2-research-digest.md](docs/v2-research-digest.md)). **No real model has been run on any v2 drill.**

| piece | what it is | docs |
|---|---|---|
| Drill spec | Harness-agnostic drill format and game-clock simulator; `kami-drills spec check DIR...` runs oracle, null, source-mistake and a lazy/degenerate battery. Adapter for kami-harness 4.7.0. | [v2-spec](docs/v2-spec.md) |
| Miner | `kami-drills mine RUN_DIR...` turns live runs into candidate drills from session-start snapshots: value swing, accepted actions, provenance, held-out split. | [v2-miner](docs/v2-miner.md) |
| v1.1 | The 47 v1 drills as specs with the audit fixes: 27 ranking, 10 smoke, 10 retired. Agrees with v1 on 1027 of 1034 recorded tries; the 7 are v1 errors (reproducing it needs the private rc2 plays, `KAMI_DRILLS_PLAYS`). | [v1.1](docs/v1.1.md) |
| Bank | `bank/v2.0-rc9/` (mined from the final 7-day Season 1 logs): 50 mined items, 6 controls, 24 traps; 59 public, 21 held out as perturbed variants (hash only). | [v2-changelog](docs/v2-changelog.md) |
| Profile | `kami-drills profile RESULTS...`: per skill area (survival, economic, progression, cadence, recovery) with bootstrap intervals over drills, beside an equal-weight headline, as HELM reports scenarios beside its aggregate. | [v2-profile](docs/v2-profile.md) |
| Scoring | pass^k, bootstrap over snapshot families, pre-registered gap coding, ranked probability score over tiers. | [v2-gap-coding](docs/v2-gap-coding.md) |
| Cadence, memory, polluted | Prototype suites: wake timing and budget use, stale and repaired notes, robustness to earlier failures. `kami-drills run --suite v2-schedule\|v2-memory\|v2-polluted\|v2-budget`. | [cadence](docs/v2-cadence.md) · [memory](docs/v2-memory.md) · [polluted](docs/v2-polluted.md) |

`uv run kami-drills check` runs the v1 script checks and the v2 tests (mock model only: no network, key or cost).

## Licence and credits

Code [MIT](LICENSE); copied upstream material is listed in [third_party/](third_party/NOTICE.md). Drill data CC-BY-4.0 ([DATA_LICENSE.md](DATA_LICENSE.md)): 37 drills
derived from KamiBench runs 004–006, 10 from Palaestra hosted runs (Coopes). Tool surface and
prompts from [kami-harness](https://github.com/tokedo/kami-harness) and
[kami-agent](https://github.com/tokedo/kami-agent) (MIT).

Example hosted-run data: [runs/](runs/README.md).

Maintained by Coopes, who works at Asphodel, the studio behind Kamigotchi, and
runs Kamibots; its tools are hidden from every drill. This project is Coopes's personal work.

## Citation

Also in [CITATION.cff](CITATION.cff) (GitHub's "Cite this repository"). Code is MIT; drill data is CC BY 4.0.

```bibtex
@software{kami_drills_2026,
  title  = {kami-drills: offline decision drills for Kamigotchi agents},
  author = {Coopes},
  year   = {2026},
  version = {1.0.0},
  url    = {https://github.com/Cooops/kami-drills}
}
```
