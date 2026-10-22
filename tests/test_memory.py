"""v2 memory family checks: uv run --with pytest pytest -q   (or `kami-drills check`, which runs this file as a script)"""
import datetime as dt
import hashlib
import json
import re

from kami_drills import memory as mem
from kami_drills import task as kd


def call(tool, text="{}", **args):
    return {"tool": tool, "args": args, "text": text}


COOLDOWN = ("Error executing tool feed_kami: validation failed; no transaction sent: transaction dry-run reverted: "
            "{'code': -32000, 'message': 'revert: kami on cooldown: Reverted'}")
FAIL = {"tool": "feed_kami", "error_class": "kami on cooldown"}


def test_schema_and_counts():
    specs = mem.load()
    assert all(mem.validate(s) == [] for s in specs), [(s["id"], mem.validate(s)) for s in specs]
    by = {k: [s for s in specs if s["kind"] == k] for k in mem.KINDS}
    assert [len(by[k]) for k in ("dont_repeat", "stale_note", "notes_first")] == [4, 4, 3]  # 11 drills; docs say so
    assert 3 <= len(by["dont_repeat"]) <= 5 and 3 <= len(by["stale_note"]) <= 4 and 2 <= len(by["notes_first"]) <= 3
    assert len({s["id"] for s in specs}) == len(specs)
    assert mem.validate({"kind": "dont_repeat", "id": "x", "format": "f"})  # missing keys are reported


def test_error_class():
    assert mem.error_class(COOLDOWN) == "kami on cooldown"
    assert mem.error_class(COOLDOWN.replace("kami on cooldown", "kami starving..")) == "kami starving"
    assert mem.error_class('{"status": "success"}') is None


def test_dont_repeat_scoring_and_arms():
    spec = next(s for s in mem.load() if s["id"] == "dr-cooldown-feed")
    rule, f = mem.v1(spec["source_drill"])["rule"], spec["failure"]
    wake = call("set_next_wake", minutes_from_now=30)
    assert not mem.score_dont_repeat([], f, rule)  # M1: doing nothing no longer passes
    assert not mem.score_dont_repeat([call("set_next_wake")], f, rule)  # nor does a bare wake with no useful delay
    assert not mem.score_dont_repeat([call("lens_party"), call("feed_kami", COOLDOWN, kami_id=20375)], f, rule)  # repeat
    assert not mem.score_dont_repeat([call("feed_kami", COOLDOWN, kami_id=20375), wake], f, rule)  # repeat first, then waits
    assert mem.score_dont_repeat([wake], f, rule)  # the required action, no repeat


def test_passive_policies():
    base = mem.null_baseline()
    assert len(base) == len(mem.PASSIVE_POLICIES) * sum(len(mem.ARMS[s["kind"]]) for s in mem.load())
    passed = {k for k, v in base.items() if v}
    # waiting is correct only for the cooldown drills (their source rule accepts a wake of >= 4-5 min); everything else
    # needs a real action, and a bare wake, end_session or no calls never passes
    # (sn-cooldown-over: the replay's wake row sets "cooled", but the note-vs-world answer there is "wait", so it too is waiting-correct)
    assert {k.split(":")[0] for k in passed} == {"dr-cooldown-feed", "dr-stop-cooldown", "sn-cooldown-over"}
    assert {k.rsplit("/", 1)[1] for k in passed} == {"wake-5", "wake-30", "wake-60", "read-then-wake"}


def test_notes_first_needs_action():
    spec = next(s for s in mem.load() if s["id"] == "nf-wait-then-start")
    rule = mem.v1(spec["source_drill"])["rule"]
    read, wake = call("workspace_read", path=mem.NOTE_PATH), call("set_next_wake", minutes_from_now=30)
    start = call("harvest_start", kami_ids=[9787], node_index=29)
    assert not mem.score_notes_first([read, wake], rule)  # the v1 rule alone would accept this
    assert mem.score_notes_first([read, call("wait", seconds=130), start], rule)


def test_arm_report():
    rows = [{"drill": d, "arm": a, "pass": p} for d, a, p in
            [("x", "a", 1), ("y", "a", 1), ("x", "b", 1), ("y", "b", 0), ("x", "c", 0), ("y", "c", 0)]]
    r = mem.arm_report(rows)
    assert r["pass"] == {"a": 1.0, "b": 0.5, "c": 0.0} and r["a-c"] == 1.0 and r["b-c"] == 0.5


def test_arms_share_base_state():
    spec = next(s for s in mem.load() if s["kind"] == "dont_repeat")
    a, b, c = (mem.materialise(spec, x) for x in "abc")
    n = spec["base_len"]
    assert a["prefix"][:n] == b["prefix"][:n] == c["prefix"][:n] and len(c["prefix"]) == n
    assert "Reverted" in json.dumps(a["prefix"][n:]) and "Reverted" not in json.dumps(b["prefix"][n:] + c["prefix"][n:])
    assert spec["note"] in json.dumps(b["prefix"][n:]) and spec["note"] not in json.dumps(a["prefix"] + c["prefix"])
    assert a["replay"] == b["replay"] == c["replay"]


def test_stale_note():
    s = next(s for s in mem.load() if s["id"] == "sn-quest10-open")
    rule = mem.v1(s["source_drill"])["rule"]
    assert not mem.score_stale_note([call("complete_quest", quest_index=10)], rule, ["q11"])  # followed the note
    assert mem.score_stale_note([call("accept_quest", quest_index=11)], rule, ["q11"])  # followed the world
    for sp in mem.load():
        if sp["kind"] != "stale_note":
            continue
        src = json.dumps(mem.v1(sp["source_drill"])["prefix"])
        newest = max(re.findall(r"20\d\d-\d\d-\d\dT\d\d:\d\d", json.dumps(mem.v1(sp["source_drill"])["prefix"][:sp["base_len"]])))
        stamp = re.search(r"20\d\d-\d\d-\d\dT\d\d:\d\d", sp["notes"]["dated_newer"]).group()
        one_day = (dt.datetime.fromisoformat(newest) + dt.timedelta(days=1)).isoformat(timespec="minutes")
        assert stamp == one_day and not re.search(r"20\d\d-\d\d-\d\d", sp["notes"]["plain"]), sp["id"]
        assert sp["notes"]["dated_newer"].endswith(sp["notes"]["plain"])  # variants differ only by the timestamp


def test_notes_first():
    spec = next(s for s in mem.load() if s["id"] == "nf-harvest-node")
    rule = mem.v1(spec["source_drill"])["rule"]
    read, go = call("workspace_read", path=mem.NOTE_PATH), call("travel_to_room", target_room=31)
    stop = call("harvest_stop", kami_ids=[17194])
    assert mem.score_notes_first([read, go, stop], rule, ["at31", "stopped"])
    assert not mem.score_notes_first([call("workspace_read", path="reference/catalogs/rooms.csv"), go, stop], rule, ["at31", "stopped"])
    assert not mem.score_notes_first([go, read, stop], rule, ["at31", "stopped"])  # acted before reading
    assert not mem.score_notes_first([read], rule) and not mem.score_notes_first([], rule)  # read alone does not use the fact
    m = mem.materialise(spec, "only")
    assert len(m["prefix"]) == 1 and m["replay"][0]["response"] == spec["note"] and m["replay"][0]["args"] == {"path": mem.NOTE_PATH}


def test_v1_untouched_by_v2():
    v1_ids = {json.loads(p.read_text())["id"] for p in mem.V1.glob("*.json") if not p.name.startswith("_")}
    assert len(v1_ids) == 47 and not v1_ids & {s["id"] for s in mem.load()}
    assert mem.V2.parent ==mem.ROOT / "drills_v2" and mem.V1 not in mem.V2.parents
    assert not list(mem.V1.rglob("drills_v2*")) and not any("memory" == p.parent.name for p in mem.V1.rglob("*.json"))
    # judge hash stamped in every v1 drill still matches task.py, so the suite hash is unchanged
    h = "sha256:" + hashlib.sha256((mem.ROOT / "kami_drills" / "task.py").read_bytes()).hexdigest()
    assert {json.loads(p.read_text())["provenance"]["judge_hash"]["kami_drills/task.py"]
            for p in mem.V1.glob("*.json") if not p.name.startswith("_")} == {h}


def test_samples_build():  # builds Inspect samples through v1's own message and replay code; no model call
    for spec in mem.load():
        for arm in mem.ARMS[spec["kind"]]:
            msgs = kd.to_messages(mem.materialise(spec, arm)["prefix"])
            assert msgs[0].role == "system" and len(msgs) > 1


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
