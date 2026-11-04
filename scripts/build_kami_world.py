"""Regenerate kami_drills/spec/kami_world.json (the kami-rules library's static world) from a kamigotchi-gdd checkout.

    uv run python scripts/build_kami_world.py <path/to/kamigotchi-gdd>

Node level caps, affinities and yield items come from the gdd's catalogs/rooms/nodes.csv, not the kami-harness copy: the harness catalog leaves
node 55's cap blank, the gdd (mechanics/world/nodes.md "Level caps") lists 55 -> 15. Room adjacency follows
kami-harness executor/rooms_graph.py: rooms one step apart on x or y on the same z plane, plus the Exits column, both
directions, In Game rooms only (room gates are not modelled).
"""
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).parents[1] / "kami_drills" / "spec" / "kami_world.json"


def rows(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def main(gdd):
    gdd = Path(gdd)
    sha = subprocess.run(["git", "-C", str(gdd), "log", "-1", "--format=%h"], capture_output=True, text=True).stdout.strip()
    items = {}
    for r in rows(gdd / "catalogs/items/items.csv"):
        eff = r["Effects"] or ""
        hp = re.search(r"HP\+(\d+)", eff)
        if r["Type"] in ("Food", "Revive", "Potion", "Consumable") and (hp or "STATE-RESTING" in eff or r["Type"] == "Food"):
            items[r["Index"]] = {"name": r["Name"], "type": r["Type"].upper(), "for": (r["For"] or "").upper(),
                                 "hp": int(hp.group(1)) if hp else 0, "revives": "STATE-RESTING" in eff}
    nodes = {r["Index"]: {"name": r["Name"], "level_cap": int(r["Level Limit"]) if r["Level Limit"] else None,
                          "affinity": [a.strip().upper() for a in re.split(r"[,-]", r["Affinity"] or "") if a.strip()],
                          "yield_item": int(r["YieldIndex"]) if r["YieldIndex"] else None}
             for r in rows(gdd / "catalogs/rooms/nodes.csv") if r["Status"] == "In Game"}
    # every room gates.csv lists, plus 59 (the live world gates Black Pool instead of 19: gdd catalogs/rooms/README)
    gated = sorted({int(r["Room Index"]) for r in rows(gdd / "catalogs/rooms/gates.csv")} | {59})
    # what an objective ties to: the nodes it must be done at (harvest time, scavenge claims, droptable items) and the
    # items it consumes or counts. The miner rejects a node or purchase choice an accepted quest explains.
    objs = {r["Description"]: r for r in rows(gdd / "catalogs/quests/objectives.csv")}
    drops = {r["Name"]: [int(i) for i in r["Indices"].split(",") if i.strip()] for r in rows(gdd / "catalogs/rooms/scavenge-droptables.csv")}
    node_drops = {int(i): drops.get(n["Drops"], []) for n in rows(gdd / "catalogs/rooms/nodes.csv") if n["Status"] == "In Game"
                  for i in [n["Index"]]}

    def ties(text):
        tn, ti, hunt, anywhere, shop = set(), set(), False, False, False
        for d in (x.strip() for x in (text or "").split(",")):
            o = objs.get(d) or {}
            t, idx = o.get("Type", ""), int(o["Index"]) if (o.get("Index") or "").strip().isdigit() else None
            if t in ("HARVEST_TIME", "HARVEST_TIME_MOONSIDE", "SCAV_CLAIM_NODE") and idx is not None:
                tn.add(idx)
            elif t.startswith("SCAV_CLAIM_AFFINITY_"):
                tn |= {int(k) for k, v in nodes.items() if t.removeprefix("SCAV_CLAIM_AFFINITY_") in v["affinity"]}
            elif t == "DROPTABLE_ITEM_TOTAL" and idx is not None:
                tn |= {k for k, v in node_drops.items() if idx in v}
                ti.add(idx)
            elif t.startswith("ITEM_") and idx is not None:
                ti.add(idx)
            hunt = hunt or t == "LIQUIDATE_TOTAL"
            anywhere = anywhere or (t == "HARVEST_TIME" and idx is None)
            shop = shop or t == "LISTING_BUY_TOTAL"
        return {"nodes": sorted(tn), "items": sorted(ti), **({"liquidate": True} if hunt else {}),
                **({"harvest_any_node": True} if anywhere else {}), **({"shop": True} if shop else {})}
    # item rewards the rules library pays on completion: only items it models (foods and revives), by name
    norm = lambda s: re.sub(r"[\"“”‘’']", "", s).replace("’", "'").strip().lower()
    by_name = {norm(v["name"]): int(k) for k, v in items.items()}

    def rewards(text):
        out = {}
        for m in re.finditer(r"(\d+)x ([^,]+)", text or ""):
            if (i := by_name.get(norm(m.group(2)))) is not None:
                out[str(i)] = out.get(str(i), 0) + int(m.group(1))
        return {"rewards": out} if out else {}
    quests = {r["Index"]: {"title": r["Title"], "has_objectives": bool(r["Objectives"].strip()), **ties(r["Objectives"]),
                           **rewards(r["Rewards"])}
              for r in rows(gdd / "catalogs/quests/quests.csv") if r["Status"] == "In Game"}
    rooms = {int(r["Index"]): r for r in rows(gdd / "catalogs/rooms/rooms.csv") if r["Status"] == "In Game" and r["Index"] != "0"}
    pos = {i: (int(r["X"]), int(r["Y"]), int(r["Z"])) for i, r in rooms.items()}
    adj = {i: set() for i in rooms}
    for a in rooms:
        for b in rooms:
            (x1, y1, z1), (x2, y2, z2) = pos[a], pos[b]
            if a != b and z1 == z2 and abs(x1 - x2) + abs(y1 - y2) == 1:
                adj[a].add(b)
        for e in re.findall(r"\d+", rooms[a]["Exits"] or ""):
            if int(e) in rooms and int(e) != a:
                adj[a].add(int(e))
                adj[int(e)].add(a)
    out = {"source": f"kamigotchi-gdd {sha}: catalogs/items/items.csv (Food/Revive/Potion rows), catalogs/rooms/nodes.csv "
                     "(level caps, affinities, yield items), catalogs/quests/quests.csv + objectives.csv (objectives and the nodes and items they tie to; food and revive rewards), catalogs/rooms/rooms.csv (adjacency as "
                     "kami-harness rooms_graph.py), catalogs/rooms/gates.csv (gated rooms); scripts/build_kami_world.py",
           "items": items, "nodes": nodes, "quests": quests, "rooms": {str(a): sorted(adj[a]) for a in sorted(adj)},
           "gated_rooms": gated}
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(f"{OUT}: {len(items)} items, {len(nodes)} nodes ({sum(n['level_cap'] is not None for n in nodes.values())} capped), {len(adj)} rooms")


if __name__ == "__main__":
    main(sys.argv[1])
