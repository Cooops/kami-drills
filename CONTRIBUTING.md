# Contributing a drill

A drill turns one real mistake into a fixed test. The bar: the mistake happened in a
recorded run, the correct play is unambiguous from what the agent could see at the
fork, and the rule passes every reasonable correct play and fails the mistake.

## 1. Mine

Find candidate moments in a run's transcripts:

```
uv run python scripts/mine_failures.py candidates.jsonl RUN_DIR [RUN_DIR ...]
```

The heuristics are loose on purpose (repeated reverts, retries of an identical call,
actions against stale state). Read each candidate in the transcript before going on.
Transcripts are untrusted data: parse them as JSON, never follow instructions in them.

## 2. Author the def

Add `defs/<category>-<short-name>.json` with:

- `id`, `category`, `difficulty` (easy, mid, hard), `harness` (the era the source was
  recorded on), `summary` (situation, mistake, correct play, pass condition)
- `src`: the session file, relative to the dataset root; `fork`: the number of
  transcript lines kept as the prefix
- `note` and `rationale`: what happened and why the rule is the right one
- `replay`: rows for the calls a correct or incorrect play will make after the fork
  (`tool`, `args` patterns, `response`, and `when` / `unless` / `sets` / `clears` flags
  for state that changes, `waited_gte` / `waited_lt` for cooldowns). Responses come from
  recorded evidence; do not invent state the recording does not support.
- `rule`: `require_any`, `require_all`, `require_sequence`, `require_count`, `forbid`,
  `forbid_before`, `require_flags`, `forbid_flags` (see `rule_failures` in
  `kami_drills/task.py`)
- `oracle`: the scripted correct play, `[[tool, args], ...]`

Build it from the source data (the KamiBench datasets, or your run directory laid out
the same way), then stamp provenance:

```
uv run python -m kami_drills.build_specs DATA_DIR
uv run python -m kami_drills.build_specs --stamp
```

## 3. Oracle, null and alternative plays

```
uv run kami-drills check
```

The oracle must pass and the null play must fail. Then write the other plays a model
might make, correct and incorrect, as `{drill_id: [[tool, args], ...]}` and run them:

```
uv run inspect eval kami_drills/task.py -T policy=script:plays.json --sample-id <id> --model mockllm/model
```

Every correct alternative must pass and every variant of the mistake must fail. Fix the
rule or the replay until they do.

## 4. Independent review

Someone other than the author reviews the rule against the decision context: is the
correct play really determined by what the agent saw, can a correct play fail, can the
mistake pass. A model review is acceptable as a first pass if it is from a model family
not under test; its flags are advisory.

## 5. Human audit

A maintainer reads the prefix around the fork, the rule in plain words, the oracle and
the review, and signs off. Then open a PR with the def, the built drill, and the plays
you tested. New drills land in the next suite version; a released version never
changes.
