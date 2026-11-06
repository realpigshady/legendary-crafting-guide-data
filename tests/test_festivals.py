import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import festivals as fe  # noqa: E402

# shape of the Wintersday / Lunar New Year pages (2026-11-06)
PAGE = """[[Wintersday]] is an annual [[festival]].

== Releases ==
* [[Wintersday: The Wondrous Workshop of Toymaker Tixx]] <small>(2012-12-14 — 2013-01-03)</small>
* [[Wintersday 2013|A Very Merry Wintersday 2013]] <small>(2013-12-10 — 2014-01-21)</small>
* [[Lunar New Year 2026]]: Year of the Horse <small>(2026-02-03 — 2026-02-24)</small>
* [[A Very Merry Wintersday 2026]] <small>(2026-12-08 — 2027-01-05)</small>
* [[Wintersday 2027]] <small>(2027-12-07 — )</small>

== Activities ==
* [[Not a release]] <small>(2020-01-01 — 2020-01-02)</small>
"""


def test_parse_windows():
    ws = fe.parse_windows(PAGE)
    assert [w["release"] for w in ws] == ["Wintersday: The Wondrous Workshop of Toymaker Tixx", "Wintersday 2013",
                                          "Lunar New Year 2026", "A Very Merry Wintersday 2026", "Wintersday 2027"]
    assert ws[3] == {"release": "A Very Merry Wintersday 2026", "start": "2026-12-08", "end": "2027-01-05"}
    assert ws[4]["end"] is None          # announced, end not given yet
    assert fe.parse_windows("no releases here") == []


def test_canonical_and_categories():
    assert fe.canonical("Festival of the four Winds") == "Festival of the Four Winds"
    assert fe.canonical("Mad King") is None
    assert fe.festival_of(["Category:Halloween", "Category:Temporary content"]) == "Halloween"
    assert fe.festival_of(["Category:Halloween"]) is None                       # permanent page in the category
    assert fe.festival_of(["Category:Halloween"], need_temporary=False) == "Halloween"
    assert fe.festival_of(["Category:Halloween", "Category:Wintersday", "Category:Temporary content"]) is None


def test_tag():
    temp = "Category:Temporary content"
    cats = {
        "Skiff Race": [],
        "Labyrinthine Cliffs": ["Category:Festival of the Four Winds", temp],
        "Halloween Reward Track": ["Category:Halloween", temp],
        "Bazaar Docks": ["Category:Festival of the Four Winds", temp],
        "Hooligan's Route": ["Category:Lion's Arch"],
        "Zalman": ["Category:Festival of the Four Winds"],
        "Lonely Trader": ["Category:Wintersday"],
    }
    acts = [
        {"id": "evt:festival-lc:skiff_race", "name": "Labyrinthine Cliffs: Skiff Race", "_page": "Skiff Race", "_map": "Labyrinthine Cliffs"},
        {"id": "evt:festival-db:x", "name": "Dragon Bash: X", "_page": "X", "_map": "Dragon Bash"},
        {"id": "loc:toypocalypse", "name": "Toypocalypse", "_cats": ["Category:Wintersday", temp]},
        {"id": "loc:fractal", "name": "Fractal", "_cats": ["Category:Fractals"]},
        {"id": "rt:halloween_reward_track:pvp", "name": "Halloween Reward Track (PvP)"},
        {"id": "wiki:queensdale", "name": "Queensdale"},
        {"id": "rt:super_adventure_reward_track:wvw", "name": "Super Adventure Reward Track (WvW)"},
        {"id": "rt:ascalon_reward_track:pvp", "name": "Ascalon Reward Track (PvP)"},
        {"id": "wiki:wintersday_gifts", "name": "Wintersday Gifts"},
    ]
    v = lambda npc, locs, req=(): {"npc": npc, "locations": list(locs), "requires": list(req)}
    nodes = [{"id": "item:1", "sources": [
        {"kind": "vendor", "vendors": [v("Zalman", ["Bazaar Docks"])]},
        {"kind": "vendor", "vendors": [v("Halloween Vendor", ["Hooligan's Route"]),
                                       v("Court Merchant", ["Mad King's Labyrinth"], ["the festival Halloween"])]},
        {"kind": "vendor", "vendors": [v("New Year Rewards", ["The Crown Pavilion"], ["the festival lunar new year"])]},
        {"kind": "vendor", "vendors": [v("Lonely Trader", [])]},
        {"kind": "vendor", "vendors": [v("Odd", [], ["the festival Moon Fest"])]},
        {"kind": "craft"},
    ]}]
    asked = []

    def cats_of(pages):
        asked.extend(pages)
        return {p: [{"_subject": p, "Category": cats[p]}] if p in cats else [] for p in pages}

    st = fe.tag(acts, nodes, cats_of)
    assert "Toypocalypse" not in asked                       # places carry their categories
    assert [a.get("festival") for a in acts] == ["Festival of the Four Winds", "Dragon Bash", "Wintersday", None, "Halloween", None,
                                                 "Super Adventure Festival", None, None]
    s = nodes[0]["sources"]
    assert s[0]["festival"] == "Festival of the Four Winds"   # every location is festival content
    assert "festival" not in s[1]                             # a year-round vendor sells it too
    assert s[1]["vendors"][1]["festival"] == "Halloween" and "festival" not in s[1]["vendors"][0]
    assert s[2]["festival"] == "Lunar New Year"               # requirement, any case
    assert s[3]["festival"] == "Wintersday"                   # no location: the NPC page
    assert s[4]["festival"] == "Moon Fest" and st["unknown_festivals"] == {"Moon Fest": 1}
    assert "festival" not in s[5]
    assert st["by_festival"]["Halloween"] == {"activities": 1, "vendor_sources": 0, "vendors": 1}


def test_pagecats_fetch(tmp_path):
    import wiki_sources as ws
    calls = []

    def post(params):
        calls.append(params)
        if "clcontinue" not in params:
            return {"continue": {"clcontinue": "x", "continue": "||"},
                    "query": {"normalized": [{"from": "zalman", "to": "Zalman"}],
                              "redirects": [{"from": "Halloween Vendor", "to": "Halloween Vendor (Lion's Arch)"}],
                              "pages": [{"title": "Zalman", "categories": [{"title": "Category:Merchants"}]},
                                        {"title": "Halloween Vendor (Lion's Arch)", "categories": []},
                                        {"title": "Nope", "missing": True}]}}
        return {"query": {"pages": [{"title": "Zalman", "categories": [{"title": "Category:Festival of the Four Winds"}]}]}}

    st = ws.SourceStore(tmp_path, post)
    got = st.get("pagecats", ["zalman", "Halloween Vendor", "Adventure#Section"])
    assert got["zalman"][0]["Category"] == ["Category:Festival of the Four Winds", "Category:Merchants"]
    assert got["Halloween Vendor"][0]["Category"] == []
    assert got["Adventure#Section"] == []
    assert len(calls) == 2 and "Adventure" in calls[0]["titles"]
    st2 = ws.SourceStore(tmp_path, lambda p: (_ for _ in ()).throw(OSError("down")))
    assert st2.get("pagecats", ["zalman"]) == {"zalman": got["zalman"]}   # stored per title
