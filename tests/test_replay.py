"""Replay equivalence checks: uv run python tests/test_replay.py"""
from kami_drills import task as kd

own = {'{"account_index": 3407, "full": false}': "A"}
assert kd.same_read("lens_quests", {"account_index": 3407, "full": False}, {"account_index": -1}, own)
assert not kd.same_read("lens_quests", {"account_index": 3407, "full": False}, {"account_index": 3407, "full": True}, own)
assert kd.same_read("lens_quests", {"account_index": 3377}, {"account_index": 3377, "full": False}, {'{"account_index": 3377}': "A"})
assert not kd.same_read("lens_roster", {"account_index": 1}, {}, {'{"account_index": 1}': "A", '{"account_index": 2}': "B"})
assert not kd.same_read("quest_state", {"quest_index": 1}, {"quest_index": 3}, {'{"quest_index": 1}': "A"})
assert kd.sequence_step({"op": "feed", "kami_id": 1, "item_id": 2}) == ("feed_kami", {"kami_id": 1, "food_item_id": 2})
print("ok")
