"""kami-drills v2 drill spec: load, validate, simulate, score (docs/v2-spec.md).

A spec is harness-agnostic: game state at the fork, response rows over abstract intents, and a scoring rule over the
intent trace and the simulated end state. Adapters (kami_drills/adapters/) render it into one harness's tool surface
and map tool calls back to intents. Nothing here is imported by task.py, so the v1 suite and its judge hash are unchanged.
"""
import copy
import hashlib
import json
import re
from pathlib import Path

import jsonschema

from kami_drills import task as kd

HERE = Path(__file__).parent
SCHEMA = json.loads((HERE / "drill-spec.schema.json").read_text())
VERBS = tuple(SCHEMA["$defs"]["intent"]["properties"]["verb"]["enum"])  # the closed intent vocabulary
REASONS = tuple(SCHEMA["$defs"]["reason_code"]["enum"])
TIME_VERBS = {"wait", "schedule_wake"}
SESSION_END = {"schedule_wake", "end_session"}
# not state changes: perception, time, session, memory; needs_action and the battery use this
PASSIVE = {"read", "quote", "wait", "schedule_wake", "end_session", "note_read", "note_write", "unknown"}
NATIVE_ANY = "*"  # result.native key: recorded text any adapter serves verbatim unless the drill is harness-bound
SCORERS = ("rule", "partial_credit")  # shortcut: schedule_loss/horizon/budget_pair/ev_regret are not implemented yet; check reports them
_VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)


def _spec_files(p):
    """Spec files under a path: a bank root follows its manifest's public[].file; a suite directory its *.json (not
    MANIFEST.json); a directory with neither is searched recursively (private/ directories are never read)."""
    if not p.is_dir():
        return [p]
    man = p / "manifest.json"
    if man.exists() and isinstance((m := json.loads(man.read_text())), dict) and "public" in m:
        return [p / e["file"] for e in m["public"]]
    files = sorted(f for f in p.glob("*.json") if f.name.lower() != "manifest.json")
    if not files:
        files = sorted(f for f in p.rglob("*.json") if f.name.lower() != "manifest.json" and "private" not in f.relative_to(p).parts)
    return files


def load(paths):
    """Specs from files or directories (see _spec_files); a file may hold one spec or a list. Raises ValueError when a
    path yields no spec, so a mistyped path can never check green."""
    out = []
    for p in map(Path, paths):
        got = []
        for f in _spec_files(p):
            d = json.loads(f.read_text())
            got += d if isinstance(d, list) else [d]
        if not got:
            raise ValueError(f"no specs found under {p}")
        out += got
    return out


def content_hash(spec):
    """sha256 of canonical JSON without content_hash, provenance.stamps and heldout.sealed_hash (schema definition)."""
    s = copy.deepcopy(spec)
    s.pop("content_hash", None)
    s.get("provenance", {}).pop("stamps", None)
    s.get("heldout", {}).pop("sealed_hash", None)
    blob = json.dumps(s, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def validate(spec):
    """Schema errors plus the checks the schema cannot express. [] means valid."""
    errs = [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in _VALIDATOR.iter_errors(spec)]
    if errs:
        return errs
    if spec.get("content_hash") and spec["content_hash"] != content_hash(spec):
        errs.append("content_hash does not match the content")
    sc = spec["scoring"]
    if sc["kind"] == "partial_credit" and sc["null_play"]["expected"] >= sc["pass_threshold"] and not waiting_is_right(spec):
        errs.append("null_play.expected passes; document why waiting is right or fix the rule")
    if spec.get("role") == "retired" and not spec.get("retired_reason"):
        errs.append("role retired needs a retired_reason")
    if "role" in spec and (spec["role"] == "retired") != (spec.get("status") == "retired"):
        errs.append("role retired and status retired go together")
    names = [p["name"] for p in sc.get("reference_plays", [])]
    if "oracle" not in names:
        errs.append("scoring.reference_plays needs an 'oracle' play")
    if not any(p.get("is_recorded_mistake") for p in sc.get("reference_plays", [])) and spec["mistake_class"] != "none":
        errs.append("a mistake drill needs a reference play with is_recorded_mistake")
    return errs


def waiting_is_right(spec):
    """The schema's escape hatch: a drill whose null play is declared to pass documents that waiting is correct."""
    sc = spec["scoring"]
    return sc["null_play"]["expected"] >= sc.get("pass_threshold", 1)


# ---------- simulator ----------

def pointer(doc, path):
    """(parent, key) for a JSON Pointer; list indexes are ints, missing dict keys are created on write."""
    parts = [p.replace("~1", "/").replace("~0", "~") for p in path.lstrip("/").split("/")]
    for p in parts[:-1]:
        doc = doc[int(p)] if isinstance(doc, list) else doc.setdefault(p, {})
    last = parts[-1]
    return doc, int(last) if isinstance(doc, list) else last


def get(doc, path):
    try:
        parent, key = pointer(copy.deepcopy(doc), path)
        return parent[key]
    except (KeyError, IndexError, ValueError, TypeError):
        return None


class Simulator:
    """Holds state, clock and flags; answers intents with the first matching response row (v1 replay rows over
    intents). Time intents advance the clock before rows are matched, so `clock_gte` sees the wait. No row: time,
    session and memory intents succeed, a read is answered from state by the adapter (or is a gap), an act is a gap."""

    def __init__(self, spec):
        self.spec, self.state = spec, copy.deepcopy(spec["state"])
        # seconds each non-time intent takes (useful work during a wait counts toward it); 0 = v1 (only waits count)
        self.call_s = spec.get("world", {}).get("constants", {}).get("call_seconds", 0)
        self.clock = spec["fork"]["clock"]["unix"]
        self.flags, self.trace, self.gaps, self.ended = set(), [], [], False
        self.notes = dict(spec["fork"].get("notes") or {})
        self.changed = False  # any state delta applied: recorded pre-fork reads are stale from then on
        lib = spec.get("world", {}).get("rules_library") or ""
        if lib and not lib.startswith("kami-rules@1."):
            raise ValueError(f"unknown rules_library {lib!r}")
        from kami_drills.spec.rules import Rules
        self.rules = Rules(self) if lib else None

    def chain_clock(self):
        return self.clock

    def kami(self, index):
        return next((k for k in self.state["kamis"] if k["index"] == index), None)

    def holds(self, row):
        if not set(kd.as_list(row.get("when"))) <= self.flags or set(kd.as_list(row.get("unless"))) & self.flags:
            return False
        t = self.chain_clock()
        if t < row.get("clock_gte", float("-inf")) or t >= row.get("clock_lt", float("inf")):
            return False
        return all(self.predicate(p) for p in row.get("state", []))

    def predicate(self, p):
        ops = {"eq": lambda a, b: a == b, "ne": lambda a, b: a != b, "gt": lambda a, b: a is not None and a > b,
               "gte": lambda a, b: a is not None and a >= b, "lt": lambda a, b: a is not None and a < b,
               "lte": lambda a, b: a is not None and a <= b, "in": lambda a, b: a in b, "not_in": lambda a, b: a not in b}
        return ops[p["op"]](self.value(p["path"]), p.get("value"))

    def value(self, path):
        if path == "clock":
            return self.chain_clock()
        m = re.fullmatch(r"(hp_frac|alive)\((\d+)\)", path)
        if m:
            k = self.kami(int(m.group(2)))
            if k is None:
                return None
            return k["hp"] / k["hp_max"] if m.group(1) == "hp_frac" else k["state"] != "DEAD"
        return get(self.state, path)

    def step(self, intent, commit=True):
        """Result of one intent; commit=False is a dry run (no clock, flags or state change)."""
        verb, args = intent["verb"], intent.get("args") or {}
        if verb == "quote" and args.get("of"):  # a dry run answers what the act would do now, and changes nothing
            act = {"verb": args["of"], "args": {k: v for k, v in args.items() if k != "of"}}
            res = self.step(act, commit=False)
            return self._record(intent, {**res, "dry_run_of": args["of"]}, commit)
        before = self.clock
        if verb in TIME_VERBS and commit:
            self.clock += float(args.get("seconds") or 0) if verb == "wait" else 60 * float(args.get("minutes") or 0)
        elif commit:
            self.clock += self.call_s
        call = to_call({**intent, "ok": True})
        for row in self.spec["responses"]:
            if matches(call, row["match"]) and self.holds(row):
                if commit:
                    self.flags = (self.flags | set(kd.as_list(row.get("sets")))) - set(kd.as_list(row.get("clears")))
                    self.apply(row["result"].get("state_delta", []))
                return self._record(intent, dict(row["result"]), commit)
        if not commit:
            self.clock = before
        if self.rules and verb not in PASSIVE and (r := self.rules.act(intent)):
            res, apply, flags = r
            if commit:
                self.flags |= flags
                if res["class"] == "ok" and apply:
                    apply()
                    self.changed = True
            return self._record(intent, res, commit)
        if verb in TIME_VERBS or verb in SESSION_END or verb in ("note_write", "note_read"):
            if commit and verb == "note_write":
                self.notes[args.get("path", "")] = args.get("content")
            return self._record(intent, {"class": "ok"}, commit)
        if verb == "read":
            return self._record(intent, {"class": "ok", "from_state": True}, commit)
        return self._record(intent, {"class": "gap"}, commit)

    def apply(self, deltas):
        for d in deltas:
            parent, key = pointer(self.state, d["path"])
            cur = parent[key] if (isinstance(parent, list) or key in parent) else 0
            parent[key] = d["value"] if d["op"] == "set" else (cur or 0) + d["value"]
            self.changed = True

    def _record(self, intent, result, commit):
        if commit:
            self.trace.append({**intent, "ok": result["class"] == "ok", "result": result})
            if intent["verb"] in SESSION_END:
                self.ended = True
            if result["class"] == "gap":
                self.gaps.append(intent["verb"])
        return result

    def run(self, intents):
        for i in intents:
            if self.ended:
                break
            self.step(i)
        return self


# ---------- scoring ----------

def to_call(intent):
    """Intent -> the v1 rule engine's call shape, so task.rule_failures/matches score intents unchanged. Verb plays the
    tool; ok and dry_run ride along as reserved args (`__ok`, `__dry_run`) so a pattern can require a *failed* intent,
    which v1's `ok` key cannot say."""
    args = {**(intent.get("args") or {}), "__ok": bool(intent.get("ok")), "__dry_run": bool(intent.get("dry_run"))}
    return {"tool": intent["verb"], "args": args, "ok": bool(intent.get("ok"))}


def to_pattern(p):
    if isinstance(p, str):
        return {"tool": p}
    if isinstance(p, list):
        return [to_pattern(x) for x in p]
    out = {"tool": p["verb"], "args": dict(p.get("args") or {})}
    if "ratio" in p:
        out["ratio"] = p["ratio"]
    if p.get("ok") is True:
        out["ok"] = True
    elif p.get("ok") is False:
        out["args"]["__ok"] = False
    if "dry_run" in p:
        out["args"]["__dry_run"] = p["dry_run"]
    return out


def matches(call, pattern):
    return kd.matches(call, to_pattern(pattern))


def _rule_patterns(rule):
    """Copy of an intent rule with every pattern in v1 tool-pattern form."""
    r = dict(rule)
    for k in ("require_any", "forbid", "require_sequence"):
        if k in r:
            r[k] = [to_pattern(p) for p in r[k]]
    if "require_all" in r:
        r["require_all"] = [[to_pattern(p) for p in g] for g in r["require_all"]]
    if "forbid_before" in r:
        r["forbid_before"] = [{"forbid": [to_pattern(p) for p in x["forbid"]], "before": [to_pattern(p) for p in x["before"]]}
                              for x in r["forbid_before"]]
    if "require_count" in r:
        r["require_count"] = [{"any": [to_pattern(p) for p in x["any"]], "n": x["n"]} for x in r["require_count"]]
    return r


def rule_failures(rule, sim):
    calls = [to_call(i) for i in sim.trace]
    why = kd.rule_failures(_rule_patterns(rule), calls, sim.flags)
    for p in rule.get("end_state", []):
        if not sim.predicate(p):
            why.append(f"end state predicate fails: {p['path']} {p['op']} {p.get('value')!r}")
    if rule.get("needs_action") and not any(i["ok"] and i["verb"] not in PASSIVE for i in sim.trace):
        why.append("no ok state-changing intent")
    return why


def refusals(sim):
    """Act intents the simulation refused (reads, quotes, time and gaps are not refusals)."""
    return [i for i in sim.trace if i["verb"] not in PASSIVE and i["result"]["class"] in ("revert", "validation_error")]


def score(spec, sim):
    """{"score", "pass", "why"} for a finished simulation. Only the intent trace, flags and end state are read."""
    sc = spec["scoring"]
    if sc["kind"] not in SCORERS:
        raise NotImplementedError(f"scoring kind {sc['kind']!r} is not implemented")
    out = _base_score(sc, sim)
    if out.get("final"):
        return {k: out[k] for k in ("score", "pass", "why")}
    value, why = out["score"], out["why"]
    for fc in sc.get("flag_caps", []):
        if fc["flag"] in sim.flags and value > fc["max"]:
            value = fc["max"]
            why.append(f"capped at {fc['max']:g}: {fc['flag']}")
    if (rp := sc.get("refusal_penalty")) and (n := len(refusals(sim))):
        cost = min(rp["amount"] * n, rp.get("cap", float("inf")))
        value -= cost
        why.append(f"{n} refused act(s) -{cost:g}")
    value = max(0.0, value)
    return {"score": value, "pass": value >= threshold(spec) - 1e-9, "why": why}


def _base_score(sc, sim):
    if sc["kind"] == "rule":
        why = rule_failures(sc["rule"], sim)
        if not why:
            return {"score": 1.0, "why": []}
        best = max((x for x in sc.get("lesser", []) if not rule_failures(x["rule"], sim)), key=lambda x: x["credit"], default=None)
        return {"score": best["credit"] if best else 0.0, "why": why + ([f"lesser play: {best['credit']:g}"] if best else [])}
    if sc.get("rule") and (gate := rule_failures(sc["rule"], sim)):
        return {"score": 0.0, "pass": False, "why": ["gate: " + "; ".join(gate)], "final": True}
    total = sum(c["weight"] for c in sc["components"])
    credit = {c["id"]: component_credit(c, sim) for c in sc["components"]}
    value = sum(c["weight"] * credit[c["id"]] for c in sc["components"]) / total
    why = [f"missing {i}" if not v else f"{i} at {v:g}" for i, v in credit.items() if v < 1]
    calls = [to_call(i) for i in sim.trace]
    for pen in sc.get("penalties", []):
        n = sum(matches(c, pen["match"]) for c in calls)
        if not n:
            continue
        if pen.get("zero_score"):
            return {"score": 0.0, "pass": False, "why": why + [f"zero_score penalty {pen['id']}"], "final": True}
        cost = pen["amount"] * (n if pen.get("per") == "each" else 1)
        value -= min(cost, pen.get("cap", cost))
        why.append(f"penalty {pen['id']} -{min(cost, pen.get('cap', cost)):g}")
    return {"score": value, "why": why}


def component_credit(c, sim):
    """1 if the component's rule holds; for `any_of`, the best credit among the alternatives whose rule holds; else 0."""
    if "rule" in c:
        return 0.0 if rule_failures(c["rule"], sim) else 1.0
    return max((x["credit"] for x in c["any_of"] if not rule_failures(x["rule"], sim)), default=0.0)


def play(spec, intents):
    """Simulate a list of intents from the fork and score it (the harness-free reference path)."""
    sim = Simulator(spec).run(copy.deepcopy(intents))
    return {**score(spec, sim), "sim": sim}


def cooldown_rows(match, until, skew_s=0, ok=None, native=None, flag="early_retry", late_from=None):
    """Response rows for an act gated by a cooldown on the simulated clock: ok from until + skew_s (chain time lags the
    agent's clock: observed skew plus ~2 blocks), a recorded-style revert that sets `flag` before `until` (acting on a
    cooldown the agent could read), and an unflagged revert in the skew window (the live game reverts there too, so
    retrying after it is fair). Split waits and useful work count, because only the clock decides. `late_from`: an act
    from then on still succeeds but sets "late" (a rule can forbid acting long after the cooldown, e.g. sleeping it off)."""
    revert = {"class": "revert", "reason": "COOLDOWN", **({"native": native} if native else {})}
    rows = [{"match": match, "clock_gte": late_from, "sets": "late", "provenance": "rule", "result": ok or {"class": "ok"}}] if late_from else []
    rows += [{"match": match, "clock_gte": until + skew_s, "provenance": "rule", "result": ok or {"class": "ok"}},
            {"match": match, "clock_lt": until, "sets": flag, "provenance": "recorded" if native else "rule", "result": revert}]
    if skew_s:
        rows.append({"match": match, "provenance": "rule", "result": revert})
    return rows


def threshold(spec):
    return spec["scoring"].get("pass_threshold", 1)
