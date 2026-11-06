import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import forge_outcomes as fo  # noqa: E402
import wiki_pack as wp  # noqa: E402
import wiki_sources as ws  # noqa: E402

CLOVER_PAGE = """
{{recipe
| name = 1 Mystic Clover or other materials (random)
| source = Mystic Forge
| quantity = 1
| ingredient1 = 1 Obsidian Shard
| ingredient2 = 6 Philosopher's Stone
}}
<div style="display:none;">
{{recipe
| name = Mystic Clover Average
| source = Mystic Forge
| quantity = 0.31
| ingredient1 = 1 Obsidian Shard
| ingredient2 = 6 Philosopher's Stone
| base ingredients calculation = y
}}
</div>
"""

RESEARCH = """
== 10 Clover ==
{{SDRH | manual totals = no | title = 10 clover
  | Clover | s1 = clover | l1 = Mystic Clover
  | Ecto | s2 = ecto | l2 = Glob of Ectoplasm
  | Lodestone | s3 = lodestone | l3 = Lodestone
}}
{{SDRL | clover = 3 | ecto = 5 | --someone }}
{{SDRF}}
==1 Clover==
{{SDRH | manual totals = no | title = 1 Clover
  | Clover | s1 = clover | l1 = Mystic Clover
  | Ecto | s2 = ecto | l2 = Glob of Ectoplasm
  | Lodestone | s3 = lodestone | l3 = Lodestone
}}
{{SDRL | clover = 7 | ecto = 3 | lodestone = 2 | --someone }}
<!-- {{SDRL | clover = 99 | ecto = 99 | --example }} -->
{{SDRF}}
=== Possible drops ===
* 1 {{item icon|Mystic Clover}}
* 2 {{item icon|Glob of Ectoplasm}}
* 1 [[Lodestone]]
"""


def test_average_recipe_is_not_a_source_and_random_is_marked():
    rs = wp.recipes(CLOVER_PAGE)
    assert [r["kind"] for r in rs] == ["mystic_forge", "average"]
    assert rs[0]["_random"]["quantity"] == 1 and rs[1]["avg"] == 0.31


def test_upper_quantity_marks_random():
    r = wp.parse_recipe({"source": "Mystic Forge", "quantity": "1", "upper quantity": "5", "ingredient1": "1 Relic"})
    assert r["_random"] == {"quantity": 1, "upper": 5, "type": ""}
    assert "_random" not in wp.parse_recipe({"source": "Mystic Forge", "quantity": "1", "ingredient1": "1 Relic"})


def test_research_tables_pool_and_skip_comment_examples():
    t = fo.research_tables(RESEARCH)
    assert [x["section"] for x in t] == ["10 Clover", "1 Clover"]
    assert t[1]["rows"] == [{"clover": 7, "ecto": 3, "lodestone": 2}]
    out, warn = fo.outcomes("Mystic Clover", {"research": "x", "split": {"Lodestone": ["A Lodestone", "B Lodestone"]}}, RESEARCH, 0.31)
    assert not warn and out["attempts"] == 20 and out["p_unit"] == 0.31 and out["from"] == "wiki average recipe"
    assert abs(out["research_p"] - 0.5) < 1e-9
    # non-clover outcomes share 1 - 0.31: ecto 8 of 10, lodestone 2 of 10 (split over two)
    assert abs(out["items"]["Glob of Ectoplasm"] - 0.8 * 0.69 * 2) < 1e-9
    assert abs(out["items"]["A Lodestone"] - 0.2 * 0.69 / 2) < 1e-9


def test_self_columns_give_expected_output():
    page = "{{SDRH | s1 = one | s2 = five }}\n{{SDRL | Total = 10 | one = 9 | five = 1 }}"
    out, _ = fo.outcomes("Mystic Facet", {"research": "x", "self_columns": {"one": 1, "five": 5}}, page, None)
    assert out["from"] == "drop research" and abs(out["p_unit"] - 1.4) < 1e-9 and out["items"] == {}


def test_reward_track_table():
    page = """=== Reward tracks ===
{| {{STDT|item}}
! Reward track
|-
! colspan=5 | Group
|-
| [[Canthan Reward Track]]
| [[End of Dragons Reward Coffer]]
| style="text-align:center" | '''7''', 2
| style="text-align:center" | {{no}}, {{yes}}
| PvP, WvW
|-
| [[Ascalon Reward Track]] (†)
| [[Ascalon Weapon Skins Box]]
| style="text-align:center" | 2
| style="text-align:center" | {{no}}
| PvP
|}
"""
    rows = wp.reward_track_table(page)
    assert rows[0] == {"track": "Canthan Reward Track", "modes": ["pvp", "wvw"], "retiring": False,
                       "repeatable": True, "count": 2, "first_time_count": 7}
    assert rows[1]["retiring"] and not rows[1]["repeatable"] and rows[1]["modes"] == ["pvp"]


def test_vendor_own_and_shared_caps(monkeypatch):
    monkeypatch.setattr(ws, "VENDOR_CAPS", {"own_cap": {"Clover": ["Keeper"]}, "shared_cap": {"Clover": [["League"]]}})
    cost = [{"Has item currency": "Mystic Coin", "Has item value": "3"}]
    rows = [{"_subject": f"{v}#v", "Has vendor": [v], "Has item cost": cost, "Has weekly purchase cap": [10]}
            for v in ("Miyani", "Attendant", "Keeper")]
    rows.append({"_subject": "League#1", "Has vendor": ["League"], "Has item cost": [{"Has item currency": "Gold", "Has item value": "1"}],
                 "Has weekly purchase cap": [5]})
    rows.append({"_subject": "League#2", "Has vendor": ["League"], "Has item cost": [{"Has item currency": "Gold", "Has item value": "2"}],
                 "Has weekly purchase cap": [5]})
    srcs, _ = ws.vendor_sources("Clover", rows, {})
    npcs = sorted(tuple(v["npc"] for v in s["vendors"]) for s in srcs)
    assert npcs == [("Attendant", "Miyani"), ("Keeper",), ("League",), ("League",)]
    assert [s.get("cap_group") for s in srcs if s["vendors"][0]["npc"] == "League"] == ["Clover: League"] * 2
