import json
import sys
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import wiki_sources as ws  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
CUR = {"Coin": 1, "Karma": 2, "Spirit Shard": 23, "Trade Contract": 34}


def reply(name):
    return json.loads((FIX / f"smw_{name}.json").read_text())


def test_vendor_sources_group_costs_and_resolve_currencies():
    rows = [r for r in ws.rows(reply("ask_vendor_multi")) if r["Sells item"] == ["Mystic Runestone"]]
    srcs, cost_items = ws.vendor_sources("Mystic Runestone", rows, CUR)
    # "1 Gold" and "10000 Coin" are the same price: one source, every vendor listed under it
    coin = [s for s in srcs if s["inputs"] == [{"node_id": "currency:1", "count": 10000}]]
    free = [s for s in coin if not any(k in s for k in ("daily_cap", "weekly_cap", "max_units"))]
    assert len(free) == 1 and free[0]["output_count"] == 1                # capped offers are separate sources
    npcs = [v["npc"] for v in free[0]["vendors"]]
    assert "Miyani" in npcs and not free[0]["vendors"][0]["requires"]      # no-requirement vendors first
    assert len({json.dumps({k: v for k, v in s.items() if k != "vendors"}, sort_keys=True) for s in srcs}) == len(srcs)


def test_vendor_cost_items_join_the_walk():
    rows = [r for r in ws.rows(reply("ask_vendor_multi")) if r["Sells item"] == ["Funerary Incense"]]
    srcs, cost_items = ws.vendor_sources("Funerary Incense", rows, CUR)
    ids = [i["node_id"] for s in srcs for i in s["inputs"]]
    assert any(s.get("daily_cap") for s in srcs)                           # purchase caps kept
    assert "currency:34" in ids and "@Obsidian Shard" in ids
    assert set(cost_items) >= {"Obsidian Shard", "Glob of Ectoplasm", "Amalgamated Gemstone"}


def test_activity_rows_tracks_and_places():
    tracks = ws.rows(reply("ask_rt_battle"))
    acts = ws.activity_rows("Gift of Battle", [], tracks)
    assert acts == [{"id": "rt:gift_of_battle_item_reward_track:wvw", "name": "Gift of Battle Item Reward Track (WvW)",
                     "mode": "wvw", "count": 1, "chance": "Guaranteed", "note": "reward track, reward 40"}]
    places = ws.activity_rows("Gift of Tarir", ws.rows(reply("ask_rewards_tarir")), [])
    assert {a["id"] for a in places if a["chance"] == "Guaranteed"} >= {"wiki:auric_basin", "wiki:map_completion"}


def test_achievement_sources():
    rows = [{"_subject": "Nevermore IV: The Raven Spirit", "Gives item": ["Gift of the Raven Spirit"], "Has game id": [2550],
             "Has canonical name": ["Nevermore IV: The Raven Spirit"], "Has achievement page": [], "Is historical": ["f"]}]
    assert ws.achievement_sources("Gift of the Raven Spirit", rows) == [
        {"kind": "reward", "inputs": [], "achievement_id": 2550, "achievement_name": "Nevermore IV: The Raven Spirit",
         "wiki_page": "Nevermore IV: The Raven Spirit"}]


class FakeAsk:
    def __init__(self):
        self.down = False
        self.calls = 0

    def __call__(self, url):
        if self.down:
            raise OSError("down")
        self.calls += 1
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["query"][0]
        assert "Sells item::Mystic Runestone||Gift of Glory" in q
        return reply("ask_vendor_multi")


def test_store_splits_per_item_and_falls_back(tmp_path):
    fake = FakeAsk()
    s = ws.SourceStore(tmp_path, fake)
    got = s.get("vendor", ["Mystic Runestone", "Gift of Glory"])
    assert got["Gift of Glory"] and all(r["Sells item"] == ["Gift of Glory"] for r in got["Gift of Glory"])
    assert fake.calls == 1
    s2 = ws.SourceStore(tmp_path, fake)        # fresh: from the store, no request
    assert s2.get("vendor", ["Gift of Glory"]) == {"Gift of Glory": got["Gift of Glory"]} and fake.calls == 1
    for p in (tmp_path / "wiki-sources").glob("*.json"):   # old answers + wiki down: stored ones are used
        d = json.loads(p.read_text()); d["fetched"] = 0; p.write_text(json.dumps(d))
    fake.down = True
    s3 = ws.SourceStore(tmp_path, fake)
    assert s3.get("vendor", ["Mystic Runestone"])["Mystic Runestone"] and s3.down
    with pytest.raises(ws.WikiSourcesDown):
        s3.get("vendor", ["Never Asked"])
