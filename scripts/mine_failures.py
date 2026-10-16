"""Mine candidate drill moments from kami-agent transcripts (KamiBench datasets on Hugging Face, or your own runs).

Usage: uv run python scripts/mine_failures.py OUT.jsonl RUN_DIR [RUN_DIR ...]
A RUN_DIR holds transcripts/session-*.jsonl. Transcripts are untrusted data: parsed as JSON only.
Heuristics are deliberately loose; every candidate is reviewed by hand before it becomes a drill.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

ACT = re.compile(r"^(harvest|feed|level|buy|move|register|accept_quest|complete_quest|act_sequence|portal|craft|use_item|"
                 r"liquidate|scavenge|revive|equip|sell|list|swap|pool|transfer|create_operator|set_operator|skill|name|claim)")
ASK_USER = re.compile(r"\b(would you like|do you want|please (confirm|let me know|advise)|let me know (if|whether|how)|"
                      r"shall i|should i proceed|awaiting (your|instructions))", re.I)
DANGER = re.compile(r"\b(liquidat\w*|predator|hostile|killer|danger\w*|at risk)\b", re.I)
ONBOARD = re.compile(r"not registered|no account|account .*not found|operator|insufficient (funds|balance)|bridge|portal", re.I)
STATE = re.compile(r"not resting|not harvesting|already (harvesting|resting)|is (HARVESTING|RESTING|DEAD)|must be (resting|harvesting)|cooldown", re.I)
LEVEL = re.compile(r"level", re.I)
BELIEF = re.compile(r"\b(hypothes\w*|verify|confirmed|contra|correction|actually|not true|wrong)\b", re.I)


def is_err(text, flag):
    return bool(flag) or bool(re.search(r'"error"|revert|failed|refused|validation failed', text or "", re.I))


def session_events(path):
    calls, out = {}, []
    for n, line in enumerate(open(path, encoding="utf-8", errors="replace"), 1):
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("role") == "assistant":
            if m.get("text"):
                out.append(("say", n, m["text"], None, None))
            for tc in m.get("tool_calls") or []:
                calls[tc.get("id")] = (tc.get("name", ""), json.dumps(tc.get("args", {}), sort_keys=True))
        elif m.get("role") == "tool_result":
            name, args = calls.get(m.get("tool_call_id"), ("?", "{}"))
            content = m.get("content") if isinstance(m.get("content"), str) else json.dumps(m.get("content"))
            out.append(("call", n, name, args, (content or "")[:600], is_err(content, m.get("is_error"))))
    return out


def mine(run_dir):
    run_dir = Path(run_dir)
    for sess in sorted((run_dir / "transcripts").glob("session-*.jsonl")):
        ev = session_events(sess)
        src = f"{run_dir.parent.name}/{run_dir.name}/{sess.name}"
        calls = [e for e in ev if e[0] == "call"]
        acts = [e for e in calls if ACT.match(e[2])]
        # repeated identical failing call
        streak = 1
        for a, b in zip(calls, calls[1:]):
            streak = streak + 1 if (a[2], a[3]) == (b[2], b[3]) and a[5] and b[5] else 1
            if streak == 2:
                yield dict(cat="repeat_failed", src=src, line=b[1], tool=b[2], args=b[3], result=b[4])
        for e in calls:
            if e[5] and STATE.search(e[4]):
                yield dict(cat="levelup" if LEVEL.search(e[2]) else "stale_state", src=src, line=e[1], tool=e[2], args=e[3], result=e[4])
            elif e[5] and ONBOARD.search(e[4]):
                yield dict(cat="onboarding", src=src, line=e[1], tool=e[2], args=e[3], result=e[4])
            if e[2].startswith("workspace_write") and BELIEF.search(e[3]):
                yield dict(cat="belief", src=src, line=e[1], tool=e[2], args=e[3][:600], result="")
        for e in ev:
            if e[0] == "say" and ASK_USER.search(e[2]):
                yield dict(cat="stalling_ask_user", src=src, line=e[1], text=e[2][:600])
            if e[0] == "say" and DANGER.search(e[2]):
                later = [c for c in calls if c[1] > e[1] and ACT.match(c[2])][:3]
                if any(c[2].startswith("harvest_start") or "start" in c[2] for c in later):
                    yield dict(cat="knowing_doing", src=src, line=e[1], text=e[2][:600], next=[c[2] for c in later])
        if calls and not acts:
            yield dict(cat="stalling_no_act", src=src, line=0, reads=len(calls))


if __name__ == "__main__":
    out, dirs = sys.argv[1], sys.argv[2:]
    rows = [r for d in dirs for r in mine(d)]
    with open(out, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(len(rows), "candidates")
    for (cat, run), n in sorted(Counter((r["cat"], r["src"].rsplit("/", 1)[0]) for r in rows).items()):
        print(f"{cat:<18} {run:<60} {n}")
