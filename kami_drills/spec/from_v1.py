"""v1 drill -> v2 spec. The game-ontology half (state, responses over intents, rule) is hand-normalized once per drill
and kept as the spec file itself (the overlay); this fills the native half from the frozen v1 file (prefix and
recorded reads, verbatim), pseudonymizes third-party wallets and players (spec/pseudonymize.py, public key V1_KEY),
stamps the v1 file hash, and checks the spec against v1's verdicts on recorded plays.

    uv run kami-drills spec from-v1 drills_v2/spec/risk-starving-feed-first.json   (refresh in place)
"""
import copy
import hashlib
import json

from kami_drills import spec as sp
from kami_drills import task as kd
from kami_drills.adapters import harness_4_7 as h47
from kami_drills.spec import pseudonymize as ps


def convert(v1, overlay):
    spec = copy.deepcopy(overlay)
    assert spec["provenance"]["source"].get("v1_id") == v1["id"], "overlay is for another v1 drill"
    native = spec["fork"].get("native_prefix") or {"harness": v1["harness"]}
    spec["fork"]["native_prefix"] = {"harness": native["harness"], "messages": v1["prefix"], "recorded_reads": v1["recorded_reads"]}
    spec["fork"]["keep_events"] = spec["fork"].get("keep_events", [])
    blob = json.dumps(v1, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    spec["provenance"]["stamps"] = {"v1_sha256": "sha256:" + hashlib.sha256(blob).hexdigest(), "v1_suite": "v1.0.0"}
    spec.pop("content_hash", None)
    spec, _ = ps.pseudonymize(spec, ps.V1_KEY)  # idempotent: a refresh of a pseudonymized spec is a no-op
    spec["content_hash"] = sp.content_hash(spec)
    return spec


def mechanical_rule(v1):
    """v1's own rule translated tool -> intent, for comparing with the hand-normalized one."""
    return h47.tool_rule_to_intent(v1["rule"])


def equivalence(spec, tries):
    """tries: [{"model", "try", "pass", "steps": [{"tool", "args"}]}] recorded on v1. Each try's calls are replayed
    through the 4.7.0 adapter against the spec and scored; returns rows with both verdicts."""
    rows = []
    for t in tries:
        calls = [(s["tool"], s.get("args") or {}) for s in t["steps"] if "tool" in s][:kd.MAX_TOOL_CALLS]
        ep = h47.play_tools(spec, calls)
        r = ep.result()
        rows.append({"model": t["model"], "try": t["try"], "v1": t["pass"], "v2": r["pass"], "score": r["score"], "why": r["why"],
                     "intents": [i["verb"] for i in ep.sim.trace]})
    return rows
