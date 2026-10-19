# Data licence

The drill data in `drills/` (transcript prefixes, recorded tool responses and the
shared world-read library) is licensed under
[CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/). Each drill names its source
in its `provenance` block.

- 37 drills are derived from the KamiBench datasets by tokedo
  ([huggingface.co/KamiBench](https://huggingface.co/KamiBench), runs 004, 005 and 006),
  CC-BY-4.0.
- 10 drills are derived from Palaestra (formerly KamiArena) hosted runs (Coopes), CC-BY-4.0. Drill provenance fields keep the original "KamiArena" label.

Credit when reusing a drill: the drill's `provenance.credit` line and a link to this
repository. The authoring sources in `defs/`, the code and the tool surface copied from
the KamiBench stack are MIT (see [LICENSE](LICENSE) and `kami_drills/surface/SOURCE`, [third_party/NOTICE.md](third_party/NOTICE.md)).
