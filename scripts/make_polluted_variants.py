"""Build polluted-context variants of selected v1 drills into drills_v2/polluted/ (docs/v2-polluted.md).

Each variant is the v1 drill with up to two real failed tool calls from a sibling drill of the same source run
copied verbatim into its prefix, right after "Session start.". Rule, replay and oracle are untouched.
Deterministic, no network, no model calls:  uv run python scripts/make_polluted_variants.py
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "drills_v2" / "polluted"
# Spread over categories, weighted to risk, memory and progression (the rc2 separators); none at a 0% or 100% floor.
SELECTED = [
    "risk-lowhp-feed-first", "risk-unmanaged-lowhp",
    "memory-sonnet-slow-points-belief", "memory-sonnet-avoid-registry-dump", "memory-luna-plan-level-after-rest",
    "prog-sonnet-unspent-skill-points", "prog-gpt52-locked-quest-chain", "prog-timewall-quest19-to-20",
    "prog-luna-two-levels-pending", "stale-harvest-start-wrong-room", "stale-notes-wrong-node-stop",
    "repeat-pool-swap-retry",
]
MAX_ERRORS, MAX_CHARS = 2, 1500  # per variant; skip bulky error replies


def load_v1():
    return {d["id"]: d for d in (json.loads(p.read_text()) for p in sorted((ROOT / "drills").glob("*.json"))
                                 if not p.name.startswith("_"))}


def run_of(d):
    return "/".join(d["src"].split("/")[:2])  # dataset/agent dir, or hosted/<run>


def failed_calls(d):
    """(prefix index of the result, assistant call, tool result) for each failed call in the prefix, in order."""
    calls = {c["id"]: c for m in d["prefix"] for c in (m.get("tool_calls") or [])}
    return [(i, calls[m["tool_call_id"]], m) for i, m in enumerate(d["prefix"])
            if m["role"] == "tool_result" and m.get("is_error") and m["tool_call_id"] in calls
            and len(m["content"]) <= MAX_CHARS]


def digest(obj):
    return "sha256:" + hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                                 ensure_ascii=False).encode()).hexdigest()


def variant(d, v1):
    used = {c["id"] for m in d["prefix"] for c in (m.get("tool_calls") or [])}
    donors = [e for i, e in sorted(v1.items()) if i != d["id"] and run_of(e) == run_of(d)
              and any(f[1]["id"] not in used for f in failed_calls(e))]
    if not donors:
        raise SystemExit(f"{d['id']}: no sibling drill with a failed call in its source run")
    donor = donors[0]
    msgs, injected = [], []
    for idx, call, res in [f for f in failed_calls(donor) if f[1]["id"] not in used][:MAX_ERRORS]:  # ids stay verbatim
        msgs += [{"role": "assistant", "text": None, "tool_calls": [call]}, res]
        injected.append({"tool": call["name"], "args": call["args"], "error": res["content"],
                         "origin": {"drill": donor["id"], "session_file": donor["src"], "prefix_index": idx}})
    assert d["prefix"][0]["role"] == "user"
    v = dict(d, id=d["id"] + "--polluted", prefix=d["prefix"][:1] + msgs + d["prefix"][1:],
             keep_lines=[k + len(msgs) if k >= 1 else k for k in d.get("keep_lines", [])])
    v["polluted"] = {"source_drill": d["id"], "injected": injected, "inserted_messages": len(msgs)}
    v["polluted"]["variant_hash"] = digest({k: x for k, x in v.items() if k != "polluted"} | {"injected": injected})
    return v


def build():
    v1 = load_v1()
    return {f"{i}--polluted.json": json.dumps(variant(v1[i], v1), indent=1, ensure_ascii=False) + "\n"
            for i in SELECTED}


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    files = build()
    for name, text in files.items():
        (OUT / name).write_text(text)
    print(f"wrote {len(files)} variants to {OUT.relative_to(ROOT)}")
