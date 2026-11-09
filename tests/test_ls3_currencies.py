import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import ls3_currencies as ls3  # noqa: E402

MAPS = [{"id": 1165, "name": "Bloodstone Fen", "centre": [100, 100],
         "waypoints": [["Far", 500, 500, "[&FAR]"], ["Near", 110, 95, "[&NEAR]"]]}]


def test_activity_per_needed_item_only():
    acts = ls3.activities(MAPS, {"item:79280"})
    assert [a["id"] for a in acts] == ["ls3:gather:bloodstone_fen"]
    a = acts[0]
    assert a["est_minutes"] == 15 and a["max_runs_per_day"] == 1 and a["map_ids"] == [1165]
    assert a["yields"] == [{"node_id": "item:79280", "count": 10}]
    assert a["guidance"]["waypoint_chat_code"] == "[&NEAR]"


def test_apply_replaces_other_source():
    nodes = {"item:79280": {"sources": [{"kind": "other", "inputs": []}, {"kind": "vendor", "inputs": []}]}}
    final = {}
    assert ls3.apply(final, nodes, MAPS) == 1
    assert nodes["item:79280"]["sources"][-1] == {"kind": "activity", "inputs": [], "activity_id": "ls3:gather:bloodstone_fen"}
    assert all(s["kind"] != "other" for s in nodes["item:79280"]["sources"])
    assert "ls3:gather:bloodstone_fen" in final


def test_all_six_maps_configured_with_owner_numbers():
    got = {c["item_name"]: c["gather"] for c in ls3.CONFIG["maps"].values()}
    assert got == {"Blood Ruby": 10, "Petrified Wood": 15, "Fresh Winterberry": 35,
                   "Jade Shard": 15, "Fire Orchid Blossom": 35, "Orrian Pearl": 15}
