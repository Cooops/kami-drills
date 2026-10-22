"""End to end through Inspect with the built-in mock model: no network, no API keys, no cost.
mockllm/model replays `custom_outputs` in order, one per model call. Each play below is a list of ModelOutputs: tool
calls for the scripted turns, then a plain-text turn that ends the sample."""
import json

import pytest
from inspect_ai import eval as inspect_eval
from inspect_ai.model import ModelOutput, get_model

from kami_drills import memory as mem
from kami_drills.cadence import drill_oracle_minutes, load_schedule_drills, schedule_v2
from kami_drills.polluted import polluted_v2

M = "mockllm/model"


def play(*calls):
    return [*(ModelOutput.for_tool_call(M, fn, args) for fn, args in calls), ModelOutput.from_content(M, "done")]


def run(task, sample_id, outputs, tmp_path):
    [log] = inspect_eval(task, model=get_model(M, custom_outputs=outputs), sample_id=sample_id, log_dir=str(tmp_path),
                         display="none", epochs=1)
    assert log.status == "success" and len(log.samples) == 1 and not log.samples[0].error, log.error
    return next(iter(log.samples[0].scores.values())).value == "C"


def spec_of(kind):  # dr-cooldown-feed: its replay reproduces the recorded revert, so repeating it is scored as a repeat
    return next(s for s in mem.load() if s["kind"] == kind and (kind != "dont_repeat" or s["id"] == "dr-cooldown-feed"))


@pytest.mark.parametrize("kind", ["stale_note", "notes_first", "dont_repeat"])
def test_memory(kind, tmp_path):
    spec = spec_of(kind)
    arm = mem.ARMS[kind][0]
    m = mem.materialise(spec, arm)
    sid, oracle = m["id"], [tuple(c) for c in m["oracle"]]
    if kind == "notes_first":
        oracle = [("workspace_read", {"path": mem.NOTE_PATH}), *oracle]
    assert run(mem.memory_v2(), sid, play(*oracle), tmp_path)
    if kind == "dont_repeat":  # doing nothing never repeats the failure, so the failing play is the one that must lose
        call = mem.v1(spec["source_drill"])["prefix"][spec["base_len"]]["tool_calls"][0]
        assert not run(mem.memory_v2(), sid, play((call["name"], call["args"])), tmp_path)
    else:
        assert not run(mem.memory_v2(), sid, play(), tmp_path)


def test_schedule(tmp_path):
    for d in load_schedule_drills():
        assert run(schedule_v2(), d["id"], play(("set_next_wake", {"minutes_from_now": drill_oracle_minutes(d)})), tmp_path)
        assert not run(schedule_v2(), d["id"], play(), tmp_path)  # never schedules a wake
        assert not run(schedule_v2(), d["id"], play(("set_next_wake", {"minutes_from_now": 1})), tmp_path)  # far too early


def test_polluted(tmp_path):
    ids = [s.id for s in polluted_v2().dataset]
    assert len(ids) == 12
    for sid in ids[:3]:  # a few samples keep it fast; run `-k polluted` with ids unsliced to cover all 12
        spec = next(s.metadata["spec"] for s in polluted_v2().dataset if s.id == sid)
        assert run(polluted_v2(), sid, play(*map(tuple, spec["oracle"])), tmp_path)
        assert not run(polluted_v2(), sid, play(), tmp_path)
