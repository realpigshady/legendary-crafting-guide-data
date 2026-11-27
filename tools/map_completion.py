"""Map completion gifts: one activity per map, once per character (owner decision 11, 2026-11-26).

The wiki stores each map's completion gift twice: on the zone page ('Rewarded by: <zone>',
location type Zone) and on the generic Map completion page ('Rewarded by: Map completion').
The walk turns both into activities: ``wiki:<zone>`` (no time, no map, expansion core) and one
``wiki:map_completion`` that yields every gift at once. ``apply`` drops the generic one and
gives each zone activity of ``config/map_completion.json`` its name, time, map, waypoint,
expansion and ``per_character`` (a character completes a map once; another character can do it
again). The Path of Fire gifts are bought after map completion, not rewarded: they keep their
vendor sources only. The Illuminating <map> lantern achievements (``lanterns`` in the config) become
repeatable activities that give the map's gift and open after that map's completion (``after_activity``).
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
    lanterns = _lanterns(final, by_name, maps, warn)
    return {"activities": made, "generic_sources_dropped": dropped, "lantern_activities": lanterns}, warn


def _lanterns(final: dict[str, dict], by_name: dict[str, dict], maps: list[dict], warn: list[str]) -> int:
    """The Illuminating <map> lantern achievements (owner 2026-11-27, SPEC §6.7 "Map completion, then the
    repeatable"): one repeatable activity each, yielding the map's gift (Chest of the Obscure holds it), opened by
    that map's completion activity (``after_activity``) where the map has one."""
    made = 0
    minutes = CONFIG["lantern_minutes"]
    for name, c in CONFIG["lanterns"].items():
        gift = by_name.get(c["gift"])
        if gift is None:
            continue   # no tree of this pack needs the gift
        aid = "wiki:" + ws.slug(name)
        where = c.get("areas") or (c["after"] or "the map")
        a = {"id": aid, "name": name, "mode": "pve", "expansion": c["expansion"], "est_minutes": minutes,
             "map_ids": [c["map_id"]], "repeatable": True,
             "yields": [{"node_id": gift["id"], "count": 1}]}
        if c.get("after"):
            opener = "wiki:" + ws.slug(c["after"])
            if opener not in final or not final[opener].get("per_character"):
                warn.append(f"lanterns: {name} opens after {c['after']}, which has no map completion activity; left out")
                continue
            a["after_activity"] = opener
        first = f"after completing the map {c['after']} once, " if c.get("after") else ""
        g = {"text": f"Light the {c['lanterns']} lanterns in {where} for {name} ({first}repeatable): Chest of the Obscure "
                     f"with {c['gift']} (about {minutes} min, owner's estimate, DRF refines it)",
             "wiki_url": WIKI + urllib.parse.quote(name.replace(" ", "_"))}
        wp = ls3._waypoint(maps, c["map_id"])
        if wp:
            g["waypoint_chat_code"] = wp
        a["guidance"] = g
        a["description"] = (f"Repeatable achievement: light {c['lanterns']} lanterns for a Chest of the Obscure with "
                            f"{c['gift']}." + (f" Opens after the map completion of {c['after']}." if c.get("after") else ""))
        final[aid] = a
        src = {"kind": "activity", "inputs": [], "activity_id": aid}
        if not any(s.get("activity_id") == aid for s in gift["sources"]):
            gift["sources"].append(src)
        made += 1
    return made
