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
    assert warn == [] and stats == {"activities": 1, "generic_sources_dropped": 2}
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
