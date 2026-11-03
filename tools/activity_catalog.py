"""The full activity catalog from the wiki: every timed event phase and every location of
the playable types, with their guaranteed rewards as yields.

  timed     Widget:Event timer/data.json: every named phase of every map (world bosses,
            metas, invasions, Ley-Line Anomaly, PvP tournaments, festivals...). Run length,
            cycle, UTC starts and waypoint come from the data. Day/night bars are skipped.
  places    SMW [[Has context::Location]][[Has location type::...]]: every fractal, strike
            mission, raid, dungeon, Dragon Response Mission, mini-dungeon and activity.
            The wiki has no run length for these: no est_minutes (DRF measures them).
  yields    SMW [[Rewarded by::page]] rows with chance "Guaranteed" (item ids from the item
            pages). Chance drops have no rate on the wiki and are left out.

Every activity is listed even with no yields or time: the pack carries the full list and
the planner uses what has numbers.
"""
from __future__ import annotations

import re
import statistics

import wiki_sources as ws

# Playable location types of SMW "Has location type" (all 24 values checked 2026-11-03; places such as
# zones, cities, vistas, waypoints, points of interest and story instances aren't activities)
PLACE_TYPES = ["Fractal", "Raid", "Dungeon", "Dragon Response Mission", "Mini-dungeon", "Activity", "Jumping puzzle",
               "Guild Puzzle", "Conquest", "Stronghold", "Team Deathmatch", "Push"]
PVP_TYPES = {"Conquest", "Stronghold", "Team Deathmatch", "Push"}
# activities filed by category, not location type
PLACE_CATEGORIES = {"Category:Strike missions": "Strike mission", "Category:Convergences": "Convergence"}
SKIP_MAPS = re.compile(r"day and night", re.I)
FILLER = re.compile(r"^\s*(reset|prep|preparations?|\(continued\))\s*$", re.I)   # gaps between runs, not activities
EXPANSION = {"core": "core", "lws2": "core", "public": "core", "festival": "core", "t": "core",
             "hot": "hot", "lws3": "hot", "pof": "pof", "lws4": "pof", "lws5": "pof",
             "eod": "eod", "soto": "soto", "jw": "jw", "voe": "voe"}
WIKI = "https://wiki.guildwars2.com/wiki/"


def _url(page: str) -> str:
    import urllib.parse
    return WIKI + urllib.parse.quote(page.replace(" ", "_"))


def _hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def timed(events: dict) -> list[dict]:
    out = []
    for key, m in events.items():
        name = m.get("name", "")
        if not name or SKIP_MAPS.search(name):
            continue
        seg = m["segments"]
        partial, pattern = m["sequences"].get("partial", []), m["sequences"].get("pattern", [])
        cyc = sum(x["d"] for x in pattern)
        line = partial + (pattern * (1440 // cyc + 2) if cyc else [])
        blocks: dict[str, list[tuple[int, int]]] = {}
        t, prev = 0, None
        for x in line:
            sname = seg[str(x["r"])].get("name") or ""
            if sname and sname == prev:
                s0, d0 = blocks[sname][-1]
                blocks[sname][-1] = (s0, d0 + x["d"])
            elif sname and t < 1440:
                blocks.setdefault(sname, []).append((t, x["d"]))
            prev, t = sname, t + x["d"]
            if t >= 1440 + 240:
                break
        for sname, bl in blocks.items():
            if FILLER.match(sname):
                continue
            if bl and bl[0][0] == 0 and partial and seg[str(partial[0]["r"])].get("name") == sname and len(bl) > 1:
                bl = bl[1:]   # a run already going at 00:00 UTC: counted where it starts
            starts = sorted({s % 1440 for s, _ in bl})
            gaps = {b - a for a, b in zip(starts, starts[1:])}
            every = gaps.pop() if len(gaps) == 1 else None
            info = next((seg[k] for k in seg if seg[k].get("name") == sname), {})
            page = info.get("link") or sname
            when = f"every {every} min" if every else f"{len(starts)} a day"
            g = {"text": f"{m.get('category', '')}: {when} (UTC {', '.join(map(_hhmm, starts[:4]))}{'...' if len(starts) > 4 else ''}; wiki event timer)",
                 "wiki_url": _url(page)}
            if info.get("chatlink"):
                g["waypoint_chat_code"] = info["chatlink"]
            label = sname if name.lower() in sname.lower() or key in ("core-wb", "core-hwb") else f"{name}: {sname}"
            out.append({"id": f"evt:{key}:{ws.slug(sname)}", "name": label,
                        "mode": "pvp" if key in ("core-ateu", "core-atna") else "pve",
                        "expansion": EXPANSION.get(key.split("-")[0], "core"),
                        "est_minutes": int(statistics.median(d for _, d in bl)),
                        "starts_utc": starts, "yields": [], "guidance": g, "_page": page})
    return out


def places(store: "ws.SourceStore") -> list[dict]:
    rows = []
    for t, rws in store.get("locations", PLACE_TYPES).items():
        rows += rws
    for cat, rws in store.get("category", list(PLACE_CATEGORIES)).items():
        for r in rws:
            rows.append({**r, "Has location type": [PLACE_CATEGORIES[cat]]})
    out, seen = [], set()
    for r in rows:
        page = r["_subject"].split("#")[0]
        if page in seen or ws._true(r.get("Is historical")):
            continue
        seen.add(page)
        kind = ", ".join(r.get("Has location type") or [])
        where = ", ".join(r.get("Located in") or [])
        if page.startswith(("Category:", "Template:", "File:")):
            continue
        pvp = bool(set(r.get("Has location type") or []) & PVP_TYPES)
        out.append({"id": f"loc:{ws.slug(page)}", "name": (r.get("Has canonical name") or [page])[0], "mode": "pvp" if pvp else "pve",
                    "expansion": "core", "yields": [],
                    "guidance": {"text": kind + (f" in {where}" if where else "") + " (no run length on the wiki: DRF times it)",
                                 "wiki_url": _url(page)}, "_page": page})
    import collections
    import sys
    counts = collections.Counter(a["guidance"]["text"].split(" (")[0].split(" in ")[0] for a in out)
    print("activity places: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())), file=sys.stderr)
    for want in PLACE_TYPES + list(PLACE_CATEGORIES.values()):
        if not any(want in k for k in counts):
            print(f"activity places: none of type {want}", file=sys.stderr)
    return sorted(out, key=lambda a: a["id"])


def add_yields(acts: list[dict], store: "ws.SourceStore") -> None:
    pages = sorted({a["_page"] for a in acts})
    got = store.get("rewards_of", pages)
    for a in acts:
        ys = {}
        for r in got.get(a["_page"], []):
            if (r.get("Has rewarded item chance") or [""])[0] != "Guaranteed":
                continue
            iid = ws._int(r.get("Has game id"))
            item = (r.get("Rewards item") or [None])[0]
            if not iid or not item:
                continue
            nid = f"item:{iid}"
            ys[nid] = ys.get(nid, 0) + (ws._int(r.get("Has rewarded item quantity")) or 1)
        a["yields"] = [{"node_id": k, "count": v, "name": None} for k, v in sorted(ys.items())]
        for y in a["yields"]:
            y.pop("name")


def build(events: dict | None, store: "ws.SourceStore") -> list[dict]:
    acts = (timed(events) if events else []) + places(store)
    add_yields(acts, store)
    return acts
