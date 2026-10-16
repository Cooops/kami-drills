# kami-drills

Offline decision drills for agents that play [Kamigotchi](https://www.kamigotchi.io/).
Each drill drops a model into a real moment from a past agent run, just before that agent
went wrong, replays the recorded game, and scores the next move with a fixed rule.
No chain, no live game, no model judge.

**v1.0.0-rc1** · 47 drills · stack kami-harness 4.7.0, kami-agent 0.7.5, kami-lens 1.0.5 ·
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

10 models, 2 tries each, 2026-10-16. Full results: [`results/v1.0.0-rc1/`](results/v1.0.0-rc1/).

| model | pass | 95% range | passed every try | cost |
|---|---|---|---|---|
| google/gemini-4-argon | 71% | 60–83% | 30/47 | $10.51 |
| openai/gpt-6.1-sol | 64% | 50–78% | 29/47 | $13.49 |
| anthropic/claude-opus-5.5 | 63% | 50–76% | 26/47 | $76.95 |
| meta/muse-wonder | 63% | 50–76% | 26/47 | $31.64 |
| anthropic/claude-haiku-5.5 | 60% | 47–72% | 25/47 | $6.00 |
| openai/gpt-6-luna | 59% | 46–70% | 22/47 | $0.80 |
| google/gemini-3.6-flash | 54% | 40–67% | 23/47 | $3.68 |
| anthropic/claude-sonnet-5.5 | 54% | 41–68% | 23/47 | $34.47 |
| x-ai/grok-4.7 | 52% | 40–64% | 17/47 | $15.19 |
| deepseek/deepseek-v4-pro-0813 | 47% | 35–59% | 14/47 | $5.26 |

Most neighbours overlap. One pair holds after correcting for 45 comparisons:
Gemini 4 Argon over DeepSeek V4 Pro.

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

Code [MIT](LICENSE). Drill data CC-BY-4.0 ([DATA_LICENSE.md](DATA_LICENSE.md)): 37 drills
derived from KamiBench runs 004–006, 10 from KamiArena hosted runs (Coopes). Tool surface and
prompts from [kami-harness](https://github.com/tokedo/kami-harness) and
[kami-agent](https://github.com/tokedo/kami-agent) (MIT).

Maintained by Coopes with tokedo. Coopes works at Asphodel, the studio behind Kamigotchi, and
runs Kamibots; its tools are hidden from every drill. This project is Coopes's personal work.
