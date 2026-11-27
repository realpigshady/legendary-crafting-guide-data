import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import map_completion as mc  # noqa: E402

MAPS = [{"id": 1550, "name": "Lowland Shore", "centre": [0, 0], "waypoints": [["Near", 1, 1, "[&NEAR]"]]}]


def _pack():
    nodes = {"item:102929": {"id": "item:102929", "name": "Gift of Lowland Shore",
                             "sources": [{"kind": "activity", "inputs": [], "activity_id": "wiki:lowland_shore"},
                                         {"kind": "activity", "inputs": [], "activity_id": "wiki:map_completion"}]},
             "item:86010": {"id": "item:86010", "name": "Gift of the Oasis",
                            "sources": [{"kind": "vendor", "inputs": []},
                                        {"kind": "activity", "inputs": [], "activity_id": "wiki:map_completion"}]}}
    final = {"wiki:lowland_shore": {"id": "wiki:lowland_shore", "name": "Lowland Shore", "mode": "pve", "expansion": "core",
                                    "yields": [{"node_id": "item:102929", "count": 1}], "guidance": {}},
             "wiki:map_completion": {"id": "wiki:map_completion", "name": "Map completion", "mode": "pve", "expansion": "core",
                                     "yields": [{"node_id": "item:102929", "count": 1}, {"node_id": "item:86010", "count": 1}]}}
    return final, nodes


def test_generic_activity_dropped_and_zone_enriched():
    final, nodes = _pack()
    stats, warn = mc.apply(final, nodes, MAPS)
    assert warn == [] and stats == {"activities": 1, "generic_sources_dropped": 2, "lantern_activities": 0}
    assert "wiki:map_completion" not in final
    a = final["wiki:lowland_shore"]
    assert a["name"] == "Map completion: Lowland Shore" and a["expansion"] == "jw"
    assert a["est_minutes"] == 60 and a["map_ids"] == [1550] and a["per_character"] is True
    assert a["guidance"]["waypoint_chat_code"] == "[&NEAR]"
    # Path of Fire gift: bought after map completion, only its vendor source stays
    assert [s["kind"] for s in nodes["item:86010"]["sources"]] == ["vendor"]
    assert [s.get("activity_id") for s in nodes["item:102929"]["sources"]] == ["wiki:lowland_shore"]


def test_owner_minutes_and_ten_maps():
    assert mc.CONFIG["minutes"] == 60
    assert len(mc.CONFIG["maps"]) == 14
    assert {c["expansion"] for c in mc.CONFIG["maps"].values()} == {"hot", "eod", "soto", "jw"}


def _soto_pack():
    gifts = {"item:100544": "Gift of Skywatch Archipelago", "item:101595": "Gift of Inner Nayos"}
    nodes = {i: {"id": i, "name": n, "sources": [{"kind": "vendor", "inputs": []}]} for i, n in gifts.items()}
    nodes["item:100544"]["sources"].append({"kind": "activity", "inputs": [], "activity_id": "wiki:skywatch_archipelago"})
    final = {"wiki:skywatch_archipelago": {"id": "wiki:skywatch_archipelago", "name": "Skywatch Archipelago", "mode": "pve",
                                           "expansion": "core", "yields": [{"node_id": "item:100544", "count": 1}], "guidance": {}}}
    return final, nodes


def test_lantern_achievements_repeat_after_map_completion():
    final, nodes = _soto_pack()
    stats, warn = mc.apply(final, nodes, MAPS)
    assert warn == [] and stats["activities"] == 1 and stats["lantern_activities"] == 2
    sky = final["wiki:illuminating_skywatch_archipelago"]
    assert sky["name"] == "Illuminating Skywatch Archipelago" and sky["repeatable"] is True
    assert sky["est_minutes"] == 25 and sky["map_ids"] == [1510] and sky["expansion"] == "soto"
    assert sky["after_activity"] == "wiki:skywatch_archipelago" and "per_character" not in sky
    assert sky["yields"] == [{"node_id": "item:100544", "count": 1}]
    assert "57 lanterns" in sky["guidance"]["text"]
    assert [s.get("activity_id") for s in nodes["item:100544"]["sources"]] == [
        None, "wiki:skywatch_archipelago", "wiki:illuminating_skywatch_archipelago"]
    # Inner Nayos has no map completion: its lanterns are open from the start
    nay = final["wiki:illuminating_inner_nayos"]
    assert "after_activity" not in nay and nay["map_ids"] == [1526] and "43 lanterns" in nay["guidance"]["text"]
    assert nodes["item:101595"]["sources"][-1]["activity_id"] == "wiki:illuminating_inner_nayos"
    # Amnytas: no tree here needs the gift, so no activity
    assert "wiki:illuminating_amnytas" not in final


def test_lantern_config():
    assert mc.CONFIG["lantern_minutes"] == 25
    assert {k: c["lanterns"] for k, c in mc.CONFIG["lanterns"].items()} == {
        "Illuminating Skywatch Archipelago": 57, "Illuminating Amnytas": 55, "Illuminating Inner Nayos": 43}
    for c in mc.CONFIG["lanterns"].values():
        assert c["after"] is None or c["after"] in mc.CONFIG["maps"]
