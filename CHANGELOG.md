# Changelog

Suite versions name the drill set and judge together. A result is comparable to
another only at the same suite version and stack.

## v1.0.0-rc1 (2026-10-16)

First release, extracted from KamiArena's `drills/` at tag `drills-v1.0.0-rc1`.
Release candidate until the KamiArena Season 1 freeze.

- 47 drills in 8 categories (onboarding 7, progression 7, levelup 1, memory 6,
  repeat_failed 10, risk 5, stale_state 10, stalling 1), forked from KamiBench runs
  004, 005, 006 and two KamiArena hosted runs.
- Replay, rules and oracles are byte-identical to the KamiArena source; each drill adds
  a `provenance` block (credit, source transcript and fork line, chain block, stack
  pins, tools_hash, judge hash). The 10 hosted-run drills now name their own source in
  `license` instead of the KamiBench datasets.
- Stack: kami-harness 4.7.0 `df7129f`, kami-agent 0.7.5 `8e444bb`, kami-lens 1.0.5
  `e604290`, gdd `3c6f4f9`; the 9 OUTSOURCE tools are hidden.
- `kami-drills` CLI: `list`, `check`, `run`, `report`.
- Results: 10 models × 47 drills × 2 tries in `results/v1.0.0-rc1/`.
