import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import castora  # noqa: E402
import wiki_pack as wp  # noqa: E402
import build_pack as bp  # noqa: E402

SNAP = Path(__file__).resolve().parent.parent / "snapshot"


def page(title):
    store = wp.PageStore(SNAP, lambda url: (_ for _ in ()).throw(RuntimeError("offline")))
    store.down = True
    return store.pages([title])[title].text


def test_guaranteed_counts_prosperity_unending_and_skips_chances():
    text = """== Rewards ==
* {{rewards item|Shadowstone Fragment|qty=5|notes=daily chest, 10 with [[Prosperity Unending]] mastery}}
* {{rewards item|Chromatic Sap|qty=1|notes=doubled with [[Prosperity Unending]] mastery}}
* {{rewards item|Shadowstone Research Data|qty=2-3|chance=guaranteed}}
*** Low chance to be replaced by {{rewards item|Shadowstone Research Data|qty=15|chance=chance}}
<!-- {{rewards item|Shadowstone Research Data|qty=99}} -->
* {{rewards item|Antiquated Ducats|qty=25}}
== Walkthrough ==
* {{rewards item|Antiquated Ducat|qty=1000}}
"""
    assert castora.guaranteed(text, "Shadowstone Fragment") == 10
    assert castora.guaranteed(text, "Chromatic Sap") == 2
    assert castora.guaranteed(text, "Shadowstone Research Data") == 2.5
    assert castora.guaranteed(text, "Antiquated Ducat") == 25   # plural name, other sections ignored


def test_snapshot_pages_give_the_research_note_amounts():
    assert castora.guaranteed(page("Hammerhart Rumble!"), "Chromatic Sap") == 2
    assert castora.guaranteed(page("Secrets of the Weald"), "Raw Enchanting Stone") == 1.5
    assert castora.guaranteed(page("Shackles of the Ancients"), "Shadowstone Fragment") == 20
    assert castora.guaranteed(page("Depths of Cruelty"), "Shadowstone Research Data") == 22.5
    assert castora.guaranteed(page("Seer Artifact Chest"), "Raw Enchanting Stone") == 3
    assert castora.guaranteed(page("Seer Storage Chest"), "Raw Enchanting Stone") == 1
    assert castora.choice_options(page("Castora: Hero's Choice Chest")) == {"chromaticsap": 25, "rawenchantingstone": 25, "chromaticresinbox": 1}
    lh = castora.choice_options(page("Leyspring Hollows: Hero's Choice Chest"))
    assert lh["shadowstonefragment"] == 20 and lh["shadowstoneresearchdata"] == 10
    tiers = castora.tier_rewards(page("Starlit Weald (achievements)"))
    assert tiers[(9057, "rawenchantingstone")] == 10


def test_config_expectations_match_the_pages():
    for m in castora.CONFIG["metas"].values():
        for f in m["facts"]:
            assert castora.guaranteed(page(f["page"]), f["item"]) == f["expect"], f["page"]
    for a in castora.CONFIG["activities"]:
        for f in a["facts"]:
            assert castora.guaranteed(page(f["page"]), f["item"]) * f.get("chests", 1) == f["expect"], f["page"]


def test_armor_set_fields():
    item = {"type": "Armor", "details": {"type": "Coat", "weight_class": "Heavy"}}
    assert bp.armor_set_fields("Sublime Mistforged Triumphant Hero's Breastplate", item) == \
        {"armor_set": "Mistforged Triumphant Hero's", "weight_class": "Heavy"}
    assert bp.armor_set_fields("Triumphant Hero's Breastplate", item)["armor_set"] == "Triumphant Hero's"
    assert bp.armor_set_fields("Selachimorpha", {"type": "Armor", "details": {"type": "HelmAquatic", "weight_class": "Heavy"}}) == {}
    assert bp.armor_set_fields("Eikasia, Mists-Grasper", {"type": "Armor", "details": {"type": "Gloves", "weight_class": "Clothing"}}) == {}
