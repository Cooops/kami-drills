"""Keyed pseudonymization (H review M5; applied from bank rc3 and v1.1): wallets and third-party accounts change,
verdicts do not, and a second pass changes nothing."""
import collections
import functools
import glob
import json
import os
import re
from pathlib import Path

import pytest

from kami_drills.spec import check as ck
from kami_drills.spec import perturb
from kami_drills.spec import pseudonymize as P

ROOT = Path(__file__).parents[1]
BANK = max((ROOT / "bank").glob("v2.0-rc*"), key=lambda p: int(p.name.rsplit("rc", 1)[1]))


def bank_specs():
    return [json.loads((BANK / e["file"]).read_text()) for e in json.loads((BANK / "manifest.json").read_text())["public"]]


def public_specs():
    v1 = [json.loads(open(f).read()) for f in sorted(glob.glob(str(ROOT / "specs/v1.1/*.json")) + glob.glob(str(ROOT / "drills_v2/spec/*.json")))]
    return [s for s in v1 if "fork" in s] + bank_specs()


def raw(sid):
    """A v1.1 spec with its native half as recorded in the frozen v1 drill (not pseudonymized)."""
    s = json.loads((ROOT / "specs/v1.1" / f"{sid}.json").read_text())
    v1 = json.loads((ROOT / "drills" / f"{sid}.json").read_text())
    s["fork"]["native_prefix"].update(messages=v1["prefix"], recorded_reads=v1["recorded_reads"])
    return s


def test_wallets_and_third_parties_are_replaced_and_verdicts_kept():
    s = raw("repeat-take-trade-underflow")
    out, stats = P.pseudonymize(s, b"test-key")
    before, after = json.dumps(s), json.dumps(out)
    gone = {a.lower() for a in P.ADDR.findall(before)} - P.allowlist()
    assert stats["addresses"] and not gone & {a.lower() for a in P.ADDR.findall(after)}
    assert P.third_parties(s) and {n for _, n in P.third_parties(out) if not P.ALIAS.fullmatch(n)} == set()
    assert out["state"]["account"]["index"] == s["state"]["account"]["index"]
    assert [k["index"] for k in out["state"]["kamis"]] == [k["index"] for k in s["state"]["kamis"]]
    assert perturb.check_equivalent(s, out, lambda x: ck.check(x)) == {}
    again, _ = P.pseudonymize(s, b"test-key")
    assert again == out  # deterministic under one key
    twice, st2 = P.pseudonymize(out, b"other-key")
    assert twice == out and st2["addresses"] == 0  # idempotent: pseudonyms are never replaced again


# ---- an independent detector (owner decision: other players' wallets AND names are aliased; the owner's own handle
# is a third party too, unless it is an S1 agent wallet). It does not call the tool: it harvests the raw player
# records from the S1 transcripts when they are next to the repo ($KAMI_DRILLS_S1_RUNS, which should be the final copy
# ../kamiarena/runs/s1-final; by default it uses s1-final if present, else the interim ../kamiarena/runs/s1), and
# otherwise falls back to fixed regexes over the committed text.
_RUNS = ROOT.parent / "kamiarena" / "runs"
S1 = Path(os.environ.get("KAMI_DRILLS_S1_RUNS") or next((p for p in (_RUNS / "s1-final", _RUNS / "s1") if p.exists()), _RUNS / "s1"))
Q, SEP = r'\\*"', r'(?:\s|\\+n)*'
ACCOUNT_PARENTS = {"maker", "taker", "account", "accounts", "owner", "seller", "buyer", "players", "members", "friends",
                   "rows", "leaderboard", "data"}
HEX40 = re.compile(r"(?<![0-9a-fA-Fx])0x[0-9a-fA-F]{40}(?![0-9a-fA-F])")
HEXCUT = re.compile(r"(?<![0-9a-fA-Fx])0x[0-9a-fA-F]{36,39}(?![0-9a-fA-F])")
DEC160 = re.compile(r"(?<!\d)\d{46,49}(?!\d)")
DECCUT = re.compile(r"(?<!\d)\d{40,45}(?!\d)")
HEXDATA = re.compile(r"0x([0-9a-fA-F]{64,})(?![0-9a-fA-F])")
RECORD = re.compile(rf'\{{{SEP}{Q}id{Q}{SEP}:{SEP}{Q}0x[0-9a-fA-F]{{36,40}}{Q},{SEP}{Q}index{Q}{SEP}:{SEP}(\d+),{SEP}{Q}name{Q}{SEP}:{SEP}{Q}([^"\\]+){Q}')


@functools.lru_cache(maxsize=1)
def s1_players():
    """{(index, name)} of player accounts recorded in S1 tool results (a kami's 64-hex entity id is not an account)."""
    out = set()

    def walk(x, parent=None):
        if isinstance(x, dict):
            if isinstance(x.get("index"), int) and isinstance(x.get("name"), str) and not re.fullmatch(r"Kamigotchi \d+", x["name"]) \
                    and not (isinstance(x.get("id"), str) and len(x["id"]) == 66) \
                    and (parent in ACCOUNT_PARENTS - {"data"} or "ownerAddress" in x or "operatorAddress" in x):
                out.add((x["index"], x["name"]))
            for k, v in x.items():
                walk(v, k)
        elif isinstance(x, list):
            for v in x:
                walk(v, parent)
    for f in sorted(S1.glob("s1-*/transcripts/*.jsonl")):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            m = json.loads(line) if line.strip() else {}
            if m.get("role") == "tool_result":
                try:
                    walk(json.loads(m["content"]) if isinstance(m["content"], str) else m["content"])
                except ValueError:
                    pass
    return frozenset(out)


def raw_people(spec):
    """Raw third-party data left in one committed spec: wallets, truncated wallets, decimal account ids, player records."""
    text, own = json.dumps(spec, ensure_ascii=False), spec["state"]["account"]["index"]
    allow = P.allowlist()
    left = {a for a in HEX40.findall(text) if a.lower() not in allow and not a.startswith("0x00000000")}
    left |= {a for a in HEXCUT.findall(text) if not a.startswith("0x00000000") and not any(x.startswith(a.lower()) for x in allow)}
    left |= {d for d in DEC160.findall(text) if 2 ** 150 <= int(d) < 2 ** 160 and f"0x{int(d):040x}" not in allow}
    left |= {d for d in DECCUT.findall(text) if not any(str(int(x, 16)).startswith(d) for x in allow)}
    # a wallet as a 32-byte ABI word inside tx data or a topic (no 0x of its own); top 4 bytes zero = a uint or an alias
    for h in HEXDATA.findall(text):
        h = h.lower()
        words = [h[i:i + 64] for i in range(8 if (len(h) - 8) % 64 == 0 else 0, len(h) - 63, 64)]
        left |= {"0x" + w[24:] for w in words if w[:24] == "0" * 24 and w[24:32] != "0" * 8 and "0x" + w[24:] not in allow}
    left |= {f"{i}:{n}" for i, n in RECORD.findall(text) if int(i) != own and not re.fullmatch(r"player-[0-9a-f]{6}", n)}
    if S1.is_dir():
        for i, n in s1_players():
            esc = re.escape(json.dumps(n, ensure_ascii=False)[1:-1])
            if i != own and n in text and re.search(rf'(?<!\d){i},{SEP}{Q}name{Q}{SEP}:{SEP}{Q}{esc}{Q}', text):
                left.add(f"{i}:{n}")
    return left


CTX = re.compile(r"(?i:acct|account|player|user|seller|buyer|maker|taker|owner|idx)")
IDX_CTX = r"(?i:acct|account|user|player|owner|killer|victim|seller|buyer|maker|taker|idx)(?:\s+(?i:index))?"
WORDS = Path("/usr/share/dict/words")


@functools.lru_cache(maxsize=1)
def free_text_names():
    """(names that must not appear as a word anywhere, names that must not appear in a name context): the S1 player
    names harvested here from the transcripts (never committed), split by whether the name is also an English word or
    first name. Agent names and harness/game words are not names."""
    agents = {a.lower() for a in json.loads(P.ALLOWLIST.read_text())["palaestra_s1_agents"]}
    h = P.harvest_players(sorted(S1.glob("s1-*")), agents)
    names = {n for _, n in s1_players() if P.real_name(n)} - h["agents"]
    words = {w.strip().lower() for w in WORDS.open(encoding="utf-8", errors="replace")} if WORDS.exists() else None
    shape = lambda n: bool(re.search(r"[^A-Za-z]", n)) or bool(re.search(r"[a-z][A-Z]", n)) or len(n) >= 9
    dictionary = lambda n: words is None or any(w in words for w in (n.lower(), n.lower().rstrip("s"), n.lower()[:-2]))
    word = {n for n in names if len(n) >= 4 and (shape(n) or (len(n) >= 5 and not dictionary(n)))}
    return frozenset(word), frozenset(names - word)


def raw_names_in_text(spec):
    """Player names left in free text (prose, notes, truncated results), at any escaping depth."""
    text = json.dumps(spec, ensure_ascii=False)
    word, plain = free_text_names()
    b, e = r"(?:(?<=\\[nrt])|(?<![A-Za-z0-9_]))", r"(?![A-Za-z0-9_])"
    sep = r"(?:\\+|[^A-Za-z0-9_:\\]){1,6}"
    left = {n for n in word if n in text and re.search(b + re.escape(n) + e, text)}
    left |= {n for n in plain if n in text and re.search(rf"\b{CTX.pattern}\b{sep}(?:#?\d+{sep})?{b}{re.escape(n)}{e}", text)}
    return left


BROKEN = re.compile(r"account\\*'player-|All player-|player-[0-9a-f]{6} kami\(s\)|level-player-|node player-")


@pytest.mark.parametrize("spec", public_specs(), ids=lambda s: s["id"])
def test_pseudonyms_never_land_on_ordinary_tokens(spec):
    """An alias where an account label, a count or a game word was (review rc6 F1): "account 'main'", "All 1 kami(s)"."""
    assert not BROKEN.search(json.dumps(spec, ensure_ascii=False))


TOKEN = re.compile(r"[A-Za-z0-9_'-]+(?:\.[A-Za-z0-9_'-]+)*")  # a sentence-final period is not part of the token


def changed_tokens(raw, out):
    """Tokens the raw text has more of than the pseudonymized text."""
    a, b = collections.Counter(TOKEN.findall(raw)), collections.Counter(TOKEN.findall(out))
    return {t for t, n in a.items() if n > b.get(t, 0)}


def allowed_changes(raw):
    """What pseudonymization may change in a raw text: record names and indices, wallets and ids, and the words of
    those names, plus truncated hex; and the index of a name-less account record or account field (BARE, FIELD)."""
    ok = set(BARE.findall(raw)) | set(FIELD.findall(raw))
    for i, n in re.findall(rf'index{Q}{SEP}:{SEP}(\d+),{SEP}{Q}name{Q}{SEP}:{SEP}{Q}([^"\\]+){Q}', raw):
        ok |= {i, n} | set(TOKEN.findall(n))
    return ok


V1_DERIVED = sorted([("specs/v1.1", p.stem) for p in (ROOT / "specs/v1.1").glob("*.json") if p.stem != "MANIFEST"]
                    + [("drills_v2/spec", p.stem) for p in (ROOT / "drills_v2/spec").glob("*.json")])
B_, E_ = r"(?:(?<=\\[nrt])|(?<![A-Za-z0-9_]))", r"(?![A-Za-z0-9_])"


def v1_pair(where, sid):
    """(raw native prefix text from the frozen v1 drill, pseudonymized text, the spec) or None."""
    s = json.loads((ROOT / where / f"{sid}.json").read_text())
    v1id = ((s.get("provenance") or {}).get("source") or {}).get("v1_id", sid)
    if "fork" not in s or not (ROOT / "drills" / f"{v1id}.json").exists():
        return None
    v1 = json.loads((ROOT / "drills" / f"{v1id}.json").read_text())
    raw = json.dumps([v1["prefix"], v1["recorded_reads"]], ensure_ascii=False)
    out = json.dumps([s["fork"]["native_prefix"]["messages"], s["fork"]["native_prefix"]["recorded_reads"]], ensure_ascii=False)
    return raw, out, s


@pytest.mark.parametrize("where,sid", V1_DERIVED, ids=lambda x: x if isinstance(x, str) else None)
def test_v1_specs_change_only_names_and_wallets(where, sid):
    """Every v1-derived native prefix (v1.1 and drills_v2/spec) differs from its frozen v1 drill only at player names,
    their indices and wallets or ids, and every alias lines up with a name it replaced (review rc6 F1, rc7 cleanup)."""
    got = v1_pair(where, sid)
    if got is None:
        pytest.skip("no native prefix")
    raw, out, s = got
    ok = allowed_changes(raw)
    hexy = re.compile(r"(0x)?[0-9a-fA-F]{16,}|\d{6,}")
    bad = {t for t in changed_tokens(raw, out) if t not in ok and not hexy.fullmatch(t)}
    assert {t for t in bad if not any(t in n for n in ok)} == set()
    # full alignment: every alias in the output is the V1_KEY alias of a name recorded in the raw drill, and each
    # name's occurrences are conserved (raw = left + replaced by its alias)
    names = {n for _, n in re.findall(rf'index{Q}{SEP}:{SEP}(\d+),{SEP}{Q}name{Q}{SEP}:{SEP}{Q}([^"\\]+){Q}', raw)}
    alias = {n: f"player-{P._mac(P.V1_KEY, 'name|' + n)[:6]}" for n in names}
    assert set(re.findall(r"player-[0-9a-f]{6}", out)) <= set(alias.values())
    record = lambda n, t: len(re.findall(rf'name{Q}{SEP}:{SEP}{Q}{re.escape(json.dumps(n, ensure_ascii=False)[1:-1])}{Q}', t))
    for n in names:
        if n.startswith("player-") or P.real_name(n):
            continue  # a real name may be replaced in free text too
        # not a name in free text ("0x00", a quest title, "main"): its alias may stand at record sites only
        a = alias[n]
        assert out.count(a) == record(a, out), (n, out.count(a), record(a, out))


def own_names(spec):
    """The drill's own account name in its raw v1 drill (v1.1 and drills_v2/spec), unless it is an S1 agent's."""
    sid = (spec.get("provenance", {}).get("source") or {}).get("v1_id")
    if not sid or not (ROOT / "drills" / f"{sid}.json").exists():
        return set()
    own = spec["state"]["account"]["index"]
    raw = (ROOT / "drills" / f"{sid}.json").read_text()
    found = set(re.findall(rf'index{Q}{SEP}:{SEP}{own},{SEP}{Q}name{Q}{SEP}:{SEP}{Q}([^"\\]+){Q}', raw))
    return {n for n in found if len(n) >= 4 and P.real_name(n)}


@pytest.mark.parametrize("spec", public_specs(), ids=lambda s: s["id"])
def test_public_specs_carry_no_raw_name_in_free_text(spec):
    """No harvested S1 player name is left in free text (a plain-word name only checked in name contexts), and the
    drill's own name (the owner's handle in v1.1) is gone too."""
    text = json.dumps(spec, ensure_ascii=False)
    if S1.is_dir() and "v1_id" not in (spec.get("provenance", {}).get("source") or {}):  # S1 names: the S1 bank
        assert raw_names_in_text(spec) == set()
    assert {n for n in own_names(spec) if re.search(rf"(?<![A-Za-z0-9_]){re.escape(n)}(?![A-Za-z0-9_])", text)} == set()


@functools.lru_cache(maxsize=1)
def s1_third_party_indices():
    """Account indices of S1 player records that are not a run agent's (by name: agents keep their raw index)."""
    agents = free_text_agents()
    return frozenset(i for i, n in s1_players() if n not in agents)


@functools.lru_cache(maxsize=1)
def free_text_agents():
    agents = {a.lower() for a in json.loads(P.ALLOWLIST.read_text())["palaestra_s1_agents"]}
    return frozenset(P.harvest_players(sorted(S1.glob("s1-*")), agents)["agents"])


def raw_indices_in_text(text, own, third):
    """Third-party account indices left raw after an account word on the same line of free text: "(acct 9101", "user
    index 909". A one-digit number is not checked ("user 2 options"), nor a 2-digit one after "idx", which also names
    quests ("MSQ016 (idx 16)")."""
    found = re.findall(rf"(?:(?<=\\[nrt])|(?<![A-Za-z0-9_]))({IDX_CTX})(?![A-Za-z0-9_])[ #(:]{{1,3}}(\d{{2,}})(?![A-Za-z0-9_])", text)
    return {f"idx:{d}" for w, d in found if int(d) != own and int(d) in third and not (w.lower().startswith("idx") and len(d) < 3)}


@pytest.mark.parametrize("spec", public_specs(), ids=lambda s: s["id"])
def test_public_specs_carry_no_raw_index_in_free_text(spec):
    """No S1 third-party account index is left beside an account word in free text (review rc9): it would re-identify
    the alias next to it. Needs the private S1 runs; test_public_specs_carry_no_raw_account_index is the data-free
    check CI runs."""
    if not S1.is_dir():
        pytest.skip("S1 runs not next to the repo (set KAMI_DRILLS_S1_RUNS to kamiarena/runs/s1-final)")
    own = spec["state"]["account"]["index"]
    assert raw_indices_in_text(json.dumps(spec, ensure_ascii=False), own, s1_third_party_indices()) == set()


def test_free_text_indices_are_aliased_and_ordinary_numbers_are_not(monkeypatch):
    monkeypatch.setitem(P.SEASON, "agent_indices", {3429})
    monkeypatch.setitem(P.SEASON, "player_indices", {9101, 909, 9303, 89, 2, 1, 3})
    s = {"state": {"account": {"index": 1}, "kamis": [{"index": 9707}]},
         "x": ["NightOwl (acct 9101, kami 9898) liquidated 9707", "- user index 909: kamiIndex 2424", "a bare acct 9303.",
               "Account 3429 Cinderpath is a run account", "victim 9707 was ours", "user 2 options", "account for 3 quests",
               "player 1 of 4", "caller's account\n2. Kami", "acct #9101 again", "acct: 89 then", "MIN004 (idx 2004) open", "Account idx 9101", "MSQ016 (idx 89)"]}
    out = P.pseudonymize(s, b"k")[0]["x"]
    a = lambda i: str(P.index_alias(b"k", i))
    assert out[0] == f"NightOwl (acct {a(9101)}, kami 9898) liquidated 9707"
    assert out[1] == f"- user index {a(909)}: kamiIndex 2424" and out[2] == f"a bare acct {a(9303)}."
    assert out[3:9] == s["x"][3:9]  # a run account, the drill's own kami, ordinary numbers: unchanged
    assert out[9] == f"acct #{a(9101)} again" and out[10] == f"acct: {a(89)} then"
    assert out[11] == "MIN004 (idx 2004) open" and out[12] == f"Account idx {a(9101)}"  # idx: a known index only
    assert out[13] == "MSQ016 (idx 89)"  # ... of 3+ digits: a quest index can share a short account index
    assert raw_indices_in_text(json.dumps(s), 1, {9101, 909, 9303}) == {"idx:9101", "idx:909", "idx:9303"}
    assert raw_indices_in_text(json.dumps(out), 1, {9101, 909, 9303}) == set()
    rec = json.dumps({"maker": {"index": 9101, "name": "NightOwl"}})  # the same alias as the structured record
    assert json.loads(P.pseudonymize({**s, "x": [rec]}, b"k")[0]["x"][0])["maker"]["index"] == int(a(9101))
    assert P.pseudonymize({**s, "x": out}, b"k")[0]["x"] == out  # idempotent


RUN_INDICES = frozenset(json.loads(P.ALLOWLIST.read_text())["palaestra_s1_agent_indices"])
PARENTS = {"account", "maker", "taker", "seller", "buyer", "owner", "occupant", "killer", "victim"}
BARE = re.compile(rf'(?<![A-Za-z_])(?:{"|".join(PARENTS)}){Q}{SEP}:{SEP}\{{(?:[^{{}}]|\{{[^{{}}]*\}})*?{Q}index{Q}{SEP}:{SEP}(\d+)(?!\d)')
FIELD = re.compile(rf'(?<![A-Za-z])(?:[A-Za-z]+_)?(?:account_index|account_key){Q}{SEP}:{SEP}(?:{Q})?(\d+)(?![\w.])')
FREE_IDX = re.compile(rf"(?:(?<=\\[nrt])|(?<![A-Za-z0-9_])){IDX_CTX.replace('|idx', '')}(?![A-Za-z0-9_])[ #(:]{{1,3}}(\d{{3,}})(?![A-Za-z0-9_])")


def raw_account_indices(spec):
    """Account indices left raw in a spec, with no private data: every index in an account record (named or not, an
    "account"/"maker"/... object, also inside tool-result strings and truncated results), every *_account_index or
    account_key field, and every 3+ digit number after an account word in free text must be an alias (>= 10**7), the
    drill's own index or kami, an S1 run account (pseudonym_allowlist.json) or 0 (no account)."""
    text, ok = json.dumps(spec, ensure_ascii=False), {0, spec["state"]["account"]["index"]} | RUN_INDICES
    ok |= {k["index"] for k in spec["state"].get("kamis") or [] if isinstance(k.get("index"), int)}
    found = set(BARE.findall(text)) | set(FIELD.findall(text)) | set(FREE_IDX.findall(text))

    def walk(x, parent=None):
        if isinstance(x, dict):
            if parent in PARENTS and isinstance(x.get("index"), int):
                found.add(str(x["index"]))
            for k, v in x.items():
                walk(v, k)
        elif isinstance(x, list):
            for v in x:
                walk(v, parent)
        elif isinstance(x, str) and x.lstrip()[:1] in "{[":
            try:
                walk(json.loads(x), parent)
            except ValueError:
                pass
    walk(spec)
    return {i for i in map(int, found) if i not in ok and i < 10 ** 7}


@pytest.mark.parametrize("spec", public_specs(), ids=lambda s: s["id"])
def test_public_specs_carry_no_raw_account_index(spec):
    """No raw third-party account index in a structured field or beside an account word (PR #3 review). Data-free, so
    it runs in CI on every public spec, v1-derived ones included."""
    assert raw_account_indices(spec) == set()


def test_nameless_account_records_are_aliased_and_run_accounts_are_not():
    run = min(RUN_INDICES)  # an S1 run account: kept raw, like its wallet
    s = {"state": {"account": {"index": 1}, "kamis": [{"index": 9898}]},
         "x": [{"account": {"index": 9101}}, {"maker": {"index": 9303}}, {"lens_node": {"occupant_account_index": 9404}},
               json.dumps({"data": {"account": {"index": 9505, "roomIndex": 3}}}), '{"maker": {"index": 9606, "name\n[trunc',
               {"account": {"index": run}}, {"account": {"index": 1}}, {"seller": {"index": 9898}},
               {"account": {"index": 9101, "name": "NightOwl"}}, "(acct 9101, kami 9898)", {"account_index": 0}]}
    assert raw_account_indices(s) == {9101, 9303, 9404, 9505, 9606}
    out, _ = P.pseudonymize(s, b"k")
    a = lambda i: P.index_alias(b"k", i)
    x = out["x"]
    assert x[0] == {"account": {"index": a(9101)}} and x[1] == {"maker": {"index": a(9303)}}
    assert x[2] == {"lens_node": {"occupant_account_index": a(9404)}}
    assert json.loads(x[3])["data"]["account"]["index"] == a(9505) and str(a(9606)) in x[4]
    assert x[5:8] == s["x"][5:8] and x[10] == {"account_index": 0}  # run account, own index, own kami, no account
    assert x[8]["account"]["index"] == a(9101) and x[9] == f"(acct {a(9101)}, kami 9898)"  # one alias everywhere
    assert raw_account_indices(out) == set()
    assert P.pseudonymize(out, b"k")[0] == out  # idempotent


@pytest.mark.parametrize("spec", public_specs(), ids=lambda s: s["id"])
def test_public_specs_carry_no_raw_person(spec):
    """No committed spec keeps a third party's wallet (whole, truncated or as a decimal account id) or player record."""
    assert raw_people(spec) == set()


def test_ids_and_truncated_wallets_are_aliased():
    a = "0x1111222233334444555566667777888899990000"  # not an S1 agent wallet
    rec = json.dumps({"seller": {"id": a, "index": 77, "name": "someseller"}})[:-5]  # a truncated result: not JSON
    s = {"state": {"account": {"index": 1}, "kamis": []}, "x": [a, str(int(a, 16)), a[:39] + "...", rec]}
    out, _ = P.pseudonymize(s, b"k")
    assert raw_people(out) == set() and "someseller" not in json.dumps(out)
    assert int(out["x"][1]) == int(out["x"][0], 16)  # the decimal id gets the same alias as the address
    assert P.pseudonymize(out, b"other")[0] == out


def test_a_wallet_inside_calldata_is_aliased_and_uints_are_not():
    a = "1111222233334444555566667777888899990000"  # a synthetic player wallet
    agent = json.loads(P.ALLOWLIST.read_text())["palaestra_s1_agents"][0].lower()[2:]
    uints = ["0" * 61 + "3e8", f"{2 ** 127 + 5:064x}", f"{10 ** 30:064x}"]  # 1000, a 2**127 amount, 1e30 wei
    word = lambda x: "0" * 24 + x
    data = "0xa9059cbb" + word(a) + uints[0] + word(agent) + uints[1] + uints[2]
    cut = str(int(a, 16))[:42]  # a decimal account id cut off by a truncated result
    s = {"state": {"account": {"index": 1}, "kamis": []}, "x": ["0x" + a, data, json.dumps({"data": data}), cut]}
    assert {"0x" + a, cut} <= raw_people(s)  # the detector sees the padded wallet and the cut id
    out, _ = P.pseudonymize(s, b"k")
    alias, got = out["x"][0], out["x"][1]
    assert raw_people(out) == set() and a not in json.dumps(out) and cut not in json.dumps(out)
    assert got == "0xa9059cbb" + word(alias[2:]) + uints[0] + word(agent) + uints[1] + uints[2]  # the 0x form's alias
    assert json.loads(out["x"][2])["data"] == got
    assert P.pseudonymize(out, b"other")[0] == out  # idempotent
    assert not any(P.padded_address(u[24:]) for u in uints) and P.padded_address(a)


def test_a_maker_record_is_renamed():
    rec = json.dumps({"data": {"open": [{"maker": {"index": 9606, "name": "someplayer"}, "type": "Sell"}]}}, indent=2)
    s = {"state": {"account": {"index": 1}, "kamis": []}, "fork": {"native_prefix": {"messages": [{"content": rec}]}}}
    out, stats = P.pseudonymize(s, b"k")
    text = out["fork"]["native_prefix"]["messages"][0]["content"]
    assert stats["accounts"] == 1 and "someplayer" not in text and "9606" not in text and "player-" in text


def test_free_text_names_follow_the_plain_word_rule():
    # made-up players, known to the pass from the records in the spec: one distinctive name, two plain words
    recs = json.dumps({"open": [{"maker": {"index": 9101, "name": "ShadowFox9"}}, {"maker": {"index": 9404, "name": "today"}},
                                {"maker": {"index": 9505, "name": "Bramble"}}]})
    s = {"state": {"account": {"index": 1}, "kamis": []},
         "x": [recs, "killed by 9797 (acct 9101 ShadowFox9)", "(acct 9404 \"today\") today is fine", "(Bramble, kami 21629)",
               "Bramble went home", '{"account": "main", "kami_id": 7}', "ShadowFox9 won again"]}
    out = P.pseudonymize(s, b"k")[0]["x"]
    assert "ShadowFox9" not in out[1] and "9101" not in out[1]          # the name, and its index beside it
    assert out[2].endswith("today is fine") and "\"today\"" not in out[2]  # a plain word: in its name context only
    assert "Bramble" not in out[3] and out[4] == "Bramble went home"
    assert out[5] == '{"account": "main", "kami_id": 7}'                # a tool argument is not a name context
    assert out[6].endswith(" won again") and "ShadowFox9" not in out[6]   # a distinctive name: anywhere
