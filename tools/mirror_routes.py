"""Magic Mirror routes (Data 4o (1b)): one route activity per Castora map (owner 2026-11-13).

Numbers and chest rules: ``config/mirror_routes.json`` (owner; routes by 12Tables). Positions on the
images: ``config/mirror_route_geometry.json`` (``tools/mirror_maps_import.py`` for the full chest map,
``tools/mirror_route_import.py`` for the route). Images ship as release assets beside ``pack.json``
(``assets/mirror-maps/``, copied to the build output by ``copy_assets``). No wiki calls: the waypoint
chat codes and map ids come from the API map index.

Each activity ``voe:mirror:<map>``: ``max_runs_per_day`` 1 (every chest and keepsake box once a day per
account), yields = the route's guaranteed map currency, ``mirror_route`` = what the UI's mirror window
draws (schema: SPEC §5 "Magic Mirror routes").
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config" / "mirror_routes.json").read_text(encoding="utf-8"))
GEOMETRY = json.loads((ROOT / "config" / "mirror_route_geometry.json").read_text(encoding="utf-8"))["maps"]
ASSETS = ROOT / "assets" / "mirror-maps"
ICON = "waypoint-map-icon.png"


def _norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9]", "", s.lower())
    return s[:-1] if s.endswith("s") else s


def asset_names() -> list[str]:
    names = [ICON]
    for g in GEOMETRY.values():
        names += [g["route"]["image"], g["full_image"]]
    return names


def copy_assets(out: Path) -> list[str]:
    """Copies the route images, full chest maps and the waypoint icon to ``out`` (release assets). -> warnings."""
    warn = []
    for n in asset_names():
        src = ASSETS / n
        if src.exists():
            shutil.copyfile(src, out / n)
        else:
            warn.append(f"mirror routes: release asset {n} missing (assets/mirror-maps/): the UI shows no map image for it")
    return warn


def apply(final: dict[str, dict], nodes: dict[str, dict], maps: list[dict]) -> tuple[dict, list[str]]:
    warn: list[str] = []
    stats = {"activities": 0}
    by_name = {_norm(n["name"]): n for n in nodes.values()}
    for m in CONFIG["maps"]:
        geo = GEOMETRY.get(m["slug"], {}).get("route")
        node = by_name.get(_norm(m["currency"]))
        if geo is None or node is None:
            warn.append(f"mirror routes: {m['map']}: " + ("no route geometry" if geo is None else f"no pack node for {m['currency']}"))
            continue
        r = m["route"]
        if r["mirrors"] * 3 + r["medium_chests"] * 2 + r["large_chests"] * 3 != r["total"]:
            warn.append(f"mirror routes: {m['map']}: total {r['total']} is not mirrors x 3 + medium x 2 + large x 3")
        if r["keys_in"] < r["keys_out"]:
            warn.append(f"mirror routes: {m['map']}: the route spends more key charges than it earns")
        api = next((x for x in maps if x.get("id") == m["map_id"]), None)
        wps = {w[0]: w[3] for w in (api or {}).get("waypoints", [])}
        starts = [{"name": leg["start"], "chat_code": wps.get(leg["start"], leg["chat_code"]), "x": leg["x"], "y": leg["y"]}
                  for leg in geo["legs"]]
        g = GEOMETRY[m["slug"]]
        route = {
            "map": m["map"],
            "image": geo["image"],
            "image_size": g["image_size"],
            "full_image": g["full_image"],
            "waypoint_icon": ICON,
            "starts": starts,
            "stops": geo["stops"],
            "path": [{"start": leg["start"], "points": leg["points"]} for leg in geo["legs"]],
            "counts": {"mirrors": r["mirrors"], "medium_chests": r["medium_chests"], "large_chests": r["large_chests"]},
            "large_rule": r["large"],
            "credit": CONFIG["credit"],
        }
        if m.get("wiki_map"):
            route["wiki_map_url"] = m["wiki_map"]
        act = {
            "id": CONFIG["id_prefix"] + m["slug"],
            "name": f"{m['map']}: Magic Mirror route",
            "mode": "pve",
            "expansion": "voe",
            "est_minutes": r["minutes"],
            "max_runs_per_day": 1,
            "yields": [{"node_id": node["id"], "count": r["total"]}],
            "description": r["description"],
            "guidance": {
                "text": (f"{r['description']} {r['total']} {m['currency']} once a day per account (every chest and keepsake box once a day; "
                         f"{r['minutes']:g} min: {r['time_source']}). {CONFIG['credit']}."),
                "wiki_url": CONFIG["wiki_url"],
                "waypoint_chat_code": starts[0]["chat_code"],
                "waypoint_name": starts[0]["name"],
            },
            "map_ids": [m["map_id"]],
            "mirror_route": route,
            "_cats": [],   # year-round content: the festival tagger asks the wiki nothing for it (stripped before writing)
        }
        final[act["id"]] = act
        srcs = node["sources"]
        if not any(x.get("activity_id") == act["id"] for x in srcs):
            srcs[:] = [x for x in srcs if x["kind"] != "other"] + [{"kind": "activity", "inputs": [], "activity_id": act["id"]}]
        stats["activities"] += 1
    return stats, warn
