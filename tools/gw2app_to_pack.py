"""TESTING ONLY: turn the gw2.app export (gw2app-export / gw2app-world branches) into a data
pack for LOCAL testing of the addon while the wiki is down. Never published or released.

    python tools/gw2app_to_pack.py --export export/gw2app.json --world world/world.json \
        --sample tools/test_activities.json --out testpack/pack.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.guildwars2.com"


def currency_ids() -> dict[str, int]:
    try:
        with urllib.request.urlopen(API + "/v2/currencies?ids=all", timeout=30) as r:
            return {c["name"]: c["id"] for c in json.load(r)}
    except Exception:  # noqa: BLE001 - offline: coin only
        return {"Coin": 1}


def map_ids_by_name() -> dict[str, list[int]]:
    """Official map names -> ids (several instances can share a name)."""
    try:
        with urllib.request.urlopen(API + "/v2/maps?ids=all", timeout=60) as r:
            out: dict[str, list[int]] = {}
            for m in json.load(r):
                out.setdefault(m["name"], []).append(m["id"])
            return out
    except Exception:  # noqa: BLE001 - offline: no map ids
        return {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", type=Path, required=True)
    ap.add_argument("--world", type=Path)
    ap.add_argument("--sample", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    exp = json.loads(a.export.read_text())
    items, mf, rc = exp["items"], exp["mystic_recipes"], exp["recipes"]
    cur_ids = currency_ids()
    nodes: dict[str, dict] = {}
    unknown_currencies: set[str] = set()

    def item_node(iid: int, name: str = "") -> str:
        nid = f"item:{iid}"
        if nid not in nodes:
            nodes[nid] = {"id": nid, "type": "drop", "name": name or items.get(str(iid), {}).get("name") or f"Item {iid}",
                          "item_id": iid, "sources": [{"kind": "trading_post", "inputs": []}]}   # price decides at runtime
        return nid

    def currency_node(cid: int, name: str) -> str:
        nid = f"currency:{cid}"
        nodes.setdefault(nid, {"id": nid, "type": "currency", "name": name, "currency_id": cid, "sources": []})
        return nid

    for iid_s, it in items.items():
        iid = int(iid_s)
        nid = item_node(iid, it["name"])
        node = nodes[nid]
        acq = it["acquisition"]
        # vendor offers: groups that start with a link to the item itself, followed by cost links
        offers, cur, bad = [], None, set()
        for l in acq:
            h = l["href"]
            if h == f"/items/{iid}":
                cur = []
                offers.append(cur)
                continue
            if cur is None:
                continue
            count = int(re.sub(r"[^\d]", "", l["label"]) or 0)
            if m := re.fullmatch(r"/items/(\d+)", h):
                if count:
                    cur.append({"node_id": item_node(int(m.group(1))), "count": count})
            elif h.startswith("/currencies?"):
                cname = urllib.parse.parse_qs(urllib.parse.urlparse(h).query).get("filter", [""])[0]
                if cname in cur_ids and count:
                    cur.append({"node_id": currency_node(cur_ids[cname], cname), "count": count})
                elif cname:
                    unknown_currencies.add(cname)
                    bad.add(id(cur))   # a cost we can't name: drop the whole offer, never under-price it
            else:
                cur = None   # recipe/achievement links end a vendor group
        for o in offers:
            if o and id(o) not in bad:
                node["sources"].append({"kind": "vendor", "inputs": o})
        for l in acq:
            h = l["href"]
            if m := re.fullmatch(r"/recipes/mystic/(\d+)", h):
                r = mf.get(m.group(1))
                if r and r.get("output_id") in (iid, None) and r["ingredients"]:
                    node["type"] = "forge"
                    node["sources"].append({"kind": "mystic_forge", "output_count": max(1, r.get("output_count", 1)),
                                            "inputs": [{"node_id": item_node(i["id"], i.get("name", "")), "count": i["count"]}
                                                       for i in r["ingredients"] if i["type"] == "item"]})
            elif m := re.fullmatch(r"/recipes/(\d+)", h):
                r = rc.get(m.group(1))
                if r and r.get("ingredients"):
                    ins = []
                    for i in r["ingredients"]:
                        ii = i.get("id", i.get("item_id"))
                        if i.get("type", "Item") == "Item":
                            ins.append({"node_id": item_node(ii), "count": i["count"]})
                        elif i.get("type") == "Currency":
                            ins.append({"node_id": currency_node(ii, f"Currency {ii}"), "count": i["count"]})
                    node["type"] = "craft"
                    node["sources"].append({"kind": "craft", "output_count": max(1, r.get("output_item_count", 1)),
                                            "disciplines": r.get("disciplines", []), "inputs": ins})
            elif m := re.fullmatch(r"/achievements/(\d+)", h):
                # time comes from the achievement's steps (player progress / API), never free
                node["sources"].append({"kind": "reward", "inputs": [], "achievement_id": int(m.group(1))})
        # an item never gets two identical sources
        seen, uniq = set(), []
        for s in node["sources"]:
            k = json.dumps(s, sort_keys=True)
            if k not in seen:
                seen.add(k)
                uniq.append(s)
        node["sources"] = uniq

    # break recipe cycles (promotion/demotion loops): drop the source that closes a cycle
    colour: dict[str, int] = {}
    dropped = 0

    def visit(nid: str) -> None:
        nonlocal dropped
        colour[nid] = 1
        keep = []
        for s in nodes[nid]["sources"]:
            if any(colour.get(i["node_id"]) == 1 for i in s["inputs"]):
                dropped += 1
                continue
            for i in s["inputs"]:
                if colour.get(i["node_id"], 0) == 0:
                    visit(i["node_id"])
            if any(colour.get(i["node_id"]) == 1 for i in s["inputs"]):
                dropped += 1
                continue
            keep.append(s)
        nodes[nid]["sources"] = keep
        colour[nid] = 2

    import sys
    sys.setrecursionlimit(10000)
    for nid in list(nodes):
        if colour.get(nid, 0) == 0:
            visit(nid)

    legendaries = []
    for lid in exp["legendary_ids"]:
        it = items.get(str(lid))
        if not it:
            continue
        legendaries.append({"id": f"test:{lid}", "family": "test", "name": it["name"], "item_id": lid, "root_node_id": f"item:{lid}"})

    activities = []
    unresolved_maps: list[str] = []
    if a.sample and a.sample.exists():
        timers = {t["name"]: t for t in (json.loads(a.world.read_text()).get("timers", []) if a.world and a.world.exists() else [])}
        maps = map_ids_by_name()
        for act in json.loads(a.sample.read_text())["activities"]:
            # only yields the legendary trees use (currencies included only when a tree needs them)
            ys = [y for y in act["yields"] if y["node_id"] in nodes]
            timer, names = act.pop("timer", None), act.pop("map_names", [])
            ids = sorted({i for n in names for i in maps.get(n, [])})
            unresolved_maps += [n for n in names if n not in maps]
            if ids:
                act["map_ids"] = ids
            t = timers.get(timer) if timer else None
            if t and t.get("waypoint"):
                act["guidance"] = {"waypoint_chat_code": t["waypoint"], "waypoint_name": t["name"],
                                   "text": f"{t['group']}: every {t['interval_min']} min" if t.get("interval_min") else t["group"]}
            if ys:
                act = dict(act, yields=ys)
                activities.append(act)
                for y in ys:
                    nodes[y["node_id"]]["sources"].append({"kind": "activity", "inputs": [], "activity_id": act["id"]})

    pack = {"format_version": 1, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "sample": True,
            "_note": "LOCAL TEST PACK built from an interim gw2.app export while the GW2 wiki is down. Never published. "
                     "Achievement rewards are timed from their steps (15 min each, estimated); daily-craft caps are missing.",
            "legendaries": legendaries, "nodes": list(nodes.values()), "activities": activities}
    if a.world and a.world.exists():
        pack["_timers"] = json.loads(a.world.read_text()).get("timers", [])
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(pack, ensure_ascii=False, separators=(",", ":")))
    print(f"activities {[x['id'] for x in activities]}, map names not found {unresolved_maps}")
    print(f"legendaries {len(legendaries)}, nodes {len(nodes)}, activities {len(activities)}, "
          f"cycle sources dropped {dropped}, unknown currencies {sorted(unknown_currencies)[:10]}")


if __name__ == "__main__":
    main()
