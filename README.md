# kami-drills

Offline decision drills for agents that play [Kamigotchi](https://www.kamigotchi.io/).
Each drill drops a model into a real moment from a past agent run, just before that agent
went wrong, replays the recorded game, and scores the next move with a fixed rule.
No chain, no live game, no model judge.

**v1.0.0** · 47 drills · stack kami-harness 4.7.0, kami-agent 0.7.5, kami-lens 1.0.5 ·
built on the [KamiBench](https://github.com/tokedo/KamiBench) stack by tokedo.

## Quickstart

```
uv sync
uv run kami-drills check                      # free: correct plays pass, empty plays fail
OPENROUTER_API_KEY=... uv run kami-drills run --model openrouter/anthropic/claude-haiku-5.5 --epochs 2
uv run kami-drills report logs/               # table, by-category scores, paired comparison
```

It is a standard [Inspect](https://inspect.aisi.org.uk/) task; `inspect view` opens the transcripts.

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
  mostly reads. Every model faces the same replay. v1.1 forks drills from runs that log full
  state at each session start.
- **Scale.** 47 drills give wide intervals; report tiers, not ranks.
- **Hand-written rules.** Each is checked against a correct play, an empty play and
  scripted alternatives, then reviewed; an unanticipated correct play can still fail.

How drills, scoring and provenance work: [docs/method.md](docs/method.md). Adding drills:
[CONTRIBUTING.md](CONTRIBUTING.md).

## Licence and credits

Code [MIT](LICENSE); copied upstream material is listed in [third_party/](third_party/NOTICE.md). Drill data CC-BY-4.0 ([DATA_LICENSE.md](DATA_LICENSE.md)): 37 drills
derived from KamiBench runs 004–006, 10 from KamiArena hosted runs (Coopes). Tool surface and
prompts from [kami-harness](https://github.com/tokedo/kami-harness) and
[kami-agent](https://github.com/tokedo/kami-agent) (MIT).

Example hosted-run data: [runs/](runs/README.md).

Maintained by Coopes with tokedo. Coopes works at Asphodel, the studio behind Kamigotchi, and
runs Kamibots; its tools are hidden from every drill. This project is Coopes's personal work.
