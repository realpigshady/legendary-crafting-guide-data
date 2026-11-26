import json
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


SNAP = Path(__file__).resolve().parent.parent / "snapshot"
CUR = {"Karma": 2, "Magnetite Shard": 28, "Legendary Insight": 70}


def _smw():
    import wiki_sources as ws
    def offline(params):
        raise RuntimeError("offline")
    return ws.SourceStore(SNAP, offline)   # the stored wiki answers (vendor rows, 'Contains item' rows)


def test_manfred_offers():
    names = ["Fresh Winterberry", "Kralkatite Ore", "Mistborn Mote"]
    nodes = {f"item:{i}": {"id": f"item:{i}", "name": n, "sources": []} for i, n in enumerate(names, 1)}
    assert ls3.manfred_sources(nodes, _smw(), CUR) == 3
    w = nodes["item:1"]["sources"][0]
    assert w["inputs"] == [{"node_id": "currency:28", "count": 50}, {"node_id": "currency:70", "count": 4}]
    assert w["output_count"] == 100 and w["weekly_cap"] == 6 and "cap_group" not in w
    assert w["offer"] == {"name": "Winterberry Strongbox", "count": 1, "contains": 100}
    k, m = nodes["item:2"]["sources"][0], nodes["item:3"]["sources"][0]
    assert k["output_count"] == 50 and k["cap_group"] == m["cap_group"] == "Manfred Njallson: Season 4 Material Chest (Raid)"
    assert k["offer"] == {"name": "Season 4 Material Chest (Raid)", "count": 1, "contains": 50, "choice": True}
    assert ls3.manfred_sources(nodes, _smw(), CUR) == 0   # idempotent


def test_heart_vendor_bundles_from_wiki():
    """Tester feedback round 1: a bundle's own contents, not one number for every map."""
    names = ["Fresh Winterberry", "Jade Shard", "Petrified Wood"]
    nodes = {f"item:{i}": {"id": f"item:{i}", "name": n, "sources": []} for i, n in enumerate(names, 1)}
    warn = []
    assert ls3.heart_vendor_sources(nodes, _smw(), CUR, warn) == 4 + 6 + 5 and warn == []
    berry, jade, wood = (nodes[f"item:{i}"]["sources"] for i in (1, 2, 3))
    assert all(s["daily_cap"] == 1 and s["inputs"] == [{"node_id": "currency:2", "count": 2688}] for s in berry + jade + wood)
    assert all(len(s["vendors"]) == 1 for s in berry + jade + wood)   # each vendor keeps its own daily limit
    assert {(s["output_count"], json.dumps(s["offer"], sort_keys=True)) for s in berry} == {
        (5, json.dumps({"name": "Bundle of Winterberries", "count": 1, "contains": 5}, sort_keys=True))}
    assert {s["output_count"] for s in jade} == {3} and {s["offer"]["name"] for s in jade} == {"Bundle of Jade Shards"}
    assert [s["output_count"] for s in wood] == [3] * 5   # owner, in game: one bundle of 3 at every vendor
    assert all(s["offer"] == {"name": "Bundle of Petrified Wood", "count": 1, "contains": 3} for s in wood)
    assert all("Bitterfrost Frontier" in s["vendors"][0]["locations"] for s in berry)
    assert ls3.heart_vendor_sources(nodes, _smw(), CUR) == 0


def test_kralkatite_bundle_once_a_day():
    nodes = {"item:1": {"id": "item:1", "name": "Kralkatite Ore", "sources": []}}
    warn = []
    assert ls3.heart_vendor_sources(nodes, _smw(), CUR, warn) == 3 and warn == []
    srcs = nodes["item:1"]["sources"]
    assert all(s["output_count"] == 25 and s["daily_cap"] == 1 for s in srcs)
    assert all(s["offer"] == {"name": "Bundle of Kralkatite", "count": 1, "contains": 25} for s in srcs)


def test_insights_on_existing_places():
    final = {"loc:spirit_vale": {"id": "loc:spirit_vale", "yields": [{"node_id": "currency:28", "count": 1}]},
             "loc:boneskinner": {"id": "loc:boneskinner", "yields": []}}
    nodes = {"currency:70": {"sources": [{"kind": "other", "inputs": []}]}}
    assert ls3.insight_sources(final, nodes) == 4        # 2 places + 2 weekly extras
    assert {"node_id": "currency:70", "count": 4} in final["loc:spirit_vale"]["yields"]
    assert final["loc:boneskinner"]["max_runs_per_week"] == 1
    assert final["li:daily_raid_bounties"]["yields"][0]["count"] == 28
    assert all(s["kind"] == "activity" for s in nodes["currency:70"]["sources"])
    # the addon rejects the whole pack when an activity has no "expansion"
    assert final["li:daily_raid_bounties"]["expansion"] == "hot"
    assert final["li:weekly_quickplay_raid_milestones"]["expansion"] == "hot"


def test_gather_activities_have_expansion():
    acts = ls3.activities(MAPS, {"item:79280"})
    assert all(a.get("expansion") for a in acts)
