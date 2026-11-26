"""Map completion gifts: one activity per map, once per character (owner decision 11, 2026-11-26).

The wiki stores each map's completion gift twice: on the zone page ('Rewarded by: <zone>',
location type Zone) and on the generic Map completion page ('Rewarded by: Map completion').
The walk turns both into activities: ``wiki:<zone>`` (no time, no map, expansion core) and one
``wiki:map_completion`` that yields every gift at once. ``apply`` drops the generic one and
gives each zone activity of ``config/map_completion.json`` its name, time, map, waypoint,
expansion and ``per_character`` (a character completes a map once; another character can do it
again). The Path of Fire gifts are bought after map completion, not rewarded: they keep their
vendor sources only.
"""
from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

import ls3_currencies as ls3
import wiki_sources as ws

CONFIG = json.loads((Path(__file__).resolve().parent.parent / "config" / "map_completion.json").read_text(encoding="utf-8"))
WIKI = "https://wiki.guildwars2.com/wiki/"
GENERIC = "wiki:map_completion"


def apply(final: dict[str, dict], nodes: dict[str, dict], maps: list[dict]) -> tuple[dict, list[str]]:
    """Returns (stats, warnings)."""
    warn: list[str] = []
    dropped = 0
    if final.pop(GENERIC, None) is not None:
        for n in nodes.values():
            before = len(n["sources"])
            n["sources"] = [s for s in n["sources"] if s.get("activity_id") != GENERIC]
            dropped += before - len(n["sources"])
    by_name = {n["name"]: n for n in nodes.values() if n["id"].startswith("item:")}
    made = 0
    for zone, c in CONFIG["maps"].items():
        aid = "wiki:" + ws.slug(zone)
        gift = by_name.get(c["gift"])
        if gift is None:
            continue   # no tree of this pack needs the gift
        a = final.get(aid)
        if a is None or not any(y["node_id"] == gift["id"] for y in a.get("yields", [])):
            warn.append(f"map completion: {zone} does not reward {c['gift']} on the wiki; left out")
            continue
        a["name"] = f"Map completion: {zone}"
        a["mode"] = "pve"
        a["expansion"] = c["expansion"]
        a["est_minutes"] = CONFIG["minutes"]
        a["map_ids"] = [c["map_id"]]
        a["per_character"] = True
        a["yields"] = [y for y in a["yields"] if y["node_id"] == gift["id"]]
        g = {"text": f"Complete the map {zone} on a character that has not completed it: {c['gift']} "
                     f"(once per character; about {CONFIG['minutes']} min, owner's estimate, DRF refines it)",
             "wiki_url": WIKI + urllib.parse.quote(zone.replace(" ", "_"))}
        wp = ls3._waypoint(maps, c["map_id"])
        if wp:
            g["waypoint_chat_code"] = wp
        a["guidance"] = g
        a["description"] = (f"Map completion of {zone} gives {c['gift']}, once per character: "
                            f"another character can complete the map again for another gift.")
        made += 1
    return {"activities": made, "generic_sources_dropped": dropped}, warn
