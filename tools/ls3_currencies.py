"""LS3 map currencies: one daily gathering activity per map, from the owner's numbers.

Blood Ruby, Petrified Wood, Fresh Winterberry, Jade Shard, Fire Orchid Blossom and Orrian
Pearl have no [fast] activity and no timed wiki event, so the pack had no daily source for
them (Aurora left ~795 units to the player in November). ``config/ls3_currencies.json``
holds the owner's per-map run (15 min, once per character per day, low end of the average
yield). Each becomes an activity ``ls3:gather:<map>`` with the map id, the nearest waypoint to the
map centre (official API map index) and a wiki link; its yield is attached only when the
item is a node of this pack's trees, like every catalog activity.

Manfred Njallson's weekly strongboxes (50 Magnetite Shard + 4 Legendary Insight, 6 a week) are
vendor sources written straight onto the content item (``manfred_sources``); the Season 4 chest
is pick-one, so its six offers share one weekly limit through ``cap_group``.

Heart vendors ("other daily sources") need the vendor's cost; they are added as vendor
sources by ``vendor_sources`` once the probe replies are in (see config ``heart_vendors``),
not as activities, because a vendor purchase has no run time.
"""
from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

CONFIG = json.loads((Path(__file__).resolve().parent.parent / "config" / "ls3_currencies.json").read_text(encoding="utf-8"))
WIKI = "https://wiki.guildwars2.com/wiki/"


def _waypoint(maps: list[dict], map_id: int) -> str | None:
    m = next((x for x in maps if x["id"] == map_id), None)
    if not m or not m.get("waypoints"):
        return None
    cx, cy = m["centre"]
    return min(m["waypoints"], key=lambda w: ((w[1] - cx) ** 2 + (w[2] - cy) ** 2, w[3]))[3]


def activities(maps: list[dict], node_ids: set[str]) -> list[dict]:
    out = []
    for name, c in CONFIG["maps"].items():
        node = f"item:{c['item']}"
        if node not in node_ids:
            continue   # no tree of this pack needs it
        g = {"text": f"Gather {c['item_name']} in {name}: {c['gather']} per character per day "
                     f"({CONFIG['run_minutes']} min; owner's average, DRF refines it)",
             "wiki_url": WIKI + urllib.parse.quote(c["place"].replace(" ", "_"))}
        wp = _waypoint(maps, c["map_id"])
        if wp:
            g["waypoint_chat_code"] = wp
        out.append({"id": "ls3:gather:" + c["place"].lower().replace("'", "").replace(" ", "_"),
                    "name": f"Gather {c['item_name']}: {name}", "mode": "pve", "expansion": "hot",
                    "est_minutes": CONFIG["run_minutes"], "max_runs_per_day": 1, "map_ids": [c["map_id"]],
                    "yields": [{"node_id": node, "count": c["gather"]}], "guidance": g})
    return sorted(out, key=lambda a: a["id"])


def apply(final: dict[str, dict], nodes: dict[str, dict], maps: list[dict]) -> int:
    """Adds the gathering activities to ``final`` and an activity source to each yielded node."""
    added = activities(maps, set(nodes))
    for a in added:
        final[a["id"]] = a
        for y in a["yields"]:
            srcs = nodes[y["node_id"]]["sources"]
            srcs[:] = [x for x in srcs if x["kind"] != "other"]
            srcs.append({"kind": "activity", "inputs": [], "activity_id": a["id"]})
    return len(added)


def manfred_sources(nodes: dict[str, dict]) -> int:
    """Vendor sources for Manfred's boxes on the item nodes this pack has; returns how many were added."""
    m = CONFIG["manfred"]
    by_name = {n["name"]: n for n in nodes.values() if n["id"].startswith("item:")}
    added = 0
    for o in m["offers"]:
        for name in o.get("items") or [o["item"]]:
            node = by_name.get(name)
            if not node:
                continue
            src = {"kind": "vendor", "inputs": sorted(m["cost"], key=lambda i: i["node_id"]),
                   "output_count": o["count"], "weekly_cap": m["weekly_cap"]}
            if o.get("pick_one"):
                src["cap_group"] = f"{m['npc']}: {o['box']}"   # one box, one pick: the six offers share the limit
            src["vendors"] = [{"npc": m["npc"], "locations": m["locations"], "requires": []}]
            if src not in node["sources"]:
                node["sources"].append(src)
                added += 1
    return added
