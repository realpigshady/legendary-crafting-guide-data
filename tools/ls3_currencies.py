"""LS3 map currencies: one daily gathering activity per map, from the owner's numbers.

Blood Ruby, Petrified Wood, Fresh Winterberry, Jade Shard, Fire Orchid Blossom and Orrian
Pearl have no [fast] activity and no timed wiki event, so the pack had no daily source for
them (Aurora left ~795 units to the player in November). ``config/ls3_currencies.json``
holds the owner's per-map run (15 min, once per character per day, low end of the average
yield). Each becomes an activity ``ls3:gather:<map>`` with the map id, the nearest waypoint to the
map centre (official API map index) and a wiki link; its yield is attached only when the
item is a node of this pack's trees, like every catalog activity.

Manfred Njallson's weekly strongboxes and the heart vendors' material bundles are vendor sources
written straight onto the content item, each with an ``offer`` naming what is bought (the box or
bundle, boxes per purchase, items per box): Bundle of Winterberries holds 5 Fresh Winterberries,
Bundle of Jade Shards 3 Jade Shards (tester feedback round 1, 2026-11-26). Vendors, costs, limits
and locations come from the wiki's vendor rows for the box, contents from its 'Contains item'
rows; config/ls3_currencies.json only names the boxes. The Season 4 chest is pick-one, so its six
offers share one weekly limit through ``cap_group``. Heart vendors get one source per vendor so
each keeps its own daily limit (1 when the wiki row has none; owner: once a day, one character);
they are not activities: a vendor purchase has no run time. ``config/legendary_insights.json`` adds Legendary Insight (currency 70)
as a weekly yield on the raid wings and unified strikes the catalog already has, plus two weekly
extras (matchmaking milestones, daily raid bounties).
"""
from __future__ import annotations

import json
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wiki_sources as ws  # noqa: E402

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


def _contents(smw, offer: str, item: str) -> int | None:
    """Items per box/bundle: the wiki's 'Contains item' row of ``item`` in ``offer`` (sure or picked)."""
    for r in smw.get("contained", [item]).get(item, []):
        box = (r.get("Contained in") or [""])[0]
        chance = str((r.get("Has contained item chance") or [""])[0]).lower()
        if box == offer and chance in ("guaranteed", "choice") and not ws._true(r.get("Is historical")):
            return ws._int(r.get("Has contained item quantity"))
    return None


def _offer_sources(smw, offer: str, item: str, currencies: dict[str, int], warn: list[str]) -> list[dict]:
    """One vendor source per wiki vendor row that sells ``offer`` (a box or bundle holding ``item``).
    output_count = boxes per purchase x items per box; ``offer`` says what is bought:
    {name, count (boxes per purchase), contains (items per box)}."""
    per_box = _contents(smw, offer, item)
    if not per_box:
        warn.append(f"ls3: no sure contents of {item} in {offer} on the wiki; offer left out")
        return []
    out = []
    for r in smw.get("vendor", [offer]).get(offer, []):
        srcs, cost_items = ws.vendor_sources(offer, [r], currencies)
        if cost_items:
            warn.append(f"ls3: {offer} costs items ({', '.join(cost_items)}); offer left out")
            continue
        for s in srcs:
            boxes = s["output_count"]
            s["output_count"] = boxes * per_box
            s["offer"] = {"name": offer, "count": boxes, "contains": per_box}
            out.append(s)
    if not out:
        warn.append(f"ls3: no wiki vendor sells {offer}; offer left out")
    return out


def manfred_sources(nodes: dict[str, dict], smw, currencies: dict[str, int], warn: list[str] | None = None) -> int:
    """Vendor sources for Manfred's boxes on the item nodes this pack has (cost, limit and contents
    from the wiki); returns how many were added."""
    m = CONFIG["manfred"]
    warn = [] if warn is None else warn
    by_name = {n["name"]: n for n in nodes.values() if n["id"].startswith("item:")}
    added = 0
    for o in m["offers"]:
        for name in o.get("items") or [o["item"]]:
            node = by_name.get(name)
            if not node:
                continue
            for src in _offer_sources(smw, o["box"], name, currencies, warn):
                if [v["npc"] for v in src["vendors"]] != [m["npc"]]:
                    continue   # another vendor sells the same box: not this config's offer
                if o.get("pick_one"):
                    src["cap_group"] = f"{m['npc']}: {o['box']}"   # one box, one pick: the six offers share the limit
                    src["offer"]["choice"] = True                  # the player picks this item when opening it
                if src not in node["sources"]:
                    node["sources"].append(src)
                    added += 1
    return added


def heart_vendor_sources(nodes: dict[str, dict], smw, currencies: dict[str, int], warn: list[str] | None = None) -> int:
    """One vendor source per heart vendor that sells the item's bundle (wiki rows); returns how many were added."""
    h = CONFIG["heart_vendors"]
    warn = [] if warn is None else warn
    zone = {c["item_name"]: c["place"] for c in CONFIG["maps"].values()}
    by_name = {n["name"]: n for n in nodes.values() if n["id"].startswith("item:")}
    added = 0
    for item, offer in h["offers"].items():
        node = by_name.get(item)
        if not node:
            continue
        for src in _offer_sources(smw, offer, item, currencies, warn):
            if not (src.get("daily_cap") or src.get("weekly_cap") or src.get("max_units")):
                src["daily_cap"] = h["daily_cap_default"]   # the wiki row leaves the limit off (owner: once a day)
            for v in src["vendors"]:
                if zone.get(item) and zone[item] not in v["locations"]:
                    v["locations"] = v["locations"] + [zone[item]]   # the map, so a waypoint resolves
            if len(src["vendors"]) > 1:   # same price, caps and quantity: still one daily limit per vendor
                for v in src["vendors"]:
                    one = {**src, "vendors": [v]}
                    one.pop("cap_group", None)
                    if one not in node["sources"]:
                        node["sources"].append(one)
                        added += 1
                continue
            if src not in node["sources"]:
                node["sources"].append(src)
                added += 1
    return added


INSIGHTS = json.loads((Path(__file__).resolve().parent.parent / "config" / "legendary_insights.json").read_text(encoding="utf-8"))


def insight_sources(final: dict[str, dict], nodes: dict[str, dict]) -> int:
    """Legendary Insight yields on raid wings / strikes (once a week) and two untimed weekly extras."""
    cur = INSIGHTS["currency"]
    if cur not in nodes:
        return 0
    n = 0

    def link(act_id: str) -> None:
        srcs = nodes[cur]["sources"]
        srcs[:] = [x for x in srcs if x["kind"] != "other"]
        s = {"kind": "activity", "inputs": [], "activity_id": act_id}
        if s not in srcs:
            srcs.append(s)

    for e in INSIGHTS["wings"] + INSIGHTS["strikes"]:
        a = final.get(e["activity"])
        if not a:
            continue
        a["yields"] = [y for y in a.get("yields", []) if y["node_id"] != cur] + [{"node_id": cur, "count": e["count"]}]
        a["max_runs_per_week"] = 1
        link(a["id"])
        n += 1
    for e in INSIGHTS["weekly_extras"]:
        final[e["id"]] = {"id": e["id"], "name": e["name"], "mode": "pve", "expansion": "hot", "max_runs_per_week": 1,
                          "yields": [{"node_id": cur, "count": e["count"]}],
                          "guidance": {"text": e["name"] + ": " + str(e["count"]) + " Legendary Insight a week (owner's sheet)"}}
        link(e["id"])
        n += 1
    return n
