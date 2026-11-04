"""Held-out split, private variants and the leak check (F review B1).

The logs and the miner are public, so a held-out item mined from them cannot be secret: anyone can re-mine the
original. The held-out set is therefore a set of private perturbed variants (spec/perturb.py: renamed kami and
account ids, shifted clock) generated with a private key, and nothing public points at their source:

  key        KAMI_DRILLS_SPLIT_KEY, or the untracked file .split-key at the repo root (gitignored). Never committed.
  unit       items split together: same (run, session) - a later fork's prefix embeds the earlier one - or same group
             (item + control, opportunity + trap). Held out when HMAC(key, unit) puts it in the held-out third.
  public     full rows for public items; a held-out item appears only as a stub: an opaque id HMAC(key, id) and its
             labels (kind, rule, class, competency, tier). No run, session, line, kami, quest or text.
  private    <stem>.heldout.jsonl: the variant spec (what models get) and the original candidate (audit).
  pseudonyms every spec, public or variant, carries pseudonymized third-party wallets and players
             (spec/pseudonymize.py) under a key derived from the split key; the S1 agents' own wallets stay.

Without a key nothing is split: every item is written to <stem>.unsplit.jsonl and no public file is produced.
"""
import hashlib
import hmac
import json
import os
import random
import re
import shutil
import tempfile
from pathlib import Path

from kami_drills.spec import check as CK
from kami_drills.spec import perturb, pseudonymize
from kami_drills.spec.from_miner import CANARY, stamp

KEY_ENV = "KAMI_DRILLS_SPLIT_KEY"
KEY_FILE = Path(__file__).parents[2] / ".split-key"
STUB_KEYS = ("schema", "kind", "rule_id", "mistake_class", "competency", "tier")
STUB_ALLOWED = set(STUB_KEYS) | {"id", "heldout", "redacted", "canary"}
HELDOUT_EVERY = 3  # one unit in three
# families whose answer the perturbation cannot hide: quest ids and merchant item ids are game content, not renamed
# (market-price: the original's oracle passed its rc4 variant)
NOT_HELD_OUT = ("quest-left@", "market-price@")


def load_key(path=None):
    """The split key as bytes, or None. The env var wins over the file."""
    k = os.environ.get(KEY_ENV)
    if not k and Path(path or KEY_FILE).exists():
        k = Path(path or KEY_FILE).read_text().strip()
    return k.encode() if k else None


def mac(key, msg):
    return hmac.new(key, msg.encode(), hashlib.sha256).hexdigest()


def opaque(key, cid):
    return "h" + mac(key, "id|" + cid)[:15]


def session_of(c):
    p = c["provenance"]
    return f"{p['run']}|{p['session']}"


def units(items):
    """{item id: unit key}: union of same-session items and same-group items (union-find), keyed by the smallest member."""
    parent = {c["id"]: c["id"] for c in items}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    by = {}
    for c in items:
        for k in (("s", session_of(c)), ("g", c.get("group"))):
            if k[1] is None:
                continue
            if k in by:
                a, b = find(by[k]), find(c["id"])
                parent[max(a, b)] = min(a, b)
            else:
                by[k] = c["id"]
    sess = {c["id"]: session_of(c) for c in items}
    roots = {}
    for c in items:
        roots.setdefault(find(c["id"]), []).append(sess[c["id"]])
    return {c["id"]: min(roots[find(c["id"])]) for c in items}


def is_heldout(key, unit):
    return int(mac(key, "split|" + unit)[:8], 16) % HELDOUT_EVERY == 0


def stub(c, key):
    return {"id": opaque(key, c["id"]), **{k: c[k] for k in STUB_KEYS}, "heldout": True, "redacted": True, "canary": CANARY}


def variant(c, key, group=None, avoid=()):
    """The private perturbed variant of a held-out item's spec, or None if perturbing changed a verdict."""
    v, changes = perturb.perturb(c["spec"], random.Random(mac(key, "variant|" + c["id"])), avoid)
    v["id"] = opaque(key, c["id"])
    v["heldout"] = {"flag": True, "method": "private_variant", "season": "s2"}
    origin = v["variants"]["origin"] if v["variants"]["origin"] == "trap_control" else "synthetic_param"  # a control stays a control
    v["variants"] = {**{k: x for k, x in v["variants"].items() if k != "group"}, "origin": origin,
                     "note": f"private variant: {len(changes['ids'])} id(s) renamed, clock shifted"}
    if group:
        v["variants"]["group"] = "h" + mac(key, "group|" + group)[:15]
    v.pop("candidate", None)
    stamp(v)
    if left := perturb.survivors(v, changes):  # H review M2: an original id left in the text links the variant to its source
        return None, {"original ids survive": left}
    diff = perturb.check_equivalent(c["spec"], v, lambda s: CK.check(s))
    return (None, diff) if diff else (v, changes)


def pseudonymized(c, key):
    """The item with its spec pseudonymized (owner decision 2026-11-03); raises if that changed a verdict."""
    p, _ = pseudonymize.pseudonymize(c["spec"], mac(key, "pseudonymize").encode())
    stamp(p)
    if diff := perturb.check_equivalent(c["spec"], p, lambda s: CK.check(s)):
        raise ValueError(f"{c['id']}: pseudonymization changed a verdict: {diff}")
    return dict(c, spec=p)


def split(result, key):
    """(public rows, private rows, dropped) for a mining result."""
    items = [pseudonymized(c, key) for c in sorted(result["candidates"] + result["controls"] + result["traps"], key=lambda c: c["id"])]
    unit = units(items)
    # quest and item ids are game content and are not renamed, so a quest-left or market-price variant would still carry
    # its answer (H review M3): a unit with such an item stays public
    quest_units = {unit[c["id"]] for c in items if c["rule_id"].startswith(NOT_HELD_OUT)}
    held = [c for c in items if unit[c["id"]] not in quest_units and is_heldout(key, unit[c["id"]])]
    pub = [public_row(c) for c in items if c not in held]
    avoid = {int(x) for r in pub for x in re.findall(r"(?<!\d)\d{3,6}(?!\d)", json.dumps(r))}  # variant ids never collide with public numbers
    private, dropped = [], []
    for c in held:
        v, info = variant(c, key, c.get("group"), avoid)
        if v is None:
            dropped.append((c, f"variant_changed_verdict:{sorted(info)[:3]}"))
            continue
        private.append({"stub": stub(c, key), "variant": v, "perturbation": info, "orig": c})
    pub += [p["stub"] for p in private]
    return pub, private, dropped


def public_row(c):
    return {**{k: v for k, v in c.items() if k not in ("notes",)}, "heldout": False, "canary": CANARY}


# ---------------------------------------------------------------- leak check

def _num(t):
    return isinstance(t, int) or (isinstance(t, str) and t.isdigit())


def answer_tokens(c):
    """Strings that reveal a private item's answer: oracle call strings and argument values, situation and objective."""
    toks = set()
    for tool, args in c.get("oracle") or []:
        toks.add(f"{tool}({json.dumps(args, sort_keys=True)})")
        for v in args.values():
            for x in (v if isinstance(v, list) else [v]):
                if isinstance(x, (int, str)) and len(str(x)) >= 3:
                    toks.add(str(x))
    toks |= {x for x in [c.get("situation", {}).get("text"), (c.get("objective") or {}).get("game")]
             if isinstance(x, str) and len(x) >= 12}
    return toks


def _found(tok, text):
    if tok.isdigit():  # digit boundaries only: 'q3034' and 'L7-3034' still hit (F review B1: \\w boundary missed them)
        return re.search(rf"(?<!\d){tok}(?!\d)", text) is not None
    return tok in text


def leak_check(public_rows, private, stats_text=""):
    """Every place a private item shows up in public output: [(private id, where, token)].
    private: [{"orig": candidate, "variant": spec, "stub": stub}]."""
    leaks = []
    text = {r["id"]: json.dumps(r, sort_keys=True, ensure_ascii=False) for r in public_rows}
    stubs = [r for r in public_rows if r.get("redacted")]
    full = [r for r in public_rows if not r.get("redacted")]
    # a public item's own answer tokens are family vocabulary (a fixed revive method, a trap's template situation), not
    # anything that singles out a private item
    shared = set().union(*(answer_tokens(r) for r in full))
    for r in stubs:
        if extra := set(r) - STUB_ALLOWED:
            leaks.append((r["id"], "stub carries fields", ",".join(sorted(extra))))
    for p in private:
        o = p["orig"]
        oid, sess = o["id"], session_of(o)
        for r in full:
            if "provenance" in r and session_of(r) == sess:
                leaks.append((oid, f"public row {r['id']} from the same session", sess))
        toks = answer_tokens(o) - shared
        vtoks = {str(n) for n in (p.get("perturbation") or {}).get("ids", {}).values()}
        for rid, t in text.items():
            if oid in t:
                leaks.append((oid, f"public row {rid}", "the private item's id"))
            if p.get("variant") and p["variant"]["id"] in t and not (p.get("stub") and rid == p["stub"]["id"]):
                leaks.append((oid, f"public row {rid}", "the variant's id"))
        for r in stubs:
            leaks += [(oid, f"stub {r['id']}", tok) for tok in toks if _found(tok, text[r["id"]])]
        for r in full:
            leaks += [(oid, f"public row {r['id']}", tok) for tok in toks | vtoks if not tok.isdigit() and _found(tok, text[r["id"]])]
            leaks += [(oid, f"public row {r['id']}", tok) for tok in vtoks if _found(tok, text[r["id"]])]
        if stats_text:
            leaks += [(oid, "stats", tok) for tok in toks | vtoks | {oid} if len(tok) >= (5 if tok.isdigit() else 4) and _found(tok, stats_text)]
    return sorted(set(leaks))


# ---------------------------------------------------------------- files

def _dump(rows, p):
    p.write_text("".join(json.dumps(r, sort_keys=True, default=str, ensure_ascii=False) + "\n" for r in rows))


def write(result, out, key, render=None):
    """Write the mining output. Everything goes to a temporary directory first and is moved into place only when the
    leak check is clean (F review m3). `render(written) -> stats text` is leak-checked too. Returns a dict with the
    paths, rows, leaks and sha256[:16] of the public file."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".mine-", dir=out.parent))
    names = {}
    try:
        if key is None:
            rows = [{**public_row(c), "heldout": None} for c in result["candidates"] + result["controls"] + result["traps"]]
            names["unsplit"] = out.with_name(out.stem + ".unsplit.jsonl")
            _dump(rows, tmp / names["unsplit"].name)
            pub, private, dropped = [], [], []
        else:
            pub, private, dropped = split(result, key)
            names["public"] = out
            names["heldout"] = out.with_name(out.stem + ".heldout.jsonl")
            _dump(pub, tmp / out.name)
            _dump([{"variant": p["variant"], "perturbation": p["perturbation"], "orig": p["orig"], "stub_id": p["stub"]["id"]}
                   for p in private], tmp / names["heldout"].name)
        names["rejections"] = out.with_name(out.stem + ".rejections.jsonl")
        _dump(result["rejections"] + [{"rule_id": c["rule_id"], "reason": why, "run": c["provenance"]["run"],
                                       "session": c["provenance"]["session"]} for c, why in dropped],
              tmp / names["rejections"].name)
        written = {"paths": {k: str(v) for k, v in names.items()}, "rows": (pub, private), "dropped": dropped,
                   "split": key is not None,
                   "sha256_16": hashlib.sha256((tmp / (out.name if key else names["unsplit"].name)).read_bytes()).hexdigest()[:16]}
        stats = render(written) if render else ""
        leaks = leak_check(pub, private, stats)
        written["leaks"] = leaks
        if leaks:
            return written  # nothing is moved into place; the temporary directory is removed below
        for k, p in names.items():
            shutil.move(str(tmp / p.name), str(p))
        written["stats"] = stats
        return written
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
