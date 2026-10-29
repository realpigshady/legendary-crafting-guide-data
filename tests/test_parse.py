import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import build_pack as bp  # noqa: E402

SAMPLE = """
Intro text {{Item infobox | id = 30689 }}
== Acquisition ==
{{Recipe
| source = mystic forge
| ingredient1 = 1 Gift of Nevermore
| ingredient2 = 1 Gift of Mastery
| ingredient3 = 1 Gift of Craftsmanship
| ingredient4 = 1 [[Raven Staff]]
}}
{{Recipe | source = automatic | disciplines = Huntsman, Artificer | rating = 500
| ingredient1 = 250 Mystic Curio | quantity = 1 | id = 12345 }}
"""


def test_find_templates_handles_nesting():
    tpls = bp.find_templates(SAMPLE, "Recipe")
    assert len(tpls) == 2
    assert tpls[0]["source"] == "mystic forge"
    assert tpls[0]["ingredient4"] == "1 [[Raven Staff]]"


def test_parse_recipe_forge():
    src = bp.parse_recipe(bp.find_templates(SAMPLE, "Recipe")[0])
    assert src["kind"] == "mystic_forge"
    assert [i["count"] for i in src["inputs"]] == [1, 1, 1, 1]
    assert src["inputs"][0]["name"] == "Gift of Nevermore"


def test_parse_recipe_craft():
    src = bp.parse_recipe(bp.find_templates(SAMPLE, "Recipe")[1])
    assert src["kind"] == "craft"
    assert src["inputs"] == [{"name": "Mystic Curio", "count": 250}]
    assert src["disciplines"] == ["Huntsman", "Artificer"]
    assert src["recipe_id"] == "12345"
