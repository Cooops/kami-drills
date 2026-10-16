"""Build drill specs by forking real kami-agent transcripts at a decision point.

Usage: uv run python -m kami_drills.build_specs HFDATA_DIR   (build drills/ from defs/ and the source transcripts)
       uv run python -m kami_drills.build_specs --stamp        (refresh only the provenance block of drills/*.json)
Each spec embeds the transcript prefix (CC-BY-4.0) so drills run without the source data.
"""
import hashlib
import json
import re
import sys
from pathlib import Path

from kami_drills import STACK, SUITE_VERSION

ROOT = Path(__file__).parent.parent

# Reads that describe the account's or a kami's current state: an earlier session's answer is stale and contradicts
# the fork (a RESTING lens_kami next to a "kami not RESTING" revert), so they come only from the forked session or the spec.
STATE_READS = {"lens_kami", "lens_roster", "lens_account", "lens_party", "lens_inventory", "get_gas_balance", "lens_status",
               "quest_state", "get_status", "lens_receipts"}
OK = '{"tx_hash": "0xd1d1d1", "status": "success", "account": "main"}'
QUEST_ACTIVE = '{"state": "active_blocked", "completable": false, "revert_kind": "reqs", "revert_reason": "reqs not met"}'
COOLDOWN = "transaction dry-run reverted: {'code': -32000, 'message': 'revert: kami on cooldown: Reverted'}"

# One drill per file in defs/: id, category, difficulty, src (KamiBench HF path), fork (prefix line count), note,
# rationale, replay rows, rule, oracle (scripted correct play). Shared constants above are for authoring defs.
def is_read(name):
    return bool(name) and (name.startswith(("lens_", "get_", "check_", "list_")) or name in {"quest_state", "pool_swap_quote", "workspace_read", "workspace_list"})


def argkey(args):
    """Canonical args for matching a replayed read; the account label is the same account in every drill."""
    return json.dumps({k: v for k, v in (args or {}).items() if k != "account"}, sort_keys=True)


DRILLS = [json.loads(p.read_text()) for p in sorted((ROOT / "defs").glob("*.json"))]

def build(hf):
    out = ROOT / "drills"
    out.mkdir(exist_ok=True)
    for d in DRILLS:
        lines = (Path(hf) / d["src"]).read_text().splitlines()[: d["fork"]]
        prefix = []
        for line in lines:
            m = json.loads(line)
            m.pop("provider_state", None)  # provider-specific thinking signatures don't transfer across models
            prefix.append(m)
        # replay reads from BEFORE the fork only (this session's prefix, then earlier sessions of the same run):
        # anything recorded after the fork would leak the future state to the model
        recorded, calls = {}, {}
        sess = Path(hf) / d["src"]
        earlier = sorted(p for p in sess.parent.glob("session-*.jsonl") if p.name < sess.name)
        sources = [sess.read_text().splitlines()[: d["fork"]]] + [p.read_text().splitlines() for p in reversed(earlier[-20:])]
        for src_lines in sources:
            seen = {}
            for line in src_lines:
                m = json.loads(line)
                for tc in m.get("tool_calls") or []:
                    calls[tc["id"]] = (tc["name"], argkey(tc.get("args")))
                if m.get("role") == "tool_result" and not m.get("is_error"):
                    name, args = calls.get(m["tool_call_id"], (None, None))
                    if is_read(name):  # an earlier action's success says nothing about the same action now
                        seen[(name, args)] = m["content"]  # latest response in this session
            for (k, a), v in seen.items():
                if src_lines is sources[0] or k not in STATE_READS:
                    recorded.setdefault(k, {}).setdefault(a, v)  # nearer sessions win; state reads only from the forked session
        spec = {**d, "prefix": prefix, "recorded_reads": recorded, "license": licence(d["src"])}
        spec["provenance"] = provenance(spec)
        (out / f"{d['id']}.json").write_text(json.dumps(spec, indent=1))
        print(d["id"], len(prefix), "prefix messages")


# World-level reads are the same for every account, so a recorded response from any run is a fair stand-in.
# Account- or kami-specific reads are never borrowed across runs. get_expected_objective serves the harness's static quest
# catalog (identical bytes for each quest_index in every run), not chain state.
GLOBAL_READS = {"lens_market", "lens_node", "lens_room", "lens_config", "lens_items", "lens_item", "lens_merchant", "lens_phase",
                "lens_leaderboard", "lens_skills", "get_scavenge_droptable", "get_item_orderbook", "lens_pool_history", "lens_auctions",
                "get_expected_objective"}


def build_global_reads(hf):
    lib, calls = {}, {}  # tool -> {canonical args json: response}
    for f in sorted(Path(hf).glob("*/*/transcripts/session-*.jsonl")):  # sorted: later sessions overwrite, newest wins
        for line in f.read_text().splitlines():
            m = json.loads(line)
            for tc in m.get("tool_calls") or []:
                calls[tc["id"]] = (tc["name"], json.dumps(tc.get("args") or {}, sort_keys=True))
            name, args = calls.get(m.get("tool_call_id"), (None, None))
            if m.get("role") == "tool_result" and not m.get("is_error") and name in GLOBAL_READS:
                lib.setdefault(name, {})[args] = m["content"]
    (ROOT / "drills" / "_global_reads.json").write_text(json.dumps(lib, indent=1))
    print("global reads:", sorted(lib))


HOSTED = "KamiArena hosted runs (Coopes)"


def licence(src):
    if src.startswith("hosted/"):
        return f"prefix: CC-BY-4.0, {HOSTED}"
    return "prefix: CC-BY-4.0, KamiBench datasets (tokedo); https://huggingface.co/KamiBench"


def chain_block(prefix):
    """Chain block at the fork: the last read in the prefix that states the block it was served at. Lens reads carry
    it as meta.blockNumber; transaction receipts as a top-level block. Nested blockNumbers are history entries (past
    events), not the read's own block, and are ignored. A truncated JSON read falls back to a meta.blockNumber match."""
    calls, found = {}, None
    for line, m in enumerate(prefix, 1):
        for tc in m.get("tool_calls") or []:
            calls[tc["id"]] = tc["name"]
        if m.get("role") != "tool_result":
            continue
        text, block, field = str(m.get("content")), None, None
        try:
            d = json.loads(text)
        except ValueError:
            d = None
            hit = re.search(r'"meta"\s*:\s*\{[^{}]*?"blockNumber"\s*:\s*(\d+)', text)
            block, field = (int(hit.group(1)), "meta.blockNumber") if hit else (None, None)
        if isinstance(d, dict):
            meta = d.get("meta") if isinstance(d.get("meta"), dict) else {}
            for f, v in (("meta.blockNumber", meta.get("blockNumber")), ("meta.block", meta.get("block")),
                         ("blockNumber", d.get("blockNumber")), ("block", d.get("block"))):
                if isinstance(v, int) and v > 0:
                    block, field = v, f
                    break
        if block:
            found = {"block": block, "field": field, "tool": calls.get(m.get("tool_call_id")), "prefix_line": line}
    if found:
        return found
    reads = sorted({calls.get(m.get("tool_call_id")) for m in prefix if m.get("role") == "tool_result"} - {None})
    return {"block": None, "reason": f"no read or receipt in the prefix states a block number (reads: {', '.join(reads) or 'none'})"}


def sha256_file(path):
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


TOOLS_HASH_METHOD = ("sha256 over json.dumps(tools, sort_keys=True, separators=(',', ':'), ensure_ascii=False), tools = "
                     "[{name, description, parameters}] in the order the task registers them (harness_tools.json minus the "
                     "9 OUTSOURCE tools, then the 8 scaffold tools), parameters = ToolParams.model_dump(exclude_none=True) "
                     "as built by kami_drills.task")


def tools_hash():
    from kami_drills import task
    from inspect_ai.tool._tool_params import ToolParams
    tools = [{"name": t["name"], "description": t["description"],
              "parameters": task.params(t["input_schema"]).model_dump(exclude_none=True)}
             for t in task.SURFACE["tools"] if t["name"] not in task.OUTSOURCE]
    for name, args in task.SCAFFOLD.items():
        props = {k: {"type": v, "description": k} for k, v in args.items()}
        tools.append({"name": name, "description": f"kami-agent scaffold tool: {name}",
                      "parameters": ToolParams(properties=props, required=list(props)).model_dump(exclude_none=True)})
    blob = json.dumps(tools, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest(), len(tools)


def provenance(spec):
    top, rest = spec["src"].split("/", 1)
    if top == "hosted":
        run, session = rest.split("/", 1)
        credit, dataset = f"{HOSTED}, CC-BY-4.0", f"hosted/{run} (unpublished; the prefix is embedded in this file)"
    else:
        run = re.match(r"experiment-(\d+)", top).group(1)
        credit = f"derived from KamiBench run {run}, CC-BY-4.0"
        dataset, session = f"https://huggingface.co/datasets/KamiBench/{top}", rest
    th, n = tools_hash()
    return {
        "credit": credit,
        "source": {"dataset": dataset, "session_file": session, "fork_line": spec["fork"],
                   "fork_note": "prefix = transcript lines 1..fork_line; the model acts in place of line fork_line + 1"},
        "chain_block": chain_block(spec["prefix"]),
        "stack": {**STACK, "drill_harness": spec.get("harness")},
        "tools_hash": {"exposed": th, "exposed_tool_count": n, "method": TOOLS_HASH_METHOD,
                       "harness_published": SURFACE_JSON["harness_published_tools_hash"],
                       "harness_published_note": "stock kami-harness 4.7.0 surface, from kami_drills/surface/harness_tools.json"},
        "judge_hash": {"kami_drills/task.py": sha256_file(ROOT / "kami_drills" / "task.py")},
        "suite_version": SUITE_VERSION,
    }


SURFACE_JSON = json.loads((ROOT / "kami_drills" / "surface" / "harness_tools.json").read_text())


def stamp():
    for p in sorted((ROOT / "drills").glob("*.json")):
        if p.name.startswith("_"):
            continue
        spec = json.loads(p.read_text())
        spec["license"] = licence(spec["src"])
        spec["provenance"] = provenance(spec)
        p.write_text(json.dumps(spec, indent=1))
        print(spec["id"], spec["provenance"]["chain_block"].get("block"))


if __name__ == "__main__":
    if sys.argv[1:] == ["--stamp"]:
        stamp()
    else:
        build(sys.argv[1])
        build_global_reads(sys.argv[1])
