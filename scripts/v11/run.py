"""Build specs/v1.1 (overlay + fixes), fill the native half with `kami-drills spec from-v1`, write MANIFEST.json."""
import json, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import conv, fixes
OUT = conv.WT / "specs/v1.1"
OUT.mkdir(parents=True, exist_ok=True)
man = []
for i in conv.all_ids():
    s, m = fixes.build(i)
    (OUT / f"{i}.json").write_text(json.dumps(s, indent=1, ensure_ascii=False) + "\n")
    man.append(m)
paths = [str(OUT / f"{m['id']}.json") for m in man]
r = subprocess.run([str(conv.WT / ".venv/bin/kami-drills"), "spec", "from-v1", *paths], cwd=conv.WT, capture_output=True, text=True)
print(r.stdout[-300:], r.stderr[-2000:])
if r.returncode:
    sys.exit(1)
for m in man:
    m["content_hash"] = json.loads((OUT / f"{m['id']}.json").read_text())["content_hash"]
counts = {k: sum(m["role"] == k for m in man) for k in ("ranking", "smoke", "retired")}
(OUT / "MANIFEST.json").write_text(json.dumps({"suite": "kami-drills v1.1", "spec_version": "2.0.0", "source": "v1.0.0 drills/ (frozen)",
    "counts": counts, "drills": man}, indent=1) + "\n")
print(counts)
