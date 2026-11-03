"""spec/perturb.py: ids, times, dates and blocks all move; verdicts do not (H review M2)."""
import random

from kami_drills.spec import perturb


def spec(text):
    return {"id": "x", "state": {"account": {"index": 3436}, "kamis": [{"index": 1234, "state": "RESTING", "hp": 5, "hp_max": 9}]},
            "fork": {"clock": {"unix": 1792000000.5, "iso": "2026-10-19T12:00:00+00:00", "block": 34858843}},
            "situation": text}


def test_ids_are_renamed_at_every_boundary():
    text = "kami 1234. Kami 1234, (1234) 1234; lens: {\"index\": 1234} account 3436. hp 1234.5 x12345 1234x"
    v, ch = perturb.perturb(spec(text), random.Random(1))
    new = str(ch["ids"][1234])
    assert v["situation"].count(new) == 5 and "1234." not in v["situation"].replace("1234.5", "")
    assert "1234.5" in v["situation"] and "x12345" in v["situation"] and "1234x" in v["situation"]  # not the id
    assert str(ch["ids"][3436]) in v["situation"] and perturb.survivors(v, ch) == []


def test_times_dates_and_blocks_shift_together():
    text = "at 2026-10-19T12:00:00Z on 2026-10-19, unix 1792000000, block 34858843. later block 34858900"
    v, ch = perturb.perturb(spec(text), random.Random(2))
    s, sh, bs = v["situation"], ch["clock_shift_s"], ch["block_shift"]
    assert "2026-10-19" not in s and str(1792000000 + sh) in s and v["fork"]["clock"]["unix"] == 1792000000.5 + sh
    assert str(34858843 + bs) in s and str(34858900 + bs) in s and v["fork"]["clock"]["block"] == 34858843 + bs


def test_survivors_reports_an_unrenamed_id():
    v, ch = perturb.perturb(spec("kami 1234"), random.Random(3))
    v["situation"] += " and again kami 1234."
    assert perturb.survivors(v, ch) == [1234]
