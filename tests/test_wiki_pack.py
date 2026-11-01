"""Wiki page store + recipe walk against a fake api.php (MediaWiki formatversion=2)."""
import sys
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import wiki_pack as wp  # noqa: E402

PAGES = {
    "Nevermore": (11, """{{Weapon infobox | id = 30689, 30690 | type = staff}}
{{Recipe | source = mystic forge
| ingredient1 = 1 [[Gift of Nevermore]] | ingredient2 = 1 Gift of Mastery
| ingredient3 = 1 [[Gift of Craftsmanship|craft gift]] | ingredient4 = 1 The Raven Staff }}"""),
    "Gift of Nevermore": (21, """{{Item infobox | id = 79740 }}
{{Recipe | source = mystic forge | ingredient1 = 250 Mystic Coin | ingredient2 = 1,000 Spirit Shard
| ingredient3 = 100 Amalgamated Gemstone }}"""),
    "Gift of Mastery": (22, "{{Item infobox | id = 19674 }}\n{{Recipe | source = mystic forge | ingredient1 = 1 Bloodstone Shard | ingredient2 = 250 Obsidian Shard }}"),
    "Gift of Craftsmanship": (23, "{{Item infobox | id = 79600 }}"),        # no recipe on the page
    "The Raven Staff": (24, "{{Weapon infobox | id = 30700 }}\n{{Recipe | source = automatic | ingredient1 = 5 Mystic Coin }}"),
    "Mystic Coin": (31, "{{Item infobox | id = 19976 }}\n{{Recipe | source = mystic forge | ingredient1 = 1 Gold }}"),
    "Amalgamated Gemstone": (32, "{{Item infobox | id = 68063 }}"),
    "Bloodstone Shard": (33, "{{Item infobox | id = 20797 }}"),
    "Obsidian Shard": (34, "{{Item infobox | id = 19925 }}"),
}
REDIRECTS = {"Raven Staff": "The Raven Staff"}
CURRENCIES = {"Spirit Shard": 23}
LOCKED = {30689, 79740, 19674, 79600, 20797}   # account bound / soulbound


class FakeWiki:
    def __init__(self):
        self.calls: list[dict] = []
        self.down = False
        self.revs = {t: r for t, (r, _) in PAGES.items()}

    def __call__(self, url: str) -> dict:
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        self.calls.append(q)
        if self.down:
            raise OSError("503 login wall")
        titles = q["titles"].split("|")
        assert len(titles) <= wp.BATCH
        assert q["maxlag"] == "5" and q["formatversion"] == "2"
        if q["prop"] == "info":
            redirects = [{"from": t, "to": REDIRECTS[t]} for t in titles if t in REDIRECTS]
            final = [REDIRECTS.get(t, t) for t in titles]
            pages = [{"title": t, "lastrevid": self.revs[t]} if t in self.revs else {"title": t, "missing": True} for t in final]
            return {"query": {"redirects": redirects, "pages": pages}}
        assert q["prop"] == "revisions" and "content" in q["rvprop"]
        return {"query": {"pages": [{"title": t, "revisions": [{"revid": self.revs[t], "slots": {"main": {"content": PAGES[t][1]}}}]}
                                    for t in titles]}}


def untradeable(ids):
    return {i for i in ids if i in LOCKED}


def test_wikitext_parsing():
    text = PAGES["Nevermore"][1]
    assert wp.infobox_id(text) == 30689
    r = wp.recipes(text)
    assert len(r) == 1 and r[0]["kind"] == "mystic_forge"
    assert [i["name"] for i in r[0]["inputs"]] == ["Gift of Nevermore", "Gift of Mastery", "Gift of Craftsmanship", "The Raven Staff"]
    assert wp.parse_recipe(wp.find_templates(PAGES["Gift of Nevermore"][1], "Recipe")[0])["inputs"][1] == {"name": "Spirit Shard", "count": 1000}


def test_walk_builds_tree(tmp_path):
    fake = FakeWiki()
    store = wp.PageStore(tmp_path, fake)
    w = wp.walk(store, ["Nevermore"], CURRENCIES, untradeable)
    n = w.nodes
    root = n["item:30689"]
    assert root["type"] == "forge" and root["guidance"]["wiki_url"].endswith("/Nevermore")
    assert [i["node_id"] for i in root["sources"][0]["inputs"]] == ["item:79740", "item:19674", "item:79600", "item:30700"]
    assert not any(s["kind"] == "trading_post" for s in root["sources"])            # bound with a recipe: crafted only
    gift = n["item:79740"]
    assert [i["node_id"] for i in gift["sources"][0]["inputs"]] == ["item:19976", "currency:23", "item:68063"]
    assert n["currency:23"]["type"] == "currency"
    # tradeable items: bought (price decides), their recipes not followed
    coin = n["item:19976"]
    assert coin["sources"] == [{"kind": "trading_post", "inputs": []}]
    assert "name:gold" not in n
    # bound item with no recipe: still a node, flagged
    assert n["item:79600"]["sources"] == [{"kind": "other", "inputs": []}] and "no source: Gift of Craftsmanship" in w.warnings
    # batching: one info + one content call per depth level
    assert all(len(c["titles"].split("|")) <= wp.BATCH for c in fake.calls)
    assert sum(1 for c in fake.calls if c["prop"] == "revisions") == len(w.pages_by_depth)


def test_unchanged_pages_not_downloaded_again(tmp_path):
    fake = FakeWiki()
    wp.walk(wp.PageStore(tmp_path, fake), ["Nevermore"], CURRENCIES, untradeable)
    first = len(fake.calls)
    fake.calls.clear()
    fake.revs["Gift of Mastery"] += 1     # one page edited since
    store = wp.PageStore(tmp_path, fake)
    wp.walk(store, ["Nevermore"], CURRENCIES, untradeable)
    content = [c for c in fake.calls if c["prop"] == "revisions"]
    assert len(content) == 1 and content[0]["titles"] == "Gift of Mastery"
    assert store.fresh == 1 and first > len(content)


def test_wiki_down_uses_stored_pages(tmp_path):
    fake = FakeWiki()
    wp.walk(wp.PageStore(tmp_path, fake), ["Nevermore"], CURRENCIES, untradeable)
    fake.down = True
    fake.calls.clear()
    store = wp.PageStore(tmp_path, fake)
    w = wp.walk(store, ["Nevermore"], CURRENCIES, untradeable)
    assert "item:79740" in w.nodes and store.down and store.stale > 0
    assert len(fake.calls) == 1      # one failure marks the wiki down for the run


def test_wiki_down_without_copy_aborts(tmp_path):
    fake = FakeWiki()
    fake.down = True
    with pytest.raises(wp.WikiDown):
        wp.walk(wp.PageStore(tmp_path, fake), ["Nevermore"], CURRENCIES, untradeable)


def test_batches_of_fifty(tmp_path):
    fake = FakeWiki()
    for i in range(120):
        PAGES[f"P{i}"] = (1000 + i, "{{Item infobox | id = %d }}" % (500000 + i))
        fake.revs[f"P{i}"] = 1000 + i
    try:
        got = wp.PageStore(tmp_path, fake).pages([f"P{i}" for i in range(120)])
        assert len(got) == 120
        info = [c for c in fake.calls if c["prop"] == "info"]
        assert [len(c["titles"].split("|")) for c in info] == [50, 50, 20]
    finally:
        for i in range(120):
            PAGES.pop(f"P{i}")


def test_redirected_root(tmp_path):
    w = wp.walk(wp.PageStore(tmp_path, FakeWiki()), ["Raven Staff"], CURRENCIES, untradeable)
    assert w.nodes["item:30700"]["name"] == "The Raven Staff" and w.nodes["item:30700"]["type"] == "craft"


def test_missing_page_is_flagged(tmp_path):
    fake = FakeWiki()
    w = wp.walk(wp.PageStore(tmp_path, fake), ["Nevermore", "No Such Page"], CURRENCIES, untradeable)
    assert "name:no_such_page" in w.nodes and any("No Such Page" in x for x in w.warnings)
