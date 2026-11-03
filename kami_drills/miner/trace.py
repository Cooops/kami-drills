"""Load one Season 1 run directory and rebuild the game state at any line of a session.

Everything read here is untrusted data from agent runs: it is parsed as JSON only, never executed or followed.
A session is its transcript (assistant/tool_result lines), the telemetry rows that time-stamp each tool result (same
order, one per result), the session-start snapshot (full state the agent never saw) and the scheduler rows.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# calls that change chain state when they succeed (dry_run=True calls are reads)
WRITES = {
    "harvest_start", "harvest_stop", "harvest_collect", "feed_kami", "revive_kami", "level_up_kami", "level_to",
    "level_and_allocate_batch", "feed_level_allocate_batch", "upgrade_skill", "allocate_skills", "use_item_batch",
    "use_account_item", "move_to_room", "travel_to_room", "listing_buy", "auction_buy", "pool_swap", "burn_items",
    "portal_deposit", "portal_withdraw", "portal_claim", "craft_item", "accept_quest", "complete_quest", "drop_quest",
    "scavenge_claim_and_reveal", "scavenge_claim", "droptable_reveal", "create_trade", "take_trade", "complete_trade",
    "complete_all_trades", "cancel_trade", "liquidate_kami", "act_sequence", "buy_kami", "name_kami", "equip_item",
    "unequip_item", "equip_all_batch", "unequip_all_batch", "newbie_vendor_buy", "cast_item", "fund_operator",
    "withdraw_operator", "bridge_eth_from_mainnet", "register_account", "create_operator_wallet",
}
KAMI_WRITES = {"harvest_start", "harvest_stop", "harvest_collect", "feed_kami", "revive_kami", "level_up_kami", "level_to",
               "level_and_allocate_batch", "feed_level_allocate_batch", "upgrade_skill", "allocate_skills",
               "use_item_batch", "liquidate_kami", "act_sequence", "name_kami", "equip_item", "unequip_item",
               "equip_all_batch", "unequip_all_batch", "cast_item"}
# which kami fields a successful call may change (empty tuple = all of them)
_HARV = ("hp", "hp_rate", "cooldown_until", "state", "node", "levelup_ready")
_LEVEL = ("level", "skill_points", "levelup_ready", "hp", "hp_total", "hp_rate")
KAMI_FIELDS_CHANGED = {
    "harvest_start": _HARV, "harvest_stop": _HARV, "harvest_collect": _HARV,
    "feed_kami": ("hp", "cooldown_until", "levelup_ready"),  # feeding moves HP, not the drain rate
    "use_item_batch": ("hp", "hp_rate", "cooldown_until", "levelup_ready", "state"),
    "revive_kami": ("hp", "hp_rate", "cooldown_until", "state"),
    "level_up_kami": _LEVEL, "level_to": _LEVEL, "level_and_allocate_batch": _LEVEL, "feed_level_allocate_batch": (),
    "upgrade_skill": ("skill_points", "hp_total", "hp_rate"), "allocate_skills": ("skill_points", "hp_total", "hp_rate"),
    "liquidate_kami": ("hp", "hp_rate", "cooldown_until"),
}
# errors that say nothing about the agent's decision (infrastructure, not game state)
TRANSIENT = re.compile(r"LENS_UNAVAILABLE|NOT_APPLIED|state not ready|timed? ?out|TimeExhausted|could not coalesce|"
                       r"bootstrap cache missing|socket error|rate limit|502|503|504", re.I)


# one catalog for the miner and the scorer: the kami-rules world (gdd items.csv; Djed Pillar and Pale Potion revive too)
from kami_drills.spec.rules import ITEMS, KAMI_FOODS  # noqa: E402
REVIVES = {i for i, v in ITEMS.items() if v["revives"]}


def semantic_fail(data):
    """A JSON result that says the call did not do what it asked (no is_error flag): a failed allocation, a travel
    that stopped short, a reverted receipt."""
    return isinstance(data, dict) and (data.get("success") is False or data.get("reached_target") is False
                                       or data.get("status") in ("reverted", "failed"))


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def jparse(text):
    if isinstance(text, (dict, list)):
        return text
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


@dataclass
class Call:
    line: int            # assistant line that issued it (the model acts in place of this line)
    result_line: int     # tool_result line
    tool: str
    args: dict
    ok: bool
    text: str
    data: object         # parsed result (None if not JSON)
    t: datetime          # telemetry time stamp of the result
    initiator: str = ""  # "scaffold" for the session-start briefing calls

    @property
    def write(self):
        return self.tool in WRITES and not self.args.get("dry_run")

    def kami_ids(self):
        a = self.args
        ids = list(a.get("kami_ids") or [])
        for k in ("kami_id", "killer_kami_id"):
            if a.get(k) is not None:
                ids.append(a[k])
        for t in a.get("targets") or []:
            if isinstance(t, dict) and t.get("kami_id") is not None:
                ids.append(t["kami_id"])
        for s in a.get("steps") or []:
            if isinstance(s, dict) and s.get("kami_id") is not None:
                ids.append(s["kami_id"])
        return [int(i) for i in ids if isinstance(i, int) or (isinstance(i, str) and i.isdigit())]


@dataclass
class Session:
    run: "Run"
    n: int
    path: Path
    calls: list
    n_lines: int
    snapshot: dict | None
    start: datetime | None
    end: datetime | None
    end_reason: str | None
    next_wake: datetime | None
    wake_source: str | None
    scaffold_end: int = 0   # last line of the scaffold briefing (a session-start fork keeps it in the prefix)
    misaligned: bool = False  # telemetry rows != tool results: time stamps cannot be trusted
    texts: list = field(default_factory=list)  # (line, assistant prose): the agent's stated intent, untrusted

    @property
    def src(self):
        return f"{self.run.name}/transcripts/{self.path.name}"

    def snap_calls(self, name=None):
        for c in (self.snapshot or {}).get("calls", []):
            if "response" in c and (name is None or c["name"] == name):
                yield c


@dataclass
class Run:
    name: str
    path: Path
    model: str
    pins: dict
    run_id: str
    sessions: list = field(default_factory=list)

    @property
    def model_short(self):
        return self.model.split("/")[-1]


def _config(path):
    """config.yaml holds a few flat keys and a `pins:` block; read those without a YAML dependency."""
    out, pins, in_pins = {}, {}, False
    for raw in path.read_text().splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        key, _, val = raw.strip().partition(":")
        if indent == 0:
            in_pins = key == "pins"
            if val.strip():
                out[key] = val.strip()
        elif in_pins and val.strip():
            pins[key] = val.strip()
    return out, pins


def load_session(run, path, tel_rows):
    n = int(path.stem.split("-")[1])
    pending, calls, n_lines, scaffold_end, in_brief, texts = {}, [], 0, 0, True, []
    tool_rows = [r for r in tel_rows if r["event"] == "tool_call"]
    k = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line_no, raw in enumerate(f, 1):
            n_lines = line_no
            m = jparse(raw)
            if not isinstance(m, dict):
                continue
            # the scaffold briefing: the user line, then scaffold-initiated calls and results, before the model acts
            in_brief = in_brief and (m.get("role") == "user" or m.get("initiator") == "scaffold")
            if in_brief:
                scaffold_end = line_no
            if m.get("role") == "assistant":
                if (m.get("text") or "").strip() and not m.get("initiator"):
                    texts.append((line_no, m["text"].strip()))
                for tc in m.get("tool_calls") or []:
                    pending[tc.get("id")] = (line_no, tc.get("name", ""), tc.get("args") or {}, m.get("initiator", ""))
            elif m.get("role") == "tool_result":
                line, tool, args, init = pending.get(m.get("tool_call_id"), (line_no, "?", {}, ""))
                text = m.get("content") if isinstance(m.get("content"), str) else json.dumps(m.get("content"))
                t = ts(tool_rows[k]["ts"]) if k < len(tool_rows) else None
                k += 1
                data = jparse(text) if not m.get("is_error") else None
                ok = not m.get("is_error") and not semantic_fail(data)
                calls.append(Call(line, line_no, tool, args if isinstance(args, dict) else {}, ok, text or "",
                                  data, t, init or m.get("initiator", "")))
    snap_path = run.path / "snapshots" / f"session-{n:04d}.json"
    snapshot = jparse(snap_path.read_text()) if snap_path.exists() else None
    ends = [r for r in tel_rows if r["event"] == "session_end"]
    starts = [r for r in tel_rows if r["event"] == "session_start"]
    sched = [r for r in tel_rows if r["event"] == "schedule_next"]
    start = ts((snapshot or {}).get("started_at")) or (ts(starts[0]["ts"]) if starts else None)
    return Session(run, n, path, calls, n_lines, snapshot, start, ts(ends[-1]["ts"]) if ends else None,
                   ends[-1].get("reason") if ends else None, ts(sched[-1].get("next_wake_at")) if sched else None,
                   sched[-1].get("source") if sched else None, scaffold_end, misaligned=k != len(tool_rows),
                   texts=texts)


def load_run(path):
    path = Path(path)
    cfg, pins = _config(path / "config.yaml") if (path / "config.yaml").exists() else ({}, {})
    run = Run(path.name, path, cfg.get("model", "unknown"), pins, cfg.get("run_id", path.name))
    tel = {}
    if (path / "telemetry.jsonl").exists():
        for raw in open(path / "telemetry.jsonl", encoding="utf-8", errors="replace"):
            r = jparse(raw)
            if isinstance(r, dict) and "session" in r:
                tel.setdefault(r["session"], []).append(r)
    for p in sorted((path / "transcripts").glob("session-*.jsonl")):
        run.sessions.append(load_session(run, p, tel.get(int(p.stem.split("-")[1]), [])))
    return run


# ---------------------------------------------------------------- state at a line

@dataclass
class Obs:
    value: object
    src: str              # "snapshot:lens_party" or "transcript:L23:lens_kami"
    t: datetime | None
    stale: bool = False   # a later state-changing call may have changed it, with no read since

    def dep(self, name):
        return {"field": name, "value": self.value, "source": self.src, "as_of": self.t.isoformat() if self.t else None}


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


class State:
    """Game state the agent could have known at a line: the session-start snapshot, then every tool result in the
    prefix. A state-changing call marks what it may have changed as stale until a read refreshes it; known effects
    (state transitions, cooldowns the tx returned, the room after a move) are applied with the call as source."""

    def __init__(self):
        self.kami, self.account, self.inv, self.quest = {}, {}, {}, {}
        self.item_type = {}
        self.writes = []      # (call) state-changing successes so far

    def _set(self, d, key, value, src, t):
        d[key] = Obs(value, src, t)

    def kami_obs(self, k, f):
        return self.kami.get(k, {}).get(f)

    def fresh(self, obs):
        return obs is not None and not obs.stale

    # -- reads
    def read_kami(self, k, src, t):
        idx = k.get("index")
        if idx is None:
            return
        d = self.kami.setdefault(int(idx), {})
        if "state" in k:
            self._set(d, "state", k["state"], src, t)
        hp = k.get("hp")
        if isinstance(hp, dict):
            self._set(d, "hp", hp.get("current"), src, t)
            self._set(d, "hp_total", hp.get("total"), src, t)
        elif isinstance(hp, list) and len(hp) == 2:
            self._set(d, "hp", hp[0], src, t)
            self._set(d, "hp_total", hp[1], src, t)
        if "hpRatePerHr" in k:
            self._set(d, "hp_rate", _num(k["hpRatePerHr"]), src, t)
        for f, name in (("cooldownUntil", "cooldown_until"), ("skillPoints", "skill_points"),
                        ("levelUpReady", "levelup_ready"), ("level", "level")):
            if f in k:
                self._set(d, name, k[f], src, t)
        if isinstance(k.get("musu"), dict) and isinstance(k["musu"].get("accrued"), (int, float)):
            self._set(d, "accrued", k["musu"]["accrued"], src, t)
        if isinstance(k.get("node"), dict):
            self._set(d, "node", k["node"].get("index"), src, t)
        elif "state" in k and k["state"] != "HARVESTING" and "hp" in k and isinstance(k["hp"], dict):
            self._set(d, "node", None, src, t)

    def read(self, tool, args, data, src, t):
        if not isinstance(data, dict):
            return
        body = data.get("data", data)
        if tool in ("lens_party", "lens_roster", "lens_account") and isinstance(body, dict):
            for k in body.get("kamis") or []:
                if isinstance(k, dict) and (tool != "lens_account" or "hp" in k):
                    self.read_kami(k, src, t)
                elif isinstance(k, dict) and k.get("index") is not None and "state" in k:
                    self._set(self.kami.setdefault(int(k["index"]), {}), "state", k["state"], src, t)
            acc = body.get("account") if tool == "lens_roster" else body
            if isinstance(acc, dict) and "roomIndex" in acc:
                self._set(self.account, "room", acc["roomIndex"], src, t)
            if tool == "lens_account" and "musu" in body:
                self._set(self.account, "musu", body["musu"], src, t)
        elif tool == "lens_kami" and isinstance(body, dict) and "index" in body:
            self.read_kami(body, src, t)
        elif tool == "lens_inventory" and isinstance(body, dict) and "items" in body:
            self.inv = {}
            for it in body["items"]:
                item = it.get("item") or {}
                if item.get("index") is not None:
                    self.inv[item["index"]] = Obs(it.get("balance", 0), src, t)
                    self.item_type[item["index"]] = (item.get("type"), item.get("name"))
            self.inv["__complete__"] = Obs(True, src, t)  # absent items are known to be 0
            if 1 in self.inv:
                self._set(self.account, "musu", self.inv[1].value, src, t)
        elif tool == "check_quest_completable" and "quest_index" in data:
            self._set(self.quest.setdefault(data["quest_index"], {}), "completable", bool(data.get("completable")), src, t)
        elif tool == "quest_state" and "quest_index" in data:
            q = self.quest.setdefault(data["quest_index"], {})
            self._set(q, "complete", bool(data.get("completed")), src, t)
            if "completable_now" in data:
                self._set(q, "completable", bool(data["completable_now"]), src, t)
        elif tool == "lens_quests" and isinstance(body, dict):
            for q in body.get("accepted") or []:
                if isinstance(q, dict) and "index" in q:
                    self._set(self.quest.setdefault(q["index"], {}), "complete", bool(q.get("complete")), src, t)
            for q in body.get("quests") or []:
                if isinstance(q, dict) and "index" in q and q.get("accepted"):
                    self._set(self.quest.setdefault(q["index"], {}), "complete", bool(q.get("complete")), src, t)

    # -- writes
    def _stale(self, d, *fields):
        for f in fields or list(d):
            if f in d:
                d[f].stale = True

    def write(self, c, src):
        self.writes.append(c)
        tool, a, data, t = c.tool, c.args, c.data if isinstance(c.data, dict) else {}, c.t
        kamis = c.kami_ids()
        for k in kamis:
            self._stale(self.kami.setdefault(k, {}), *KAMI_FIELDS_CHANGED.get(tool, ()))
        if tool == "harvest_start":
            for k in kamis:
                self._set(self.kami[k], "state", "HARVESTING", src, t)
                self._set(self.kami[k], "node", a.get("node_index"), src, t)
        elif tool == "harvest_stop":
            for k in kamis:
                self._set(self.kami[k], "state", "RESTING", src, t)
                self._set(self.kami[k], "node", None, src, t)
        elif tool == "revive_kami":
            for k in kamis:
                self._set(self.kami[k], "state", "RESTING", src, t)
        elif tool in ("level_up_kami", "level_to") and kamis and self.kami[kamis[0]].get("level") is not None:
            lv = self.kami[kamis[0]]["level"].value
            new = a.get("target_level") if tool == "level_to" else (lv + 1 if isinstance(lv, int) else None)
            if isinstance(new, int):
                self._set(self.kami[kamis[0]], "level", new, src, t)
        elif tool == "feed_kami" and isinstance(data.get("hp"), dict) and kamis:
            self._set(self.kami[kamis[0]], "hp", data["hp"].get("after"), src, t)
        for cd in data.get("cooldowns") or []:
            if isinstance(cd, dict) and cd.get("kami_id") is not None:
                self._set(self.kami.setdefault(int(cd["kami_id"]), {}), "cooldown_until", cd.get("cooldown_until"), src, t)
        if tool in ("harvest_collect", "harvest_stop", "harvest_start"):  # the bounty is paid out (or starts at 0)
            for k in kamis:
                self._set(self.kami[k], "accrued", 0, src, t)
        if tool == "harvest_collect":  # collect leaves state and node as they were
            for k in kamis:
                for f in ("state", "node"):
                    if f in self.kami[k]:
                        self.kami[k][f].stale = False
        if tool in ("move_to_room", "travel_to_room"):
            room = a.get("room_index", a.get("target_room"))
            if tool == "travel_to_room" and a.get("allow_partial"):
                self._stale(self.account, "room")
            else:
                self._set(self.account, "room", room, src, t)
        if tool not in ("move_to_room", "travel_to_room", "harvest_start", "harvest_stop", "level_up_kami",
                        "upgrade_skill", "allocate_skills", "level_to", "name_kami", "accept_quest"):
            for o in self.inv.values():
                o.stale = True
            self._stale(self.account, "musu")
            for q in self.quest.values():
                self._stale(q, "completable")
        if tool == "pool_swap" and "musu" in self.account and isinstance(self.account["musu"].value, (int, float)):
            # a swap's own MUSU side is known: spent amount_in, or received (the receipt's inventory_out)
            m = self.account["musu"].value
            if a.get("item_in") == 1 and isinstance(a.get("amount_in"), int):
                self._set(self.account, "musu", m - a["amount_in"], src, t)
            elif a.get("item_out") == 1 and isinstance((data.get("inventory_out") or {}).get("after"), int):
                self._set(self.account, "musu", data["inventory_out"]["after"], src, t)
        paid = sum(x.get("amount") or 0 for x in data.get("payouts") or [] if isinstance(x, dict) and x.get("item") == 1)
        if paid and "musu" in self.account and isinstance(self.account["musu"].value, (int, float)):
            self._set(self.account, "musu", self.account["musu"].value + paid, src, t)  # a collect/stop/claim payout (rc6 F3)
        if tool == "complete_quest" and a.get("quest_index") is not None:
            q = self.quest.setdefault(a["quest_index"], {})
            self._set(q, "complete", True, src, t)
            self._set(q, "completable", False, src, t)
        if tool == "accept_quest" and a.get("quest_index") is not None:
            self._set(self.quest.setdefault(a["quest_index"], {}), "complete", False, src, t)

    # -- derived
    def hp_at(self, k, when):
        """Projected HP at `when` from the latest fresh HP and rate; None if either is unknown or stale."""
        hp, rate, total = (self.kami_obs(k, f) for f in ("hp", "hp_rate", "hp_total"))
        if not (self.fresh(hp) and self.fresh(rate)) or hp.value is None or rate.value is None or hp.t is None:
            return None
        v = hp.value + rate.value * (when - max(hp.t, rate.t)).total_seconds() / 3600
        cap = total.value if total is not None and total.value else float("inf")
        return max(0.0, min(cap, v))

    def held(self, kinds):
        """Held items of the given catalog set, if the inventory is fresh: {item_index: balance}; None if unknown."""
        if not self.fresh(self.inv.get("__complete__")):
            return None
        return {i: o.value for i, o in self.inv.items() if i in kinds and not o.stale and o.value}

    def foods(self):
        """Kami foods held (catalog For=Kami; account stamina foods such as 21201-21205 excluded)."""
        return self.held(KAMI_FOODS)


def state_at(session, line):
    """State known after transcript line `line` (the prefix 1..line), starting from the session snapshot."""
    st = State()
    if session.snapshot:
        t0 = session.start
        for c in session.snap_calls():
            st.read(c["name"], c.get("args") or {}, c["response"], f"snapshot:{c['name']}", t0)
    for c in session.calls:
        if c.result_line > line:
            break
        src = f"transcript:L{c.result_line}:{c.tool}"
        if not c.ok:
            continue
        if c.write:
            st.write(c, src)
        else:
            st.read(c.tool, c.args, c.data, src, c.t)
    return st


def time_at(session, line):
    """Wall time at the end of prefix 1..line: the last tool result's time stamp, else the session start."""
    t = session.start
    for c in session.calls:
        if c.result_line > line:
            break
        t = c.t or t
    return t


def block_at(session, line):
    """Latest chain block stated in the prefix (tx receipts' `block`, lens reads' meta.blockNumber), else the snapshot's."""
    best = {"block": (session.snapshot or {}).get("block"), "source": "snapshot"}
    for c in session.calls:
        if c.result_line > line:
            break
        d = c.data if isinstance(c.data, dict) else {}
        b = d.get("block") if isinstance(d.get("block"), int) else (d.get("meta") or {}).get("blockNumber")
        if isinstance(b, int):
            best = {"block": b, "source": f"transcript:L{c.result_line}:{c.tool}"}
    return best
