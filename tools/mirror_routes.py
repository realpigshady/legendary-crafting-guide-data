"""Magic Mirror routes (Data 4o (1b)): one full-sweep and one efficient activity per Castora map.

Owner-measured numbers come from ``config/mirror_routes.json``; positions on the route images
from ``config/mirror_route_geometry.json`` (``tools/mirror_maps_import.py``); the images ship as
release assets beside ``pack.json`` (``assets/mirror-maps/``, copied to the build output by
``copy_assets``). No wiki calls: the start waypoints' chat codes and the map ids come from the
API map index.

Each activity ``voe:mirror:<map>:full`` / ``voe:mirror:<map>:efficient``:
  group           ``voe:mirror:<map>``: at most one of the two a play day
  max_runs_per_day 1 (every chest and keepsake box once a day per account)
  yields          the route's guaranteed map currency
  mirror_route    what the UI's mirror window draws (schema: SPEC §5 "Magic Mirror routes")
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


def _waypoint(maps: list[dict], map_name: str, wp_name: str) -> tuple[int | None, str | None]:
    m = next((x for x in maps if x.get("name") == map_name), None)
    if not m:
        return None, None
    w = next((w for w in m.get("waypoints", []) if w[0] == wp_name), None)
    return m["id"], (w[3] if w else None)


def asset_names() -> list[str]:
    names = [ICON]
    for g in GEOMETRY.values():
        names += [g["full_image"], g["efficient_image"]]
    return names


def copy_assets(out: Path) -> list[str]:
    """Copies the route images and the waypoint icon to ``out`` (release assets). -> warnings."""
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
    stats = {"activities": 0, "maps": 0}
    by_name = {_norm(n["name"]): n for n in nodes.values()}
    for m in CONFIG["maps"]:
        geo = GEOMETRY.get(m["slug"])
        node = by_name.get(_norm(m["currency"]))
        if geo is None or node is None:
            warn.append(f"mirror routes: {m['map']}: " + ("no geometry" if geo is None else f"no pack node for {m['currency']}"))
            continue
        on_image = {w["name"]: (w["x"], w["y"]) for w in geo["waypoints"]}
        group = CONFIG["group_prefix"] + m["slug"]
        stats["maps"] += 1
        for variant in ("full", "efficient"):
            v = m[variant]
            map_id, code = _waypoint(maps, m["map"], v["start"])
            if code is None:
                warn.append(f"mirror routes: {m['map']}: no chat code for {v['start']}")

            def start(name: str) -> dict:
                mid_, c = _waypoint(maps, m["map"], name)
                s = {"name": name, "chat_code": c}
                if name in on_image:
                    s["x"], s["y"] = on_image[name]
                return s
            starts = [start(v["start"])] + ([start(v["second_start"])] if v.get("second_start") else [])
            route = {
                "map": m["map"],
                "variant": variant,
                "image": geo[f"{variant}_image"],
                "image_size": geo["image_size"],
                "waypoint_icon": ICON,
                "starts": starts,
            }
            if m.get("wiki_map"):
                route["wiki_map_url"] = m["wiki_map"]
            if variant == "full":
                route["mirrors"] = geo["full"]["mirrors"]
                if geo["full"]["chests"]:
                    route["chests"] = geo["full"]["chests"]
                summary = f"all mirrors ({v['mirrors']} keepsake boxes) and {v['medium_chests']} medium chests"
            else:
                e = geo["efficient"]
                route["stops"] = e["stops"]
                route["path"] = [{"start": leg["start"], "points": leg["points"]} for leg in e["legs"]]
                route["path_drawn_by_owner"] = all(leg["drawn"] for leg in e["legs"])
                summary = (f"medium / large chests only: {v['mirrors']} mirrors, {v['chest_spots']} chest spots "
                           f"(small chests and {e['skipped_mirrors']} mirrors skipped)")
            act = {
                "id": f"{group}:{variant}",
                "name": f"{m['map']}: Magic Mirror route ({'full sweep' if variant == 'full' else 'efficient'})",
                "mode": "pve",
                "expansion": "voe",
                "est_minutes": v["minutes"],
                "max_runs_per_day": 1,
                "group": group,
                "yields": [{"node_id": node["id"], "count": v["total"]}],
                "guidance": {
                    "text": (f"{m['map']} Magic Mirror route, {summary}: {v['total']} {m['currency']} once a day per account "
                             f"(every chest and keepsake box once a day; owner-measured, {v['minutes']:g} min: {v['time_source']})"),
                    "wiki_url": CONFIG["wiki_url"],
                },
                "mirror_route": route,
                "_cats": [],   # year-round content: the festival tagger asks the wiki nothing for it (stripped before writing)
            }
            if code:
                act["guidance"]["waypoint_chat_code"], act["guidance"]["waypoint_name"] = code, v["start"]
            if map_id:
                act["map_ids"] = [map_id]
            # the owner's note where there is one (Eternity's Garden, exact wording), else a short line
            act["description"] = m.get("description") or (
                f"{'Every mirror and its chests' if variant == 'full' else 'Medium / large chests only'}: "
                f"{v['total']} {m['currency']}, start at {v['start']}. Once a day per account.")
            final[act["id"]] = act
            srcs = node["sources"]
            if not any(x.get("activity_id") == act["id"] for x in srcs):
                srcs[:] = [x for x in srcs if x["kind"] != "other"] + [{"kind": "activity", "inputs": [], "activity_id": act["id"]}]
            stats["activities"] += 1
    return stats, warn
