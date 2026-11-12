import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import collection_gifts as cg  # noqa: E402
import wiki_pack as wp  # noqa: E402

CUR = {"Coin": 1, "Karma": 2, "Research Note": 61}


class Store:
    """PageStore stand-in: title -> wikitext; missing titles left out (like the real one)."""
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


def item(iid, recipe=""):
    return f"{{{{Item infobox\n| id = {iid}\n| type = Weapon\n}}}}\n{recipe}"


def cost(*pairs):
    return [{"Has item currency": c, "Has item value": v} for c, v in pairs]


def vendor(npc, *pairs, requires=()):
    return {"_subject": f"{npc}#1", "Has vendor": [npc], "Has item cost": cost(*pairs), "Located in": ["Somewhere"],
            "Has requirement": list(requires)}


PAGES = {
    "Shining Ward Axe": item(901, "{{Recipe\n| source = recipe sheet\n| disciplines = Weaponsmith\n| ingredient1 = 1 Shining Ward Weapon Shard\n"
                                  "| ingredient2 = 10 Glob of Ectoplasm\n| ingredient3 = 250 Research Note\n}}"),
    "Shining Ward Dagger": item(902, "{{Recipe\n| source = recipe sheet\n| ingredient1 = 1 Shining Ward Weapon Shard\n}}"),
    "Recipe: Shining Ward Axe": item(911),
    "Shining Ward Weapon Shard": item(920),
    "Glob of Ectoplasm": item(19721),
    "Shadowstone Research Data": item(110156),
    "Tenebral Ward Medium Helmet": item(930),
    "Tenebral Ward Medium Gloves": item(931),
    "Tenebral Ward Token: Helmet": item(940),
    "Tenebral Ward Token: Gloves": item(941),
}
ROWS = {
    ("vendor", "Shining Ward Weapon Shard"): [vendor("Acolyte", ("Shadowstone Research Data", 5))],
    ("vendor", "Recipe: Shining Ward Axe"): [vendor("Acolyte", ("Shadowstone Research Data", 5))],
    ("vendor", "Glob of Ectoplasm"): [vendor("Ecto seller", ("Coin", 5000))],
    ("vendor", "Shadowstone Research Data"): [vendor("Data seller", ("Karma", 100))],
    ("vendor", "Tenebral Ward Medium Helmet"): [
        vendor("Alliance Field Quartermaster", ("Gold", 100), ("Shadowstone Research Data", 150), ("Karma", 70000)),
        vendor("Alliance Field Quartermaster", ("Tenebral Ward Token: Helmet", 1), ("Karma", 35000))],
    ("vendor", "Tenebral Ward Medium Gloves"): [
        vendor("Alliance Field Quartermaster", ("Gold", 150), ("Shadowstone Research Data", 150), ("Karma", 70000)),
        vendor("Alliance Field Quartermaster", ("Tenebral Ward Token: Gloves", 1), ("Karma", 35000))],
    ("achievement", "Tenebral Ward Token: Helmet"): [{"_subject": "Everything That We Are", "Has game id": 9001,
                                                       "Has canonical name": ["Everything That We Are"]}],
    # Token: Gloves: chance drops only -> no source -> its vendor route drops out
}


def config(weapons, armor):
    c = copy.deepcopy(cg.CONFIG)
    c["collections"][0]["items"] = weapons
    c["collections"][0]["items_from_api"] = False
    c["collections"][1]["items"] = armor
    return c


def gift_nodes():
    def gift(iid, name, ach, ach_name):
        return {"id": f"item:{iid}", "type": "drop", "name": name, "item_id": iid, "sources": [
            {"kind": "vendor", "inputs": [{"node_id": "currency:1", "count": 250000}, {"node_id": "currency:2", "count": 300000}],
             "output_count": 1, "vendors": [{"npc": "Dexx", "locations": [], "requires": [ach_name]}]},
            {"kind": "vendor", "inputs": [{"node_id": "currency:1", "count": 1}], "output_count": 1,
             "vendors": [{"npc": "Other", "locations": [], "requires": ["something else"]}]},
            {"kind": "reward", "inputs": [], "achievement_id": ach, "achievement_name": ach_name, "wiki_page": ach_name}]}
    return {"item:110133": gift(110133, "Gift of Shining Arms", 9437, "Shining Ward Weapon Collector"),
            "item:110033": gift(110033, "Gift of Tenebral Armor", 9363, "Tenebral Ward Armor Collection"),
            "item:19721": {"id": "item:19721", "type": "drop", "name": "Glob of Ectoplasm", "item_id": 19721, "sources": [{"kind": "trading_post", "inputs": []}]}}


def run(monkeypatch, weapons, armor, store=None, get_json=None):
    monkeypatch.setattr(cg, "CONFIG", config(weapons, armor))
    nodes, acts = gift_nodes(), {}
    stats, warn = cg.apply(nodes, acts, {}, get_json or (lambda url: []), store or Store(PAGES), Smw(ROWS), CUR,
                           lambda ids: set(ids), set())
    return nodes, stats, warn


def test_items_become_gift_inputs_on_reward_and_gated_vendor(monkeypatch):
    nodes, stats, warn = run(monkeypatch, ["Shining Ward Axe", "Shining Ward Dagger"],
                             ["Tenebral Ward Medium Helmet", "Tenebral Ward Medium Gloves"])
    arms = nodes["item:110133"]["sources"]
    assert arms[0]["inputs"][2:] == [{"node_id": "item:901", "count": 1}, {"node_id": "item:902", "count": 1}]
    assert arms[0]["inputs"][:2] == [{"node_id": "currency:1", "count": 250000}, {"node_id": "currency:2", "count": 300000}]
    assert arms[1]["inputs"] == [{"node_id": "currency:1", "count": 1}]           # other requirement: untouched
    assert arms[2]["inputs"] == [{"node_id": "item:901", "count": 1}, {"node_id": "item:902", "count": 1}]
    assert arms[2]["step_minutes"] == cg.CONFIG["step_minutes"]
    armor = nodes["item:110033"]["sources"]
    assert [i["node_id"] for i in armor[2]["inputs"]] == ["item:930", "item:931"]
    assert stats["Gift of Shining Arms"]["sources_changed"] == 2 and stats["Gift of Tenebral Armor"]["sources_changed"] == 2


def test_weapon_tree_recipe_sheet_and_existing_nodes_kept(monkeypatch):
    nodes, stats, _ = run(monkeypatch, ["Shining Ward Axe", "Shining Ward Dagger"], ["Tenebral Ward Medium Helmet"])
    axe = nodes["item:901"]
    craft = next(s for s in axe["sources"] if s["kind"] == "craft")
    ids = {i["node_id"]: i["count"] for i in craft["inputs"]}
    assert ids == {"item:920": 1, "item:19721": 10, "currency:61": 250, "item:911": 1}   # recipe sheet joined the craft
    assert stats["Gift of Shining Arms"]["recipe_sheets"] == 1                              # Dagger has no sheet page
    assert nodes["item:920"]["sources"][0]["inputs"] == [{"node_id": "item:110156", "count": 5}]
    assert nodes["item:19721"]["sources"] == [{"kind": "trading_post", "inputs": []}]      # the pack's node stays


def test_tokens_only_where_guaranteed(monkeypatch):
    nodes, _, _ = run(monkeypatch, ["Shining Ward Axe"], ["Tenebral Ward Medium Helmet", "Tenebral Ward Medium Gloves"])
    helm = [s for s in nodes["item:930"]["sources"] if s["kind"] == "vendor"]
    gloves = [s for s in nodes["item:931"]["sources"] if s["kind"] == "vendor"]
    assert len(helm) == 2 and any(i["node_id"] == "item:940" for s in helm for i in s["inputs"])
    assert len(gloves) == 1   # Token: Gloves has only chance drops: no source, so its route is gone
    assert {(i["node_id"], i["count"]) for i in gloves[0]["inputs"]} == {("currency:1", 1500000), ("currency:2", 70000), ("item:110156", 150)}
    assert nodes["item:940"]["sources"][0]["achievement_id"] == 9001


def test_collection_applied_whole_or_not_at_all(monkeypatch):
    nodes, stats, warn = run(monkeypatch, ["Shining Ward Axe", "Shining Ward Nonexistent"], ["Tenebral Ward Medium Helmet"])
    assert nodes["item:110133"]["sources"] == gift_nodes()["item:110133"]["sources"]
    assert any("Shining Ward Nonexistent" in w for w in warn)
    assert "item:901" not in nodes                                                          # nothing merged
    assert stats["Gift of Tenebral Armor"]["sources_changed"] == 2                          # the other one still applies


def test_wiki_down_keeps_old_sources(monkeypatch):
    nodes, _, warn = run(monkeypatch, ["Shining Ward Axe"], ["Tenebral Ward Medium Helmet"], store=Store(PAGES, down=True))
    assert nodes == gift_nodes()
    assert sum("not walked" in w for w in warn) == 2


def test_armor_price_check_warns_on_difference(monkeypatch):
    _, _, warn = run(monkeypatch, ["Shining Ward Axe"], ["Tenebral Ward Medium Helmet", "Tenebral Ward Medium Gloves"])
    assert not any("vendor price" in w for w in warn)           # both match the research note
    rows = copy.deepcopy(ROWS)
    rows[("vendor", "Tenebral Ward Medium Gloves")][0]["Has item cost"][0]["Has item value"] = 175
    monkeypatch.setattr(cg, "CONFIG", config(["Shining Ward Axe"], ["Tenebral Ward Medium Gloves"]))
    _, warn = cg.apply(gift_nodes(), {}, {}, lambda url: [], Store(PAGES), Smw(rows), CUR, lambda ids: set(ids), set())
    assert any("Tenebral Ward Medium Gloves has no vendor price" in w for w in warn)


def test_api_items_from_skin_bits():
    replies = {
        "https://api.guildwars2.com/v2/achievements?ids=9437": [{"id": 9437, "bits": [{"type": "Skin", "id": 2}, {"type": "Skin", "id": 1}]}],
        "https://api.guildwars2.com/v2/skins?ids=2,1": [{"id": 1, "name": "Shining Ward Axe"}, {"id": 2, "name": "Shining Ward Dagger"}],
    }
    assert cg.api_items(replies.get, 9437) == ["Shining Ward Dagger", "Shining Ward Axe"]
    assert cg.api_items(lambda url: [], 9363) is None


def test_probe_reply_matches_config():
    import json
    probe = Path(__file__).resolve().parent.parent / "probe" / "out"
    ach = json.loads((probe / "cg_api_achievements.json").read_text(encoding="utf-8"))["reply"]
    skins = json.loads((probe / "cg_api_skins.json").read_text(encoding="utf-8"))["reply"]
    replies = {f"https://api.guildwars2.com/v2/achievements?ids=9437": [a for a in ach if a["id"] == 9437]}
    ids = ",".join(str(b["id"]) for b in replies["https://api.guildwars2.com/v2/achievements?ids=9437"][0]["bits"])
    replies[f"https://api.guildwars2.com/v2/skins?ids={ids}"] = skins
    assert sorted(cg.api_items(replies.get, 9437)) == sorted(cg.CONFIG["collections"][0]["items"])
    assert not [a for a in ach if a["id"] == 9363]   # the API does not serve the armour collection
