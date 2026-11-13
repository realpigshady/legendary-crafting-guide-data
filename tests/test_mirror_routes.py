import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import mirror_routes as mr  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _maps():
    return json.loads((ROOT / "snapshot" / "map-index.json").read_text(encoding="utf-8"))["maps"]


def _nodes():
    return {f"item:{i}": {"id": f"item:{i}", "name": n, "sources": [{"kind": "other", "inputs": []}]}
            for i, n in [(106385, "Chromatic Sap"), (105686, "Raw Enchanting Stone"),
                         (109459, "Shadowstone Fragment"), (110156, "Shadowstone Research Data")]}


def test_owner_numbers_and_key_budget():
    by = {m["map"]: m["route"] for m in mr.CONFIG["maps"]}
    want = {"Shipwreck Strand": (9, 10, 1, 50, 7), "Starlit Weald": (10, 12, 1, 57, 7),
            "Eternity's Garden": (9, 12, 0, 51, 12.5), "Leyspring Hollows": (4, 2, 1, 19, 15)}
    for name, (mir, med, lg, total, mins) in want.items():
        r = by[name]
        assert (r["mirrors"], r["medium_chests"], r["large_chests"], r["total"], r["minutes"]) == (mir, med, lg, total, mins), name
        assert mir * 3 + med * 2 + lg * 3 == total, name
        # Ethereal Key Charges: 8 on entry + 5 a mirror in; medium 4, large 10 out (Efficient Magic)
        assert r["keys_in"] == 8 + 5 * mir and r["keys_out"] == 4 * med + 10 * lg and r["keys_in"] >= r["keys_out"], name
        assert r["large"] == ("none" if lg == 0 else "first"), name


def test_route_geometry_and_assets():
    from PIL import Image
    for slug, g in mr.GEOMETRY.items():
        r = g["route"]
        w, h = g["image_size"]
        for name in (r["image"], g["full_image"]):
            assert list(Image.open(mr.ASSETS / name).size) == [w, h], name
        cfg = next(m["route"] for m in mr.CONFIG["maps"] if m["slug"] == slug)
        assert sum(s["kind"] == "mirror" for s in r["stops"]) == cfg["mirrors"], slug
        assert sum(s.get("size") == "large" for s in r["stops"]) == cfg["large_chests"], slug
        # the pack shows more medium spots than there are chests (shared spawns), never fewer
        assert sum(s.get("size") == "medium" for s in r["stops"]) >= cfg["medium_chests"], slug
        pts = [(s["x"], s["y"]) for s in r["stops"]] + [(l["x"], l["y"]) for l in r["legs"]] + [tuple(q) for l in r["legs"] for q in l["points"]]
        assert all(0 <= x <= w and 0 <= y <= h for x, y in pts), slug
        assert r["fit_max_mirror_px"] < 40, slug
    assert (mr.ASSETS / mr.ICON).exists()


def test_apply_makes_one_route_per_map():
    final, nodes = {}, _nodes()
    stats, warn = mr.apply(final, nodes, _maps())
    assert stats == {"activities": 4} and warn == []
    for a in final.values():
        assert a["max_runs_per_day"] == 1 and a["mode"] == "pve" and a["map_ids"]
        r = a["mirror_route"]
        assert r["image"] in mr.asset_names() and r["starts"][0]["chat_code"]
        assert a["guidance"]["waypoint_chat_code"] == r["starts"][0]["chat_code"]
        assert "12Tables" in r["credit"]
    ss = final["voe:mirror:shipwreck-strand"]
    assert ss["yields"] == [{"node_id": "item:106385", "count": 50}] and ss["est_minutes"] == 7
    assert [s["name"] for s in ss["mirror_route"]["starts"]] == ["Pub Canach Waypoint", "Dark Leviathan Waypoint"]
    assert final["voe:mirror:starlit-weald"]["guidance"]["waypoint_chat_code"] == "[&BMAPAAA=]"   # Foothold Bivouac
    assert final["voe:mirror:eternitys-garden"]["mirror_route"]["large_rule"] == "none"
    for n in nodes.values():
        assert [s["kind"] for s in n["sources"]] == ["activity"]


def test_copy_assets(tmp_path):
    assert mr.copy_assets(tmp_path) == []
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(mr.asset_names())
