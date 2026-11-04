"""Perturbed variants: the same drill with renamed kami and account ids and a shifted clock (D-spec 6.2, F review B1).

Held-out items are private variants of mined situations. The source logs and the miner are public, so the original
situation can always be re-mined; the variant's answer cannot be recalled from it (other ids, other times). The
algorithm is public, the seed is private (split key), so the variants are reproducible only by the key holder.

The edit is textual over the canonical JSON, with digit boundaries, so every place an id or a time appears (state,
patterns, reference plays, the native prefix and recorded reads the model sees) moves together.
"""
import copy
import json
import re
from datetime import datetime, timedelta

ISO = re.compile(r"\b(20\d\d-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?)(Z|[+-]\d\d:\d\d)?")
DATE = re.compile(r"(?<![\d-])(20\d\d-\d\d-\d\d)(?![\dT])")
UNIX = re.compile(r"(?<![\w.])(1[6-9]\d{8})(\.\d+)?(?![\w])")
BLOCK = re.compile(r"(?<![\w.])(\d{7,9})(?!\w)(?!\.\d)")


def _id_rx(old):
    # a sentence-final period ends the number ("kami 4278."); a decimal point does not ("4278.5" is not the id)
    return rf"(?<![0-9A-Za-z])(?<!\d\.){old}(?![0-9A-Za-z])(?!\.\d)"


def _sub_id(text, old, new):
    return re.sub(_id_rx(old), str(new), text)


def survivors(variant, changes):
    """Original ids still present in the variant (must be [] before it is used)."""
    blob = json.dumps(variant, ensure_ascii=False)
    return [old for old in changes["ids"] if re.search(_id_rx(old), blob)]


def perturb(spec, rng, avoid=()):
    """(variant spec, changes) for a random.Random `rng`. Ids under 100 are not renamed (they collide with counts); new ids
    avoid every number in the spec and in `avoid` (e.g. all numbers in the public bank)."""
    blob = json.dumps(spec, sort_keys=True, ensure_ascii=False)
    ids = sorted({k["index"] for k in spec["state"]["kamis"] if k["index"] >= 100} | ({spec["state"]["account"]["index"]} - {0}),
                 key=lambda x: -len(str(x)))  # longest first, so 1234 is not rewritten inside 12345
    taken = {int(x) for x in re.findall(r"\d+", blob) if len(x) <= 6} | set(avoid)
    idmap = {}
    for old in ids:
        new = rng.randrange(10 ** (len(str(old)) - 1), 10 ** len(str(old)))
        while new in taken or new in idmap.values():
            new = rng.randrange(10 ** (len(str(old)) - 1), 10 ** len(str(old)))
        idmap[old] = new
    clock = spec["fork"]["clock"]["unix"]
    shift = rng.randrange(3, 40) * 86400 + rng.randrange(0, 86400)
    block = spec["fork"]["clock"].get("block")
    block_shift = rng.randrange(100_000, 900_000)
    for old, new in idmap.items():
        blob = _sub_id(blob, old, new)

    def unix(m):
        v = int(m.group(1))
        return f"{v + shift}{m.group(2) or ''}" if abs(v - clock) < 30 * 86400 else m.group(0)

    def iso(m):
        try:
            d = datetime.fromisoformat(m.group(1) + (m.group(2) or "").replace("Z", "+00:00"))
        except ValueError:
            return m.group(0)
        out = (d + timedelta(seconds=shift)).isoformat()
        if not m.group(2):
            out = out.split("+")[0]
        elif m.group(2) == "Z":
            out = out.replace("+00:00", "Z")
        return out
    def date(m):
        try:
            return (datetime.fromisoformat(m.group(1)) + timedelta(seconds=shift)).date().isoformat()
        except ValueError:
            return m.group(0)

    def blk(m):
        v = int(m.group(1))
        return str(v + block_shift) if block and abs(v - block) < 5_000_000 else m.group(0)
    blob = BLOCK.sub(blk, DATE.sub(date, UNIX.sub(unix, ISO.sub(iso, blob))))
    out = json.loads(blob)
    out["fork"]["clock"]["unix"] = clock + shift  # exact (the textual edit may have rounded a float clock)
    return out, {"ids": idmap, "clock_shift_s": shift, "block_shift": block_shift if block else 0}


def check_equivalent(spec, variant, check):
    """The variant must keep every reference-play verdict and the battery outcome of its base (check = spec.check.check)."""
    a, b = check(copy.deepcopy(spec)), check(copy.deepcopy(variant))
    same = {n: (a["plays"][n]["pass"], b["plays"].get(n, {}).get("pass")) for n in a["plays"]}
    diff = {n: v for n, v in same.items() if v[0] != v[1]} | {
        f"battery:{n}": (v, b["battery"].get(n)) for n, v in a["battery"].items() if v != b["battery"].get(n)}
    return diff
