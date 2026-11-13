import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import mirror_routes as mr  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OWNER_EG = "Map requires you to gain 5 additional keys through events, do this before route start"


def _maps():
    return json.loads((ROOT / "snapshot" / "map-index.json").read_text(encoding="utf-8"))["maps"]


def _nodes():
    return {f"item:{i}": {"id": f"item:{i}", "name": n, "sources": [{"kind": "other", "inputs": []}]}
            for i, n in [(106385, "Chromatic Sap"), (105686, "Raw Enchanting Stone"),
                         (109459, "Shadowstone Fragment"), (110156, "Shadowstone Research Data")]}


def test_owner_numbers():
    by = {m["map"]: m for m in mr.CONFIG["maps"]}
    want = {"Shipwreck Strand": (47, 35, 7), "Starlit Weald": (66, 42, 7),
            "Eternity's Garden": (45, 33, 12.5), "Leyspring Hollows": (16, 10, 15)}
    for name, (full, eff, mins) in want.items():
        m = by[name]
        assert (m["full"]["total"], m["efficient"]["total"]) == (full, eff), name
        assert m["full"]["minutes"] == m["efficient"]["minutes"] == mins, name
        # full sweep = medium chests x 2 + keepsake boxes (one per mirror) x 3 (owner 2026-11-12)
        assert m["full"]["medium_chests"] * 2 + m["full"]["mirrors"] * 3 == full, name
    assert by["Eternity's Garden"]["description"] == OWNER_EG


def test_geometry_inside_images_and_assets_present():
    from PIL import Image
    for slug, g in mr.GEOMETRY.items():
        w, h = g["image_size"]
        for name in (g["full_image"], g["efficient_image"]):
            assert list(Image.open(mr.ASSETS / name).size) == [w, h], name
        pts = [(p["x"], p["y"]) for p in g["full"]["mirrors"] + g["full"]["chests"] + g["efficient"]["stops"] + g["waypoints"]]
        pts += [tuple(q) for leg in g["efficient"]["legs"] for q in leg["points"]]
        assert all(0 <= x <= w and 0 <= y <= h for x, y in pts), slug
        assert sum(s["kind"] == "mirror" for s in g["efficient"]["stops"]) == next(
            m["efficient"]["mirrors"] for m in mr.CONFIG["maps"] if m["slug"] == slug)
    assert (mr.ASSETS / mr.ICON).exists()


def test_apply_makes_two_variants_per_map_in_one_group():
    final, nodes = {}, _nodes()
    stats, warn = mr.apply(final, nodes, _maps())
    assert stats == {"activities": 8, "maps": 4} and warn == []
    by_group = {}
    for a in final.values():
        by_group.setdefault(a["group"], []).append(a)
        assert a["max_runs_per_day"] == 1 and a["mode"] == "pve" and a["map_ids"]
        r = a["mirror_route"]
        assert r["image"] in mr.asset_names() and r["starts"][0]["chat_code"]
        assert a["guidance"]["waypoint_chat_code"] == r["starts"][0]["chat_code"]
        assert ("stops" in r and "path" in r) if r["variant"] == "efficient" else "mirrors" in r
    assert sorted(len(v) for v in by_group.values()) == [2, 2, 2, 2]
    eg = [a for a in final.values() if a["mirror_route"]["map"] == "Eternity's Garden"]
    assert all(a["description"] == OWNER_EG for a in eg)
    sw = final["voe:mirror:starlit-weald:full"]
    assert sw["yields"] == [{"node_id": "item:105686", "count": 66}] and sw["est_minutes"] == 7
    assert sw["guidance"]["waypoint_chat_code"] == "[&BMAPAAA=]"   # Foothold Bivouac
    lh = final["voe:mirror:leyspring-hollows:efficient"]["mirror_route"]
    assert [s["name"] for s in lh["starts"]] == ["Doyen's Adytum Waypoint", "Hall of Assemblage Waypoint"]
    assert lh["path_drawn_by_owner"] is True
    # each currency node gets both variants as activity sources, 'other' dropped
    for n in nodes.values():
        assert [s["kind"] for s in n["sources"]] == ["activity", "activity"]


def test_copy_assets(tmp_path):
    assert mr.copy_assets(tmp_path) == []
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(mr.asset_names())
