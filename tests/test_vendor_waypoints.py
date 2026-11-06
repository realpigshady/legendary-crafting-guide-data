import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import vendor_waypoints as vw  # noqa: E402


def wp(i, name, x, y):
    return {"id": i, "name": name, "type": "waypoint", "coord": [x, y], "chat_link": f"[&WP{i}]"}


def sec(i, name, x, y):
    return {"id": i, "name": name, "coord": [x, y]}


# shapes as /v2/maps?ids=all and /v2/continents/{c}/floors/{f} return them
MAPS_ALL = [
    {"id": 50, "name": "Lion's Arch", "type": "Public", "continent_id": 1, "default_floor": 1, "region_id": 8},
    {"id": 51, "name": "Frostgorge Sound", "continent_id": 1, "default_floor": 1, "region_id": 2},
    {"id": 52, "name": "Memory of Old Lion's Arch", "continent_id": 1, "default_floor": 2, "region_id": 8},
    {"id": 53, "name": "Empty Map", "continent_id": 1, "default_floor": 1, "region_id": 2},
]
FLOOR1 = {"regions": {
    "8": {"maps": {"50": {"name": "Lion's Arch", "continent_rect": [[0, 0], [100, 100]],
                          "sectors": {"1": sec(1, "Trader's Forum", 10, 10), "2": sec(2, "Grand Piazza", 90, 90)},
                          "points_of_interest": {"10": wp(10, "Trader's Forum Waypoint", 12, 12),
                                                 "11": wp(11, "Grand Piazza Waypoint", 88, 88),
                                                 "12": {"id": 12, "name": "Statue", "type": "landmark", "coord": [10, 10]}}}}},
    "2": {"maps": {"51": {"name": "Frostgorge Sound", "continent_rect": [[200, 0], [400, 200]],
                          "sectors": {"3": sec(3, "Jora's Keep", 210, 10), "4": sec(4, "Grand Piazza", 390, 190)},
                          "points_of_interest": {"20": wp(20, "Jora's Waypoint", 215, 15),
                                                 "21": wp(21, "Far Waypoint", 390, 190)}},
                   "53": {"name": "Empty Map", "sectors": {}, "points_of_interest": {}}}},
}}
FLOOR2 = {"regions": {"8": {"maps": {"52": {"name": "Memory of Old Lion's Arch", "continent_rect": [[0, 0], [50, 50]],
                                            "sectors": {"5": sec(5, "Trader's Forum", 20, 20)},
                                            "points_of_interest": {"30": wp(30, "Old Forum Waypoint", 21, 21)}}}}}}
FLOORS = {(1, 1): FLOOR1, (1, 2): FLOOR2}


def maps():
    return vw.extract(MAPS_ALL, FLOORS)


def test_extract_keeps_waypoints_and_sectors_only():
    m = {x["id"]: x for x in maps()}
    assert set(m) == {50, 51, 52}                      # map without waypoints dropped
    assert m[50]["waypoints"] == [["Grand Piazza Waypoint", 88, 88, "[&WP11]"], ["Trader's Forum Waypoint", 12, 12, "[&WP10]"]]
    assert m[50]["centre"] == [50, 50]


def test_area_to_nearest_waypoint_on_its_map():
    f = vw.Finder(maps())
    assert f.find("Jora's Keep") == {"map_id": 51, "map": "Frostgorge Sound", "waypoint_name": "Jora's Waypoint",
                                     "waypoint_chat_code": "[&WP20]"}


def test_ambiguous_name_needs_a_map_hint():
    f = vw.Finder(maps())
    assert f.find("Grand Piazza") is None              # on two maps, different waypoints: no guess
    assert f.find("Trader's Forum") is None
    assert f.find("Trader's Forum (Memory of Old Lion's Arch)")["waypoint_chat_code"] == "[&WP30]"
    assert f.find("Trader's Forum (Lion's Arch)")["waypoint_chat_code"] == "[&WP10]"


def test_map_name_uses_map_centre():
    assert vw.Finder(maps()).find("Lion's Arch")["waypoint_chat_code"] == "[&WP10]"   # equal distance: lowest chat code wins (stable)
    assert vw.Finder(maps()).find("Nowhere") is None


def test_annotate_vendors_and_coverage():
    nodes = [{"id": "item:1", "sources": [{"kind": "vendor", "inputs": [], "vendors": [
        {"npc": "Anette", "locations": ["Nowhere", "Jora's Keep"], "requires": []},
        {"npc": "Bob", "locations": ["Grand Piazza"], "requires": []}]}]}]
    st = vw.annotate(nodes, maps())
    a, b = nodes[0]["sources"][0]["vendors"]
    assert a["waypoint_chat_code"] == "[&WP20]" and a["map"] == "Frostgorge Sound"
    assert "waypoint_chat_code" not in b
    assert st["vendors"] == 2 and st["with_waypoint"] == 1 and st["unresolved_locations"] == {"Grand Piazza": 1}
    vw.annotate(nodes, [])                              # re-run with no index clears old fields
    assert "waypoint_chat_code" not in a


def test_load_uses_fresh_extract_offline_and_fetches_when_old(tmp_path):
    calls = []

    def live(url):
        calls.append(url)
        return MAPS_ALL if "/v2/maps" in url else FLOORS[tuple(int(x) for x in url.split("/continents/")[1].split("/floors/"))]
    got, fresh = vw.load(tmp_path, live, offline=False)
    assert fresh and len(got) == 3 and len(calls) == 3   # maps + 2 floors
    stored = json.loads((tmp_path / vw.INDEX).read_text())
    assert stored["maps"] == got
    calls.clear()
    assert vw.load(tmp_path, live, offline=False) == (got, False) and not calls   # under a week old: no fetch
    stored["fetched_at"] = 0
    (tmp_path / vw.INDEX).write_text(json.dumps(stored))
    assert vw.load(tmp_path, live, offline=True) == (got, False) and not calls    # offline: old extract is fine

    def down(url):
        raise OSError("down")
    assert vw.load(tmp_path, down, offline=False) == (got, False)                 # outage: stored extract


def test_sub_area_climbs_wiki_parents():
    wiki = {"Maker's Terrace": {"Located in": ["Lions Inner Court"], "Has map id": []},
            "Lions Inner Court": {"Located in": ["Lion's Arch"], "Has map id": [50]},
            "Statue Row": {"Located in": ["Grand Piazza"], "Has map id": [51]},
            "Lost Hut": {"Located in": [], "Has map id": [51]}}
    asked = []

    def places(names):
        asked.append(sorted(names))
        return {n: [{"_subject": n, **wiki[n]}] if n in wiki else [] for n in names}
    got = vw.resolve_all(["Maker's Terrace", "Statue Row", "Lost Hut", "Jora's Keep", "Nowhere"], vw.Finder(maps()), places)
    assert got["Jora's Keep"]["waypoint_chat_code"] == "[&WP20]" and asked[0] == ["Lost Hut", "Maker's Terrace", "Nowhere", "Statue Row"]
    assert got["Maker's Terrace"]["map"] == "Lion's Arch"                     # two levels up: the map itself
    assert got["Statue Row"]["waypoint_chat_code"] == "[&WP21]"               # Grand Piazza is on 2 maps: wiki map id picks Frostgorge
    assert got["Lost Hut"]["map_id"] == 51                                    # no parent: the wiki's map, by its centre
    assert got["Nowhere"] is None
    assert vw.resolve_all(["Statue Row"], vw.Finder(maps()), None) == {"Statue Row": None}   # wiki unknown: no guess


def test_map_found_on_another_floor_and_skips_logged():
    moved = [dict(MAPS_ALL[2], default_floor=1)]               # says floor 1, but only floor 2 has it
    assert [m["id"] for m in vw.extract(moved, FLOORS)] == [52]
    sk = []
    vw.extract([{"id": 99, "name": "Ghost", "type": "Public", "continent_id": 1, "default_floor": 1, "region_id": 2}], FLOORS, sk)
    assert sk == ["99 Ghost (1/1)"]


def test_old_extract_version_is_refetched(tmp_path):
    (tmp_path / vw.INDEX).write_text(json.dumps({"fetched_at": 9e12, "maps": []}))   # no "v": older extract
    calls = []

    def live(url):
        calls.append(url)
        return MAPS_ALL if "/v2/maps" in url else FLOORS[tuple(int(x) for x in url.split("/continents/")[1].split("/floors/"))]
    maps_, fresh = vw.load(tmp_path, live, offline=False)
    assert fresh and calls and json.loads((tmp_path / vw.INDEX).read_text())["v"] == vw.VERSION
