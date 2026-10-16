# Third-party material

`kami_drills/surface/` reproduces the agent-facing tool surface and prompts of two MIT-licensed
projects by tokedo, so that drills show a model the same surface the original agents saw.
Each project's licence is reproduced verbatim in this directory.

| Upstream | Version | Commit | Copied into this repo | Licence file |
|---|---|---|---|---|
| [kami-agent](https://github.com/tokedo/kami-agent) | 0.7.5 | `8e444bb5ca4fadc70f5487be901136f885310e19` | `kami_drills/surface/{system,kickoff,continue,orientation,planning}.txt` (prompts), `kami_drills/surface/scaffold.py` (scaffold tool definitions) | [LICENSE-kami-agent](LICENSE-kami-agent) |
| [kami-harness](https://github.com/tokedo/kami-harness) | 4.7.0 | `df7129f0b762e91994cb75c793e8755777bbd037` | `kami_drills/surface/harness_tools.json` (tool definitions and standing text) | [LICENSE-kami-harness](LICENSE-kami-harness) |

The recorded transcripts and game data in `drills/` come from KamiBench (tokedo) and KamiArena
hosted runs (Coopes) and are licensed separately; see [DATA_LICENSE.md](../DATA_LICENSE.md).
