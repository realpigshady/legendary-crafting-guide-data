"""Nearest waypoint for every vendor, from the official API only.

The wiki gives a vendor's place as the area it stands in ("Located in", e.g. "Jora's
Keep"). The API's continent floors hold the same areas as map sectors (name + label
position) and every waypoint (name, position, chat link). So:

  area name -> sector(s) of that name -> the map it is on -> nearest waypoint on that map

A location that is a map name instead of an area uses the map's centre. A name found on
more than one map is only used when a parenthesis names the map ("Trader's Forum (Memory
of Old Lion's Arch)") or every candidate map gives the same waypoint; otherwise it stays
unresolved rather than sending the player to the wrong map.

Floors are big (several MB each), so only an extract is kept: snapshot/map-index.json
(maps, sectors, waypoints), refreshed weekly. Offline and outage runs use the extract.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Callable

MAX_AGE_S = 6 * 24 * 3600
API = "https://api.guildwars2.com"
INDEX = "map-index.json"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _values(d):
    return list(d.values()) if isinstance(d, dict) else list(d or [])


VERSION = 2   # bump when extract() changes: a stored extract of another version is fetched again


def extract(maps_all: list[dict], floors: dict[tuple[int, int], dict], skipped: list[str] | None = None) -> list[dict]:
    """Compact table from /v2/maps?ids=all and the floors each map defaults to:
    [{id, name, centre: [x, y], sectors: [[name, x, y]], waypoints: [[name, x, y, chat_link]]}]"""
    out: dict[int, dict] = {}
    skipped.clear() if skipped is not None else None
    for m in maps_all:
        c = m.get("continent_id")
        order = [(c, m.get("default_floor"))] + sorted(k for k in floors if k[0] == c and k != (c, m.get("default_floor")))
        fm, wps = None, []
        for k in order:   # default floor first; a map missing there may sit on another floor
            for reg in _values((floors.get(k) or {}).get("regions")):
                cand = (reg.get("maps") or {}).get(str(m["id"]))
                if cand:
                    w = sorted([p.get("name") or "", round(p["coord"][0]), round(p["coord"][1]), p["chat_link"]]
                               for p in _values(cand.get("points_of_interest"))
                               if p.get("type") == "waypoint" and p.get("chat_link") and p.get("coord"))
                    if w:
                        fm, wps = cand, w
                        break
            if fm:
                break
        if not fm:
            if skipped is not None and m.get("type") in ("Public", "Instance", "Unknown", "PublicMini"):
                skipped.append(f"{m['id']} {m.get('name')} ({c}/{m.get('default_floor')})")
            continue
        secs = sorted([s.get("name") or "", round(s["coord"][0]), round(s["coord"][1])]
                      for s in _values(fm.get("sectors")) if s.get("name") and s.get("coord"))
        rect = fm.get("continent_rect") or m.get("continent_rect") or [[0, 0], [0, 0]]
        out[m["id"]] = {"id": m["id"], "name": m.get("name") or fm.get("name") or "",
                        "centre": [round((rect[0][0] + rect[1][0]) / 2), round((rect[0][1] + rect[1][1]) / 2)],
                        "sectors": secs, "waypoints": wps}
    return [out[k] for k in sorted(out)]


def load(snapshot: Path, get_live: Callable[[str], object] | None, offline: bool) -> tuple[list[dict], bool]:
    """(map index, fresh). Fetches when the stored extract is over a week old and the API is up."""
    path = snapshot / INDEX
    have = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if have and (offline or (time.time() - have.get("fetched_at", 0) < MAX_AGE_S and have.get("v") == VERSION)):
        return have["maps"], False
    if not offline and get_live:
        try:
            maps_all = get_live(f"{API}/v2/maps?ids=all")
            want = sorted({(m["continent_id"], m["default_floor"]) for m in maps_all
                           if m.get("continent_id") and m.get("default_floor") is not None})
            floors = {}
            for k in want:   # one bad floor costs only its maps
                try:
                    floors[k] = get_live(f"{API}/v2/continents/{k[0]}/floors/{k[1]}")
                except Exception as e:  # noqa: BLE001
                    print(f"  map index: floor {k} skipped ({e})")
            skipped: list[str] = []
            maps = extract(maps_all, floors, skipped)
            print(f"  map index: {len(maps)} maps from {len(floors)} floors; {len(skipped)} public/instance maps without waypoints: "
                  + "; ".join(skipped[:60]))
            if maps:
                text = json.dumps({"fetched_at": round(time.time()), "v": VERSION, "maps": maps}, ensure_ascii=False, sort_keys=True, indent=0)
                path.write_text(text + "\n", encoding="utf-8")
                return maps, True
        except Exception as e:  # noqa: BLE001
            print(f"  map index: API unavailable ({e}); using stored extract")
    return (have or {}).get("maps", []), False


class Finder:
    def __init__(self, maps: list[dict]):
        self.maps = {m["id"]: m for m in maps}
        self.by_sector: dict[str, list[tuple[int, int, int]]] = {}
        self.by_map: dict[str, list[int]] = {}
        for m in maps:
            self.by_map.setdefault(_norm(m["name"]), []).append(m["id"])
            for name, x, y in m["sectors"]:
                self.by_sector.setdefault(_norm(name), []).append((m["id"], x, y))

    def _nearest(self, mid: int, x: int, y: int) -> dict:
        m = self.maps[mid]
        name, _, _, code = min(m["waypoints"], key=lambda w: ((w[1] - x) ** 2 + (w[2] - y) ** 2, w[3]))
        return {"map_id": mid, "map": m["name"], "waypoint_name": name, "waypoint_chat_code": code}

    def find(self, location: str, on_maps: set[int] | None = None) -> dict | None:
        """``on_maps``: map ids the wiki gives for this place; narrows a name found on several maps."""
        base, hint = location, None
        mt = re.match(r"^(.*?)\s*\((.+)\)\s*$", location)
        if mt:
            base, hint = mt.group(1), mt.group(2)
        cands = self.by_sector.get(_norm(location)) or self.by_sector.get(_norm(base)) or []
        if hint and len(cands) > 1:
            on = set(self.by_map.get(_norm(hint), []))
            cands = [c for c in cands if c[0] in on] or cands
        if on_maps and len(cands) > 1:
            cands = [c for c in cands if c[0] in on_maps] or cands
        if not cands:   # the location is a whole map
            ids = self.by_map.get(_norm(location)) or self.by_map.get(_norm(base)) or []
            cands = [(i, *self.maps[i]["centre"]) for i in ids]
        hits = [self._nearest(*c) for c in cands]
        if not hits:
            return None
        if len({h["waypoint_chat_code"] for h in hits}) > 1:
            return None   # same name on different maps: don't guess
        return hits[0]


DEPTH = 4   # sub-area -> area -> zone -> region at most


def _ids(v) -> set[int]:
    out = set()
    for x in v if isinstance(v, list) else [v]:
        try:
            out.add(int(float(str(x))))
        except (TypeError, ValueError):
            pass
    return out


def resolve_all(locations: list[str], f: Finder, places: Callable[[list[str]], dict[str, list[dict]]] | None) -> dict[str, dict | None]:
    """Waypoint per wiki location. A sub-area the API doesn't know as a sector ("Maker's
    Terrace") climbs the wiki's Located in chain (SMW, ``places``) until a sector or map
    matches; the wiki's map id narrows names found on several maps, or gives the map
    centre as a last resort."""
    out: dict[str, dict | None] = {}
    info: dict[str, tuple[list[str], set[int]]] = {}
    for loc in locations:
        out[loc] = f.find(loc)
    level = [l for l in locations if out[l] is None]
    chains = {l: [l] for l in level}            # loc -> names asked so far, nearest first
    for _ in range(DEPTH):
        ask = sorted({c[-1] for l, c in chains.items() if out[l] is None and c[-1] not in info})
        if ask and places:
            for name, rws in places(ask).items():
                r = rws[0] if rws else {}
                info[name] = ([x for x in (r.get("Located in") or []) if isinstance(x, str)], _ids(r.get("Has map id")))
        moved = False
        for l, c in chains.items():
            if out[l] is not None:
                continue
            parents, ids = info.get(c[-1], ([], set()))
            wanted = set().union(*(info.get(x, ([], set()))[1] for x in c))
            for par in parents:
                hit = f.find(par, wanted or None)
                if hit:
                    out[l] = hit
                    break
            if out[l] is None and parents and parents[0] not in c:
                c.append(parents[0])
                moved = True
        if not moved:
            break
    for l, c in chains.items():                 # last resort: a map the wiki names, by its centre
        if out[l] is None:
            for x in c:
                for mid in sorted(info.get(x, ([], set()))[1]):
                    if mid in f.maps:
                        out[l] = f._nearest(mid, *f.maps[mid]["centre"])
                        break
                if out[l]:
                    break
    return out


def annotate(nodes: list[dict], maps: list[dict], places: Callable[[list[str]], dict[str, list[dict]]] | None = None) -> dict:
    """Adds map_id, map, waypoint_name, waypoint_chat_code to each vendor (first location
    that resolves). Returns coverage counts for stats.json."""
    f = Finder(maps)
    locs = sorted({l for n in nodes for s in n.get("sources", []) for v in s.get("vendors") or [] for l in v.get("locations") or []})
    cache = resolve_all(locs, f, places) if maps else {l: None for l in locs}
    seen, hit = set(), set()
    unresolved: dict[str, int] = {}
    for n in nodes:
        for s in n.get("sources", []):
            for v in s.get("vendors") or []:
                for k in ("map_id", "map", "waypoint_name", "waypoint_chat_code"):
                    v.pop(k, None)
                key = v["npc"] + "|" + "|".join(v.get("locations") or [])
                seen.add(key)
                for loc in v.get("locations") or []:
                    if cache[loc]:
                        v.update(cache[loc])
                        hit.add(key)
                        break
                else:
                    for loc in v.get("locations") or ["(none)"]:
                        unresolved[loc] = unresolved.get(loc, 0) + 1
    return {"vendors": len(seen), "with_waypoint": len(hit), "maps_indexed": len(maps),
            "unresolved_locations": dict(sorted(unresolved.items(), key=lambda kv: (-kv[1], kv[0]))[:40])}
