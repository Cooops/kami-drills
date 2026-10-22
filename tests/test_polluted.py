"""Polluted-context variants: deterministic, verbatim errors, v1 suite untouched. Run: uv run pytest -q tests/test_polluted.py"""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from types import SimpleNamespace

from inspect_ai.model import ChatMessageAssistant, ChatMessageTool, ChatMessageUser
from inspect_ai.scorer import CORRECT, INCORRECT, Score
from inspect_ai.tool import ToolCall

from kami_drills import task as kd
from kami_drills.polluted import eligible_error, own_error_variants, polluted_delta, polluted_v2

ROOT = Path(__file__).resolve().parent.parent
V1_SUITE_SHA256 = "cd9a2fe576bd08f8d460a419069ee87fa497a196be1f426d29696d8d203bafa9"  # file names + bytes of drills/*.json
spec = importlib.util.spec_from_file_location("make_polluted_variants", ROOT / "scripts" / "make_polluted_variants.py")
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)


def test_deterministic_and_committed():
    first = gen.build()
    assert first == gen.build()
    assert 10 <= len(first) <= 15
    assert {p.name: p.read_text() for p in gen.OUT.glob("*.json")} == first  # rerun the script if this fails


def test_errors_are_verbatim_v1_data_from_the_same_run():
    v1 = gen.load_v1()
    for text in gen.build().values():
        v = json.loads(text)
        meta, src = v["polluted"], v1[v["polluted"]["source_drill"]]
        assert meta["family"] == "static_robustness" and "same source run" in meta["label"]
        assert meta["injected"] and meta["variant_hash"].startswith("sha256:")
        for inj in meta["injected"]:
            assert inj["tool"] not in kd.OUTSOURCE and eligible_error(inj["tool"], inj["error"])  # no hidden tools, no infra
            donor = v1[inj["origin"]["drill"]]
            assert gen.run_of(donor) == gen.run_of(src) and donor["src"] == inj["origin"]["session_file"]
            call_msg = next(m for m in v["prefix"] if m["role"] == "assistant"
                            and any(c["name"] == inj["tool"] and c["args"] == inj["args"] for c in m.get("tool_calls") or []))
            res = next(m for m in donor["prefix"] if m["role"] == "tool_result" and m["content"] == inj["error"] and m.get("is_error"))
            assert call_msg["tool_calls"][0] in [c for m in donor["prefix"] for c in m.get("tool_calls") or []]
            assert res in v["prefix"]
        n = meta["inserted_messages"]
        assert v["prefix"][:1] + v["prefix"][1 + n:] == src["prefix"]  # only insertion; clean prefix intact
        assert {k: x for k, x in v.items() if k not in ("id", "prefix", "keep_lines", "polluted")} == \
               {k: x for k, x in src.items() if k not in ("id", "prefix", "keep_lines")}  # same rule, replay, oracle


def test_v1_suite_unchanged_and_loader_ignores_variants():
    h = hashlib.sha256()
    for p in sorted((ROOT / "drills").glob("*.json")):
        h.update(f"drills/{p.name}".encode() + p.read_bytes())
    assert h.hexdigest() == V1_SUITE_SHA256
    from kami_drills import task
    ids = [json.loads(p.read_text())["id"] for p in task.DRILLS.glob("*.json") if not p.name.startswith("_")]
    assert len(ids) == 47 and not any("polluted" in i for i in ids)
    assert not list((ROOT / "drills").glob("**/*polluted*"))


def test_polluted_delta():
    clean = {"a": [True, True], "b": [True, False], "c": [False, False]}
    polluted = {"a": [True, False], "b": [False, False], "c": [False, False], "x": [True]}  # x has no clean twin
    assert polluted_delta(clean, polluted) == pytest.approx(-1 / 3)  # (-0.5 - 0.5 + 0) / 3
    assert polluted_delta(clean, clean) == 0
    with pytest.raises(ValueError):
        polluted_delta(clean, {"x": [True]})


def test_eligible_error():
    assert not eligible_error("get_strategy_status", "Error executing tool get_strategy_status: not found")  # hidden
    assert not eligible_error("harvest_stop", "{'message': 'failed to insert tx into auction index: pool reached max tx capacity'}")
    assert eligible_error("feed_kami", "Error executing tool feed_kami: ... revert: kami too far: Reverted")


def test_replay_gap_is_not_an_own_error():
    gap = "Error executing tool lens_kami: not available in this drill (no recorded response)."
    assert kd.is_error_text(gap)  # v1 counts it as a failed call ...
    assert not eligible_error("lens_kami", gap)  # ... but it is the drill's missing recording, not the model's error
    for text in ("Off the recorded path.", "Recorded path ended.", "Episode over.", "Budget not used up (3 calls left). Keep working."):
        assert not eligible_error("feed_kami", text)


def test_own_error_variants_from_synthetic_log():
    drill_id = "risk-lowhp-feed-first"
    drill = gen.load_v1()[drill_id]
    bad = "Error executing tool feed_kami: validation failed; no transaction sent: ... revert: kami too far: Reverted"
    msgs = [ChatMessageUser(content="start")] + [
        ChatMessageAssistant(content="", tool_calls=[ToolCall(id="c1", function="feed_kami", arguments={"kami_id": 1}),
                                                     ToolCall(id="c2", function="get_strategy_status", arguments={}),
                                                     ToolCall(id="c3", function="lens_party", arguments={})]),
        ChatMessageTool(content=bad, tool_call_id="c1", function="feed_kami"),
        ChatMessageTool(content="Error executing tool get_strategy_status: nope", tool_call_id="c2", function="get_strategy_status"),
        ChatMessageTool(content='{"ok": 1}', tool_call_id="c3", function="lens_party")]
    log = SimpleNamespace(samples=[SimpleNamespace(id=drill_id, messages=msgs, metadata={"fork_len": 1}),
                                   SimpleNamespace(id="not-a-drill", messages=msgs, metadata={"fork_len": 1}),
                                   SimpleNamespace(id="risk-unmanaged-lowhp", messages=msgs[:1], metadata={"fork_len": 1})])
    out = own_error_variants(log)
    # the failing drill has no donor but itself, so it gets no variant; the failure-free drill gets it as a donor error
    assert list(out) == ["risk-unmanaged-lowhp--own-error"]  # unknown samples are left out
    drill = gen.load_v1()["risk-unmanaged-lowhp"]
    v = out["risk-unmanaged-lowhp--own-error"]
    assert [i["tool"] for i in v["polluted"]["injected"]] == ["feed_kami"]  # hidden tool and the successful read dropped
    assert v["polluted"]["inserted_messages"] == 2 and v["polluted"]["family"] == "own_error"
    assert v["polluted"]["injected_count"] == 1 and v["polluted"]["model_eligible_failures"] == 1 and v["polluted"]["model"] is None
    assert v["prefix"][0] == drill["prefix"][0] and v["prefix"][3:] == drill["prefix"][1:]
    assert v["prefix"][1]["tool_calls"][0] == {"id": "own_0", "name": "feed_kami", "args": {"kami_id": 1}}
    assert v["prefix"][2]["content"] == bad and v["prefix"][2]["tool_call_id"] == "own_0"
    assert {k: x for k, x in v.items() if k not in ("id", "prefix", "keep_lines", "polluted")} == \
           {k: x for k, x in drill.items() if k not in ("id", "prefix", "keep_lines")}
    assert own_error_variants(log) == out  # deterministic
    assert len(polluted_v2(specs=out.values()).dataset) == 1


def test_own_error_variants_use_other_drills_and_ignore_outcome():
    drills = gen.load_v1()
    ids = ["risk-lowhp-feed-first", "risk-unmanaged-lowhp", "stale-notes-wrong-node-stop"]
    def sample(did, tool, kami, passed):  # the model's failed call carries the drill id in its text, so origin is traceable
        tc = ToolCall(id="c1", function=tool, arguments={"kami_id": kami})
        err = f"Error executing tool {tool}: validation failed; no transaction sent: ... revert: from {did}: Reverted"
        msgs = [ChatMessageUser(content="start"), ChatMessageAssistant(content="", tool_calls=[tc]),
                ChatMessageTool(content=err, tool_call_id="c1", function=tool)]
        # the outcome lives where an Inspect log keeps it: the drill_rule score (C/I) and the sample's metadata
        return SimpleNamespace(id=did, messages=msgs, metadata={"fork_len": 1, "passed": passed},
                               scores={"drill_rule": Score(value=CORRECT if passed else INCORRECT)})
    log = lambda flags: SimpleNamespace(samples=[sample(d, "feed_kami", 100 + i, f) for i, (d, f) in enumerate(zip(ids, flags))])
    out = own_error_variants(log([True, True, True]), max_errors=1)
    assert set(out) == {i + "--own-error" for i in ids}  # every drill gets a variant, not just failed ones
    for did in ids:
        for inj in out[did + "--own-error"]["polluted"]["injected"]:
            assert f"from {did}:" not in inj["error"]  # never the scored drill's own failure
    assert own_error_variants(log([False, False, False]), max_errors=1) == out  # pass/fail of the log changes nothing
    assert own_error_variants(log([True, False, True]), max_errors=1) == out
    # a donor call that the scored drill's rule forbids is skipped
    rule = drills["risk-lowhp-feed-first"]["rule"]
    forbidden = [p for p in rule.get("forbid", []) if p["tool"] == "feed_kami"]
    for inj in out["risk-lowhp-feed-first--own-error"]["polluted"]["injected"]:
        assert not any(kd.matches({"tool": inj["tool"], "args": inj["args"], "ok": False}, p) for p in forbidden)
