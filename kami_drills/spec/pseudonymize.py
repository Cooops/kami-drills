"""Keyed pseudonymization of personal data in public specs (H review M5; owner decision 2026-11-03: applied to v1.1,
drills_v2/spec and the bank from rc3).

Applied where the specs are made, so a rebuild keeps it:
- v1-derived specs (specs/v1.1, drills_v2/spec): `from_v1.convert`, under the public key V1_KEY. Their raw text is in the
  frozen public drills/ anyway; the key is public so a clean clone can rebuild and check them.
- V1_KEY is public: a v1.1 alias can be reversed by hashing a list of wallets (the raw text is in drills/ anyway).
- the bank: the miner's split (`miner/split.py`), under a key derived from the private split key, so bank aliases
  cannot be linked to v1.1 aliases (and through drills/ to the raw names).
By hand: KAMI_PSEUDO_KEY=... uv run kami-drills spec pseudonymize PATHS --key-env KAMI_PSEUDO_KEY --out DIR

What changes, consistently across every spec under one key (the same input always gets the same alias):
- every 0x-address (40 hex) except the allowlist -> 0x00000000 + HMAC(key, address)[:32]; the same address as a decimal
  160-bit account id -> that alias as an integer; an address as a 0{24}-padded ABI word in tx data or a topic -> the
  same alias, padded (a word is an address only with a nonzero byte in its top 4 bytes, so no ordinary uint changes);
  an address truncated to 36-39 hex -> a pseudonym of the same length; a decimal id truncated to 40-45 digits -> a
  128-bit integer. The allowlist is the vendored
  harness surface's addresses plus pseudonym_allowlist.json: game contracts and tokens, and the Palaestra S1 agents'
  own owner and operator wallets (kept as recorded). A pseudonym starts 0x00000000, so it is recognizable and is
  never replaced again (the tool is idempotent);
- a third-party player account (a recorded {index, name} under maker/owner/account/... or with a 40-hex id, not the
  drill's own account): its name -> player-<hmac6> and its index in that record -> a fresh index above 10**7.

What stays: the drill's own account index, kami ids and quest ids (scoring reads them), tx hashes and entity ids.
Each output spec must keep every reference-play and battery verdict of its input (perturb.check_equivalent), else
the tool refuses it.
"""
import functools
import hashlib
import hmac
import json
import re
from pathlib import Path

ADDR = re.compile(r"0x[0-9a-fA-F]{40}(?![0-9a-fA-F])")
TRUNC = re.compile(r"(?<![0-9a-fA-Fx])0x[0-9a-fA-F]{36,39}(?![0-9a-fA-F])")  # an address cut off by a truncated result
DEC = re.compile(r"(?<!\d)\d{46,49}(?!\d)")  # a decimal 160-bit account id (account id = owner wallet as an integer)
DEC_LO, DEC_HI = 2 ** 150, 2 ** 160
SURFACE = Path(__file__).parents[1] / "surface"
ALLOWLIST = Path(__file__).parent / "pseudonym_allowlist.json"
V1_KEY = b"kami-drills v1.1 pseudonyms"
DEC_CUT = re.compile(r"(?<!\d)\d{40,45}(?!\d)")  # a decimal account id cut off by a truncated result
WORDS_HEX = re.compile(r"(?<![0-9a-fA-Fx])0x([0-9a-fA-F]{64,})(?![0-9a-fA-F])")  # ABI data and topics: 32-byte words
MARK = "0x00000000"  # pseudonym prefix: 8 zero nibbles, then 32 of the HMAC
ALIAS = re.compile(r"player-[0-9a-f]{6}")


def padded_address(tail):
    """Whether the 40-hex tail of a 0{24}-padded ABI word is an address: a nonzero byte in its top 4 bytes. A uint below
    2**128 (amounts, indices, timestamps) and a pseudonym (0x00000000...) never have one."""
    return tail[:8] != "00000000"


def allowlist():
    out = set()
    for f in SURFACE.glob("*.json"):
        out |= {a.lower() for a in ADDR.findall(f.read_text())}
    a = json.loads(ALLOWLIST.read_text())
    return out | {x.lower() for x in a["contracts"]} | {x.lower() for x in a["palaestra_s1_agents"]}


def _mac(key, msg):
    return hmac.new(key, msg.encode(), hashlib.sha256).hexdigest()


ACCOUNT_KEYS = {"maker", "taker", "account", "accounts", "owner", "seller", "buyer", "killer", "victim", "players",
                "members", "friends", "rows", "leaderboard"}


def _decoded(x, parent=None):
    """(parent key, dict) for every dict reachable from x, including inside strings that hold JSON (tool results,
    recorded reads)."""
    if isinstance(x, dict):
        yield parent, x
        for k, v in x.items():
            yield from _decoded(v, k)
    elif isinstance(x, list):
        for v in x:
            yield from _decoded(v, parent)
    elif isinstance(x, str) and x.lstrip()[:1] in "{[":
        try:
            yield from _decoded(json.loads(x), parent)
        except ValueError:
            pass


def third_parties(spec):
    """{(index, name)} of recorded player accounts that are not the drill's own: an {index, name} record under an
    account-like key (maker, owner, account, ...) or one carrying a 40-hex account id. Kamis (64-hex ids) and items
    are not accounts."""
    own = spec["state"]["account"]["index"]
    return {(d["index"], d["name"]) for k, d in _decoded(spec)
            if isinstance(d.get("index"), int) and isinstance(d.get("name"), str) and d["index"] != own
            and (k in ACCOUNT_KEYS or (isinstance(d.get("id"), str) and ADDR.fullmatch(d["id"])))}


B = r"(?:(?<=\\[nrt])|(?<![A-Za-z0-9_]))"  # a word start, also right after an escaped newline or tab
E = r"(?![A-Za-z0-9_])"
CONTEXT = r"(?i:acct|account|player|user|seller|buyer|maker|taker|owner|idx)"
# Never a name in free text, whoever chose it (review rc6 F1): harness account labels and tool words, game words, and
# common tech words. Numerals and tokens of 2 characters or fewer are never names either (real_name).
VOCAB = {"main", "operator", "owner", "default", "alt", "account", "accounts", "player", "players", "user", "users",
         "agent", "agents", "bot", "admin", "test", "tester", "null", "none", "true", "false", "yes", "no", "ok",
         "windows", "linux", "mac", "macos", "server", "client", "api", "json", "rpc", "error", "success", "status",
         "data", "result", "session", "wallet", "token", "tokens", "gas", "eth", "ether", "pool", "shop", "market",
         "trade", "listing", "seller", "buyer", "maker", "taker", "kami", "kamis", "musu", "item", "items", "quest",
         "quests", "room", "rooms", "node", "nodes", "level", "harvest", "mina", "kamigotchi", "initia", "yominet",
         "vipp", "obol", "onyx", "kamiswap", "palaestra", "harness", "lens", "steel", "master", "time", "day", "night"}


@functools.lru_cache(maxsize=1)
def _world_vocab():
    """Game words: every word of an item, node or quest name, and every whole name or title."""
    from kami_drills.spec.rules import WORLD
    names = [v.get("name") or v.get("title") or "" for sec in ("items", "nodes", "quests") for v in WORLD[sec].values()]
    return frozenset({w.lower() for n in names for w in re.findall(r"[A-Za-z0-9'-]+", n)} | {n.lower() for n in names})


def real_name(name):
    """A token that can be a player's name in free text: >= 3 characters, a letter in it, not a numeral or hex
    literal ("0x00"), and no harness, game or tech word, nor a quest, item or node name."""
    return (len(name) >= 3 and bool(re.search(r"[A-Za-z]", name)) and not re.fullmatch(r"[\d\W_]+", name)
            and not re.fullmatch(r"0x[0-9a-fA-F]{0,15}", name) and not name.isdigit()
            and name.lower() not in VOCAB and name.lower() not in _world_vocab())


# The season's player names, built at mine time from the transcripts (a private input; nothing about them is
# committed: review rc6 F4). split.py sets it before pseudonymizing the bank; v1-derived specs leave it empty.
SEASON = {"anywhere": set(), "plain": set(), "agents": set()}
WORDS = Path("/usr/share/dict/words")  # web2: English words and first names; without it every name counts as plain


def harvest_players(run_dirs, agent_wallets):
    """{"anywhere", "plain", "agents"} name sets from a season's transcripts and snapshots. A player name is the name of
    a record with its account wallet (ownerAddress, a 40-hex id, a decimal 160-bit id) or under a trade-party key.
    Names whose wallet is an agent's are agents (kept). A player name that is an English word or first name (web2)
    is plain: replaced in name contexts only."""
    q, sep = r'\\*"', r'(?:\s|\\+n)*'
    with_id = re.compile(rf'{q}id{q}{sep}:{sep}{q}(0x[0-9a-fA-F]{{40}}|\d{{46,49}}){q},{sep}{q}index{q}{sep}:{sep}\d+,{sep}{q}name{q}{sep}:{sep}{q}([^"\\]+){q}')
    party = re.compile(rf'(?:maker|taker|seller|buyer|owner){q}{sep}:{sep}\{{{sep}{q}index{q}{sep}:{sep}\d+,{sep}{q}name{q}{sep}:{sep}{q}([^"\\]+){q}')
    wallets, parties = {}, set()

    def walk(o):
        if isinstance(o, dict):
            n, w, i = o.get("name"), None, o.get("id")
            if isinstance(o.get("ownerAddress"), str):
                w = o["ownerAddress"].lower()
            elif isinstance(i, str) and i.isdigit() and 2 ** 150 <= int(i) < 2 ** 160:
                w = f"0x{int(i):040x}"
            elif isinstance(i, str) and len(i) == 42 and i.startswith("0x"):
                w = i.lower()
            if isinstance(n, str) and n and w:
                wallets.setdefault(n, set()).add(w)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str) and o[:1] in "{[":
            try:
                walk(json.loads(o))
            except ValueError:
                pass
    for d in map(Path, run_dirs):
        for f in sorted(d.glob("transcripts/*.jsonl")) + sorted(d.glob("snapshots/*.json")):
            text = f.read_text(encoding="utf-8", errors="replace")
            for w, n in with_id.findall(text):
                wallets.setdefault(n, set()).add(w.lower() if w.startswith("0x") else f"0x{int(w):040x}")
            parties |= set(party.findall(text))
            for line in text.splitlines() if f.suffix == ".jsonl" else [text]:
                try:
                    walk(json.loads(line))
                except ValueError:
                    pass
    agents = {n for n, w in wallets.items() if w & agent_wallets}
    players = {n for n in ({n for n, w in wallets.items() if not w & agent_wallets} | parties) - agents if real_name(n)}
    words = {w.strip().lower() for w in WORDS.open(encoding="utf-8", errors="replace")} if WORDS.exists() else None
    plain = {n for n in players if words is None or n.lower() in words}
    return {"anywhere": players - plain, "plain": plain, "agents": agents}


def season_names(blob):
    """The season's player names that occur in the text."""
    return {n for n in SEASON["anywhere"] | SEASON["plain"] if n in blob or json.dumps(n)[1:-1] in blob}


def distinctive(name):
    """Whether a name is replaced anywhere it stands as a whole word: a season name that is no English word or first
    name, or any other name (v1-era records, the drill's own name) that looks like no word: >= 4 characters, and a
    digit or symbol, an inner capital (ShadowFox) or >= 9 characters. A plain word ("today") is replaced in name
    contexts only."""
    if name in SEASON["plain"] or len(name) < 4 or not real_name(name):
        return False
    return name in SEASON["anywhere"] or bool(re.search(r"[^A-Za-z]", name)) or bool(re.search(r"[a-z][A-Z]", name)) or len(name) >= 9


def free_text(blob, names, key, own, anywhere=()):
    """Replace player names in free text. Every name is replaced next to an account word, with or without an index
    ("acct 3413 ShadowFox9", "(acct 807 \"today\")", "seller someseller"), and the index becomes the record alias (the
    drill's own index stays), or followed by a kami, listing or account ("Bramble: kamiIndex 21629"). A distinctive name
    is also replaced anywhere it stands as a whole word; a plain word ("today", "Bramble") only in those contexts."""
    names = [n for n in names if real_name(n) and (n in blob or json.dumps(n)[1:-1] in blob)]  # gone, or never there
    if not names:
        return blob
    alias = {n: f"player-{_mac(key, 'name|' + n)[:6]}" for n in names}
    canon = lambda t: re.sub(r"\\+u([0-9a-fA-F]{4})", lambda u: chr(int(u.group(1), 16)), t)
    alt = lambda ns: "|".join("".join(_char(ch) for ch in n) for n in ns)

    def ctx(m):
        num = m.group("num")
        if num:
            digits = num.lstrip("#")
            new = digits if int(digits) == own else str(10 ** 7 + int(_mac(key, f"idx|{digits}")[:6], 16))
            num = num[:len(num) - len(digits)] + new
        return m.group("w") + m.group("s1") + (num or "") + (m.group("s2") or "") + alias.get(canon(m.group("n")), m.group(0))
    sp = r"(?:\\+|[^A-Za-z0-9_:\\]){1,6}"
    blob = re.sub(rf"(?P<w>\b{CONTEXT}\b)(?P<s1>{sp})(?:(?P<num>#?\d+)(?P<s2>{sp}))?{B}(?P<n>{alt(names)}){E}", ctx, blob)
    # ... or followed by what it sells or owns: "Bramble: kamiIndex 21629", "(Bramble, kami 21629)"
    blob = re.sub(rf'{B}(?P<n>{alt(names)})(?=[^A-Za-z0-9_"\\]{{1,4}}(?:kami|listing|acct|account|#\d))',
                  lambda m: alias.get(canon(m.group("n")), m.group(0)), blob)
    if anywhere := [n for n in names if distinctive(n) or n in anywhere]:  # the drill's own name: the owner's handle
        blob = re.sub(rf"{B}(?P<n>{alt(anywhere)}){E}", lambda m: alias.get(canon(m.group("n")), m.group(0)), blob)
    return blob


def _char(ch):
    """One name character as it may appear in the blob: raw, or JSON-escaped (\\u00f1) at any escaping depth."""
    plain, esc = re.escape(ch), json.dumps(ch)[1:-1]
    return plain if esc == ch else f"(?:{plain}|{re.escape(esc).replace(re.escape(chr(92)), chr(92) * 2 + '+')})"


def pseudonymize(spec, key, allow=None):
    """(pseudonymized spec, {"addresses": n, "accounts": n})."""
    allow = allowlist() if allow is None else allow
    blob = json.dumps(spec, ensure_ascii=False)
    q, sep = r'\\*"', r'(?:\s|\\+n)*'  # a quote, and whitespace or escaped newlines, at any escaping depth
    accounts = third_parties(spec)
    # records inside truncated tool results do not parse as JSON: find the same shape in the text as well
    keys = "|".join(sorted(ACCOUNT_KEYS))
    ident = rf'(?:{q}id{q}{sep}:{sep}{q}[^"\\]*{q},{sep})?'  # lens records may lead with their "id"
    accounts |= {(int(i), n) for i, n in re.findall(
        rf'(?:{keys}){q}{sep}:{sep}\{{{sep}{ident}{q}index{q}{sep}:{sep}(\d+),{sep}{q}name{q}{sep}:{sep}{q}([^"\\]+){q}', blob)
        if int(i) != spec["state"]["account"]["index"]}
    # ... and any record that leads with a 40-hex account id, under any key (an "accounts" list item, a lens_account body)
    accounts |= {(int(i), n) for i, n in re.findall(
        rf'\{{{sep}{q}id{q}{sep}:{sep}{q}0x[0-9a-fA-F]{{36,40}}{q},{sep}{q}index{q}{sep}:{sep}(\d+),{sep}{q}name{q}{sep}:{sep}{q}([^"\\]+){q}', blob)
        if int(i) != spec["state"]["account"]["index"]}
    accounts = {(i, n) for i, n in accounts if not ALIAS.fullmatch(n)}  # already a pseudonym
    own = spec["state"]["account"]["index"]
    # the drill's own account record, not a registry/quest/item record that happens to share its index (quest 1 vs
    # account 1): those carry a description, a type or a rarity
    own_names = {n for i, n, rest in re.findall(rf'index{q}{sep}:{sep}(\d+),{sep}{q}name{q}{sep}:{sep}{q}([^"\\]+){q}([^{{}}]{{0,300}})', blob)
                 if int(i) == own and n not in SEASON["agents"] and not ALIAS.fullmatch(n) and real_name(n)
                 and not re.search(r"description|\btype\b|rarity|objectives", rest)}
    kami_names = {d["name"] for k, d in _decoded(spec) if isinstance(d.get("name"), str)
                  and (k in ("killer", "victim", "attacker") or (isinstance(d.get("id"), str) and len(d["id"]) == 66))}
    rename = {(idx, name) for idx, name in accounts | {(own, n) for n in own_names}}

    def record(m):  # the lens shape: "index": N, "name": "X" (any escaping depth), one pass over the text
        idx, raw = int(m.group(2)), m.group(4)
        name = re.sub(r"\\+u([0-9a-fA-F]{4})", lambda u: chr(int(u.group(1), 16)), raw)
        if (idx, name) not in rename:
            return m.group(0)
        new_idx = idx if idx == own else 10 ** 7 + int(_mac(key, f"idx|{idx}")[:6], 16)  # the drill's own index stays
        return m.group(1) + str(new_idx) + m.group(3) + f"player-{_mac(key, 'name|' + name)[:6]}" + m.group(5)
    blob = re.sub(rf'(index{q}{sep}:{sep})(\d+)(,{sep}{q}name{q}{sep}:{sep}{q})((?:[^"\\]|\\+u[0-9a-fA-F]{{4}})+)({q})', record, blob)
    # free text (notes, prose, truncated results): the record names above, the drill's own name, and any harvested S1
    # player name, wherever they are a player's name (free_text)
    names = {nm for _, nm in accounts if nm not in kami_names} | own_names | season_names(blob)
    # harness refusal templates (world.native_text) are never free text to rename: hold them out of the pass
    held = json.loads(blob)
    native = (held.get("world") or {}).pop("native_text", None)
    blob = json.dumps(held, ensure_ascii=False)
    blob = free_text(blob, sorted(names - SEASON["agents"], key=lambda x: (-len(x), x)), key, own,
                     anywhere={n for n in own_names if len(n) >= 4 and not n.isdigit()})
    if native is not None:
        held = json.loads(blob)
        held["world"]["native_text"] = native
        blob = json.dumps(held, ensure_ascii=False)
    n = 0

    def addr(m):
        nonlocal n
        a = m.group(0)
        if a.lower() in allow or a.startswith(MARK):
            return a
        n += 1
        return MARK + _mac(key, "addr|" + a.lower())[:32]
    blob = ADDR.sub(addr, blob)

    def words(m):  # an address as an ABI word in tx data or a log topic: 0{24} + address -> 0{24} + its 0x form's alias
        nonlocal n
        h = m.group(1)
        off = 8 if (len(h) - 8) % 64 == 0 else 0 if len(h) % 64 == 0 else None  # after a 4-byte selector, or bare words
        if off is None:
            return m.group(0)
        for i in range(off, len(h), 64):
            w = h[i + 24:i + 64].lower()
            if h[i:i + 24] == "0" * 24 and padded_address(w) and "0x" + w not in allow:
                n += 1
                h = h[:i + 24] + _mac(key, "addr|0x" + w)[:32].rjust(40, "0") + h[i + 64:]
        return "0x" + h
    blob = WORDS_HEX.sub(words, blob)

    def trunc(m):
        nonlocal n
        a = m.group(0).lower()
        if a.startswith(MARK) or any(x.startswith(a) for x in allow):
            return m.group(0)
        n += 1
        return MARK + _mac(key, "trunc|" + a)[:len(a) - 2 - len(MARK) + 2]
    blob = TRUNC.sub(trunc, blob)
    # an account id cut short by a truncated result ("id": "0x<16-39 hex>[truncated"): review rc6 F6
    blob = re.sub(rf'(id{q}{sep}:{sep}{q})(0x[0-9a-fA-F]{{16,35}})(?![0-9a-fA-F])', lambda m: m.group(1) + trunc(re.match(".*", m.group(2))), blob)

    def dec(m):
        nonlocal n
        v = int(m.group(0))
        if not DEC_LO <= v < DEC_HI or f"0x{v:040x}" in allow:
            return m.group(0)
        n += 1
        return str(int(MARK + _mac(key, f"addr|0x{v:040x}")[:32], 16))  # the hex address's own alias, as an integer
    blob = DEC.sub(dec, blob)

    def dec_cut(m):  # -> a <= 39-digit integer (a 128-bit alias), so a second pass leaves it alone
        nonlocal n
        d = m.group(0)
        if any(str(int(a, 16)).startswith(d) for a in allow):
            return d
        n += 1
        return str(int(_mac(key, "dtrunc|" + d)[:32], 16))
    blob = DEC_CUT.sub(dec_cut, blob)
    return json.loads(blob), {"addresses": n, "accounts": len(accounts)}


def run(paths, key, out, check):
    """Pseudonymize every spec under `paths` into `out`; returns [(id, stats or refusal)]."""
    from kami_drills import spec as sp
    from kami_drills.spec import perturb
    from kami_drills.spec.from_miner import stamp
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    allow, report = allowlist(), []
    for s in sp.load(paths):
        p, stats = pseudonymize(s, key, allow)
        stamp(p)
        if diff := perturb.check_equivalent(s, p, check):
            report.append((s["id"], {"refused": diff}))
            continue
        (out / f"{s['id']}.json").write_text(json.dumps(p, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
        report.append((s["id"], stats))
    return report
