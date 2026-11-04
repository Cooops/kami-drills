"""Freeze a bank (F review m1, E review M6): a versioned directory with the public spec files, a manifest that hashes
each of them, and a hash-only commitment for the held-out variants. The private files go to <bank>/private/
(gitignored). Membership is then fixed: a later mining pass adds a new version, it never reselects.

    bank/<version>/manifest.json   committed: ids, content hashes, file sha256s, commitments, root hash
    bank/<version>/public/*.json   committed: one spec per public item (controls included)
    bank/<version>/private/        never committed: heldout.jsonl (variants)
"""
import hashlib
import json
from pathlib import Path

from kami_drills.miner import MINER_VERSION
from kami_drills.miner.split import mac
from kami_drills.spec import content_hash
from kami_drills.spec.from_miner import CANARY
from kami_drills.spec.rules import NAME as RULES_LIBRARY


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def commitment(hashes):
    """sha256 over the sorted content hashes: publish this now, the items after the season; anyone can recompute it."""
    return "sha256:" + _sha("\n".join(sorted(hashes)).encode())


def freeze(written, bank_dir, version, key, runs):
    pub, private = written["rows"]
    d = Path(bank_dir)
    (d / "public").mkdir(parents=True, exist_ok=True)
    (d / "private").mkdir(exist_ok=True)
    entries = []
    for r in sorted((r for r in pub if not r.get("redacted")), key=lambda r: r["spec"]["id"]):
        blob = (json.dumps(r["spec"], indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode()
        f = d / "public" / f"{r['spec']['id']}.json"
        f.write_bytes(blob)
        entries.append({"id": r["spec"]["id"], "version": r["spec"]["version"], "content_hash": r["spec"]["content_hash"],
                        "file": f"public/{f.name}", "sha256": _sha(blob), "rule_id": r["rule_id"], "tier": r["tier"],
                        "kind": r["kind"], "group": r["spec"]["variants"].get("group")})
    held = [p["variant"] for p in private]
    (d / "private" / "heldout.jsonl").write_text("".join(json.dumps(v, sort_keys=True, ensure_ascii=False) + "\n" for v in held))
    man = {"bank": version, "spec_version": "2.0.0", "rules_library": RULES_LIBRARY, "miner": MINER_VERSION, "canary": CANARY,
           "source": f"palaestra/s1 ({runs} runs)", "split_key_id": mac(key, "key-id")[:12],
           "public": entries,
           "heldout": {"count": len(held), "stub_ids": sorted(p["stub"]["id"] for p in private),
                       "commitment": commitment(v["content_hash"] for v in held)}}
    man["root"] = "sha256:" + _sha(json.dumps(man, sort_keys=True).encode())
    (d / "manifest.json").write_text(json.dumps(man, indent=1, sort_keys=True) + "\n")
    return man


def verify(bank_dir):
    """Problems with a frozen bank: file hashes, spec content hashes, the root hash, and (when the private files are
    present) the commitments. [] means intact."""
    d = Path(bank_dir)
    man = json.loads((d / "manifest.json").read_text())
    bad = []
    root = man.pop("root")
    if root != "sha256:" + _sha(json.dumps(man, sort_keys=True).encode()):
        bad.append("root hash")
    for e in man["public"]:
        blob = (d / e["file"]).read_bytes()
        if _sha(blob) != e["sha256"]:
            bad.append(f"{e['id']}: file sha256")
        if content_hash(json.loads(blob)) != e["content_hash"]:
            bad.append(f"{e['id']}: content hash")
    p = d / "private" / "heldout.jsonl"
    if p.exists():
        hs = [content_hash(json.loads(x)) for x in p.read_text().splitlines() if x]
        if commitment(hs) != man["heldout"]["commitment"]:
            bad.append("heldout commitment")
    return bad
