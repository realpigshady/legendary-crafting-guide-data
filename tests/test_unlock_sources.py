import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import unlock_sources as us  # noqa: E402
import vendor_waypoints as vwp  # noqa: E402
import wiki_pack as wp  # noqa: E402


class Store:
    def __init__(self, pages, down=False):
        self.p, self.down_, self.redirects = pages, down, {}

    def pages(self, titles):
        if self.down_:
            raise wp.WikiDown("down")
        return {t: wp.Page(t, 1, self.p[t]) for t in titles if t in self.p}


class Smw:
    def __init__(self, rows):
        self.r = rows

    def get(self, kind, names):
        return {n: self.r.get((kind, n), []) for n in names}


THEORY = """{{Item infobox
| type = consumable
| id = 70665
}}

== Acquisition ==
* Talking to [[Kelvei, the Legendary Observer]] after completing [[Ad Infinitum I: Finite Result]].
"""
KELVEI = "{{NPC infobox\n| location = Mistlock Observatory\n| race = Asura\n}}\n"


def solution_nodes():
    return {"item:73201": {"id": "item:73201", "type": "drop", "name": "Solution: Upper Bound", "item_id": 73201,
                           "sources": [{"kind": "other", "inputs": []}]}}


ROWS = {
    ("contained", "Solution: Upper Bound"): [{"_subject": "Proof: Upper Bound#contains2", "Contained in": ["Proof: Upper Bound"],
                                              "Has contained item chance": ["Guaranteed"], "Has contained item quantity": ["1"],
                                              "Is historical": ["f"]}],
    ("achievement", "Proof: Upper Bound"): [{"_subject": "Legendary Backpacks#achievement2557", "Gives item": ["Proof: Upper Bound"],
                                             "Has game id": [2557], "Has canonical name": ["Ad Infinitum II: Upper Bound"],
                                             "Has achievement page": ["Ad Infinitum II: Upper Bound"], "Is historical": ["f"]}],
}


def test_solution_gets_the_reward_of_the_achievement_that_gives_its_proof():
    nodes = solution_nodes()
    store = Store({"Theory of the Upper Bound": THEORY, "Kelvei, the Legendary Observer": KELVEI})
    stats, warn, fixed = us.apply_reward_containers(nodes, store, Smw(ROWS))
    assert warn == [] and fixed == ["Solution: Upper Bound"] and stats == {"items": 1, "sources_added": 1}
    (src,) = nodes["item:73201"]["sources"]
    assert src["kind"] == "reward" and src["achievement_id"] == 2557 and src["inputs"] == []
    assert src["achievement_name"] == "Ad Infinitum II: Upper Bound" and src["via_container"] == "Proof: Upper Bound"
    assert src["unlock"] == {"item": "Theory of the Upper Bound", "item_id": 70665, "npc": "Kelvei, the Legendary Observer",
                             "locations": ["Mistlock Observatory"], "after_achievement": "Ad Infinitum I: Finite Result"}
    # idempotent: a second run adds nothing
    us.apply_reward_containers(nodes, store, Smw(ROWS))
    assert len(nodes["item:73201"]["sources"]) == 1


def test_no_guaranteed_contents_row_leaves_the_item():
    nodes = solution_nodes()
    rows = dict(ROWS)
    rows[("contained", "Solution: Upper Bound")] = [dict(ROWS[("contained", "Solution: Upper Bound")][0], **{"Has contained item chance": ["Chance"]})]
    _, warn, fixed = us.apply_reward_containers(nodes, Store({}), Smw(rows))
    assert fixed == [] and nodes["item:73201"]["sources"] == [{"kind": "other", "inputs": []}] and "guaranteed" in warn[0]


def test_no_achievement_for_the_box_leaves_the_item():
    nodes = solution_nodes()
    rows = {k: v for k, v in ROWS.items() if k[0] != "achievement"}
    _, warn, fixed = us.apply_reward_containers(nodes, Store({}), Smw(rows))
    assert fixed == [] and "Gives item" in warn[0]


def item(iid):
    return f"{{{{Item infobox\n| id = {iid}\n| type = Trophy\n}}}}\n"


REQ = "bringing her all four statuettes for the week: Jade Empress Statuette, Jade Gate Statuette, Jade Cathedral Statuette, Jade Dragon Statuette"
STATUETTES = {"Jade Empress Statuette": 95872, "Jade Gate Statuette": 97384, "Jade Cathedral Statuette": 97185, "Jade Dragon Statuette": 97724}


def blessing():
    return {"item:97829": {"id": "item:97829", "type": "drop", "name": "Blessing of the Jade Empress", "item_id": 97829,
                           "sources": [{"kind": "vendor", "inputs": [{"node_id": "currency:68", "count": 500}], "output_count": 1,
                                        "vendors": [{"npc": "Myung-Hee", "locations": ["Shing Jea Monastery"], "requires": [REQ]}]}]}}


def api(bits):
    return lambda url: [{"id": 6436, "bits": [{"type": "Item", "id": i} for i in bits]}]


def test_vendor_unlock_from_requirement_items():
    nodes, acts = blessing(), {}
    store = Store({n: item(i) for n, i in STATUETTES.items()})
    stats, warn = us.apply_vendor_unlocks(nodes, acts, {}, api(STATUETTES.values()), store, Smw({}), {}, lambda ids: set(), set())
    assert warn == []
    src = nodes["item:97829"]["sources"][0]
    assert src["inputs"] == [{"node_id": "currency:68", "count": 500}]   # the price is unchanged
    u = src["unlock"]
    assert u["achievement_id"] == 6436 and u["achievement_name"] == "The Jade Vault" and u["items_per"] == "week"
    assert [x["node_id"] for x in u["items"]] == ["item:95872", "item:97384", "item:97185", "item:97724"]
    for i in STATUETTES.values():   # walked: tradeable, so a Trading Post source
        assert any(s["kind"] == "trading_post" for s in nodes[f"item:{i}"]["sources"])
    assert stats["Myung-Hee"] == {"items": 4, "nodes_added": 4, "sources_changed": 1}


def test_vendor_unlock_warns_when_api_bits_differ():
    nodes = blessing()
    store = Store({n: item(i) for n, i in STATUETTES.items()})
    _, warn = us.apply_vendor_unlocks(nodes, {}, {}, api([95872, 97384, 97185]), store, Smw({}), {}, lambda ids: set(), set())
    assert len(warn) == 1 and "Jade Dragon Statuette" in warn[0] and "unlock" in nodes["item:97829"]["sources"][0]


def test_vendor_unlock_needs_every_item_page():
    nodes = blessing()
    store = Store({n: item(i) for n, i in STATUETTES.items() if n != "Jade Dragon Statuette"})
    _, warn = us.apply_vendor_unlocks(nodes, {}, {}, api(STATUETTES.values()), store, Smw({}), {}, lambda ids: set(), set())
    assert "no unlock added" in warn[0] and "unlock" not in nodes["item:97829"]["sources"][0]


def test_instance_entrance_and_unlock_npc_waypoints():
    maps = [{"id": 50, "name": "Lion's Arch", "centre": [0, 0], "sectors": [["Fort Marriner", 10, 10]],
             "waypoints": [["Fort Marriner Waypoint", 11, 11, "[&BDAEAAA=]"], ["Far Waypoint", 900, 900, "[&X=]"]]},
            {"id": 957, "name": "Fractals of the Mists", "centre": [5, 5], "sectors": [],
             "waypoints": [["Thaumanova Reactor", 5, 5, "[&BDAHAAA=]"]]}]
    nodes = [{"sources": [{"kind": "vendor", "vendors": [{"npc": "BUY-4373", "locations": ["Mistlock Observatory", "Mistlock Sanctuary"]}]},
                          {"kind": "reward", "unlock": {"npc": "Kelvei", "locations": ["Mistlock Observatory"]}}]}]
    vwp.annotate(nodes, maps, None)
    for v in (nodes[0]["sources"][0]["vendors"][0], nodes[0]["sources"][1]["unlock"]):
        assert v["waypoint_chat_code"] == "[&BDAEAAA=]" and v["map"] == "Lion's Arch"
