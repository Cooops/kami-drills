"""Polluted-context variants: deterministic, verbatim errors, v1 suite untouched. Run: uv run pytest -q tests/test_polluted.py"""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from kami_drills.polluted import polluted_delta

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
        assert meta["injected"] and meta["variant_hash"].startswith("sha256:")
        for inj in meta["injected"]:
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
