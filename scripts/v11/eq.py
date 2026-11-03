"""Equivalence of the mechanical conversion vs v1 verdicts on the recorded rc2 tries (B-data/plays.json)."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import conv  # noqa: E402
from kami_drills.spec import from_v1  # noqa: E402

# recorded rc2 tries (11 models x 2 tries x 47 drills), outside the repo: $KAMI_DRILLS_PLAYS
PLAYS = json.loads(Path(os.environ.get("KAMI_DRILLS_PLAYS", "drills-s1/B-data/plays.json")).expanduser().read_text())


def tries(did):
    out = []
    for model, drills in sorted(PLAYS.items()):
        for t, p in enumerate(drills.get(did, [])):
            out.append({"model": model, "try": t, "pass": p["pass"], "steps": p["steps"]})
    return out


if __name__ == "__main__":
    ids = sys.argv[1:] or conv.all_ids()
    tot = bad = 0
    for i in ids:
        v1 = conv.load_v1(i)
        spec = from_v1.convert(v1, conv.overlay(v1))
        rows = from_v1.equivalence(spec, tries(i))
        mism = [r for r in rows if r["v1"] != r["v2"]]
        tot += len(rows)
        bad += len(mism)
        print(f"{i:45s} {len(rows) - len(mism)}/{len(rows)}" + ("" if not mism else "  " + "; ".join(f"{r['model'][:14]}#{r['try']} v1={r['v1']} v2={r['v2']} {r['why'][:1]}" for r in mism[:3])))
    print("total", tot - bad, "/", tot)
