"""Wiki side of the pack generator: revision-aware page store and recipe-tree walk.

Built while the GW2 wiki is offline, against the MediaWiki API contract (``api.php``,
``formatversion=2``), not against any page layout. Everything that depends on how the
wiki writes its pages (template names, parameter names) lives in ``TEMPLATES`` so a
restructured wiki means editing one table, not the walk.

Manners: titles go 50 to a request; a cheap ``prop=info`` call finds which pages changed
since the last run and only those are downloaded again; ``maxlag=5`` lets the wiki shed
us when busy; the Http class spaces requests by one second.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

BATCH = 50                 # MediaWiki: titles per query for normal clients
MAX_NODES = 4000           # guard against a runaway walk (a bad link loop)

# How the wiki writes the parts we read. Edit here if the wiki's templates change.
TEMPLATES = {
    "recipe": ["Recipe"],                          # {{Recipe | source = .. | ingredient1 = 250 Mystic Coin ..}}
    "infobox_suffix": "infobox",                   # {{Item infobox | id = 19976}}, {{Weapon infobox ..}}
    "id_param": "id",
}

_LINK = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]")
_ING = re.compile(r"^\s*(\d[\d,]*)\s+(.+?)\s*$")


class WikiDown(Exception):
    """The wiki can't be reached and a page we need was never stored."""


# --------------------------------------------------------------------------- wikitext

def find_templates(wikitext: str, name: str) -> list[dict[str, str]]:
    """Params of every {{name ...}} template (nested braces and links handled)."""
    out = []
    pat = re.compile(r"\{\{\s*" + re.escape(name) + r"\s*[|}]", re.IGNORECASE)
    for m in pat.finditer(wikitext):
        depth, i = 0, m.start()
        while i < len(wikitext) - 1:
            two = wikitext[i:i + 2]
            if two == "{{":
                depth += 1
                i += 2
                continue
            if two == "}}":
                depth -= 1
                i += 2
                if depth == 0:
                    break
                continue
            i += 1
        body = wikitext[m.end():i - 2] if wikitext[m.end() - 1] == "|" else ""
        out.append(split_params(body))
    return out


def split_params(body: str) -> dict[str, str]:
    parts, depth, cur, i = [], 0, [], 0
    while i < len(body):
        two = body[i:i + 2]
        if two in ("{{", "[["):
            depth += 1
            cur.append(two)
            i += 2
            continue
        if two in ("}}", "]]"):
            depth -= 1
            cur.append(two)
            i += 2
            continue
        if body[i] == "|" and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(body[i])
        i += 1
    parts.append("".join(cur))
    params: dict[str, str] = {}
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.strip().lower()] = v.strip()
    return params


def clean_name(raw: str) -> str:
    """'[[Gift of Mastery|gifts]]' -> 'Gift of Mastery'; '[[Raven Staff]]' -> 'Raven Staff'."""
    m = re.search(r"\[\[([^|\]]+)", raw)
    if m:
        return m.group(1).strip()
    return _LINK.sub(r"\1", raw).strip()


def infobox_id(wikitext: str) -> int | None:
    """Game id from the page's first ...infobox template (first of a comma list)."""
    suffix = TEMPLATES["infobox_suffix"]
    for m in re.finditer(r"\{\{\s*([A-Za-z ]*" + re.escape(suffix) + r")\s*\|", wikitext, re.IGNORECASE):
        for params in find_templates(wikitext[m.start():], m.group(1).strip()):
            raw = params.get(TEMPLATES["id_param"], "")
            first = raw.split(",")[0].strip()
            if first.isdigit():
                return int(first)
            break
    return None


def recipes(wikitext: str) -> list[dict]:
    out = []
    for name in TEMPLATES["recipe"]:
        for params in find_templates(wikitext, name):
            r = parse_recipe(params)
            if r:
                out.append(r)
    return out


def parse_recipe(params: dict[str, str]) -> dict | None:
    source = params.get("source", "").lower()
    inputs = []
    for n in range(1, 9):
        raw = params.get(f"ingredient{n}")
        if not raw:
            continue
        m = _ING.match(raw)
        count, name = (int(m.group(1).replace(",", "")), m.group(2)) if m else (1, raw)
        inputs.append({"name": clean_name(name), "count": count})
    if not inputs:
        return None
    kind = "mystic_forge" if "mystic forge" in source else "craft" if source in ("automatic", "discovery", "recipe sheet") else "other"
    try:
        out_count = max(1, int(params.get("quantity", "1") or 1))
    except ValueError:
        out_count = 1
    return {
        "kind": kind,
        "inputs": inputs,
        "output_count": out_count,
        "disciplines": [d.strip() for d in params.get("disciplines", "").split(",") if d.strip()],
        "recipe_id": params.get("id") or None,
    }


# --------------------------------------------------------------------------- page store

@dataclass
class Page:
    title: str
    revid: int
    text: str


class PageStore:
    """Wiki pages by title, kept under snapshot/wiki-pages/ between runs.

    ``get_live(url) -> dict`` does one api.php request (raises when the wiki is down).
    A page is downloaded again only when its revision changed; while the wiki is down
    stored pages are used (any age) and ``stale`` counts them.
    """

    def __init__(self, root: Path, get_live: Callable[[str], dict], api: str = "https://wiki.guildwars2.com/api.php"):
        self.dir = root / "wiki-pages"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.get_live = get_live
        self.api = api
        self.down = False
        self.stale = 0
        self.fresh = 0
        self.requests = 0
        self.bytes = 0
        self.redirects: dict[str, str] = {}

    def _path(self, title: str) -> Path:
        return self.dir / (hashlib.sha256(title.encode()).hexdigest()[:32] + ".json")

    def _load(self, title: str) -> Page | None:
        p = self._path(title)
        if not p.exists():
            return None
        d = json.loads(p.read_text(encoding="utf-8"))
        return Page(d["title"], d["revid"], d["text"])

    def _save(self, page: Page) -> None:
        text = json.dumps({"title": page.title, "revid": page.revid, "text": page.text}, ensure_ascii=False, sort_keys=True, indent=0)
        p = self._path(page.title)
        if not p.exists() or p.read_text(encoding="utf-8") != text:
            p.write_text(text, encoding="utf-8")

    def _query(self, **params) -> dict:
        params = {"action": "query", "format": "json", "formatversion": "2", "maxlag": "5", **params}
        self.requests += 1
        return self.get_live(f"{self.api}?{urllib.parse.urlencode(params)}")

    def pages(self, titles: list[str]) -> dict[str, Page]:
        """Requested title -> page (redirects followed). Missing pages are left out."""
        want = list(dict.fromkeys(t for t in titles if t))
        out: dict[str, Page] = {}
        for i in range(0, len(want), BATCH):
            out.update(self._batch(want[i:i + BATCH]))
        return out

    def _batch(self, titles: list[str]) -> dict[str, Page]:
        if not self.down:
            try:
                return self._batch_live(titles)
            except WikiDown:
                raise
            except Exception:  # noqa: BLE001 - one failure = down for this run
                self.down = True
        out = {}
        for t in titles:
            target = self.redirects.get(t, t)
            page = self._load(target) or self._load(t)
            if page is None:
                raise WikiDown(f"wiki down and '{t}' was never stored")
            self.stale += 1
            out[t] = page
        return out

    def _batch_live(self, titles: list[str]) -> dict[str, Page]:
        info = self._query(prop="info", titles="|".join(titles), redirects="1").get("query", {})
        rename = {n["from"]: n["to"] for n in info.get("normalized", [])}
        redirect = {r["from"]: r["to"] for r in info.get("redirects", [])}
        final = {}
        for t in titles:
            f = rename.get(t, t)
            f = redirect.get(f, f)
            final[t] = f
            if f != t:
                self.redirects[t] = f
        revs = {p["title"]: p.get("lastrevid", 0) for p in info.get("pages", []) if not p.get("missing") and not p.get("invalid")}
        have = {f: self._load(f) for f in set(final.values()) if f in revs}
        changed = [f for f in revs if not have.get(f) or have[f].revid != revs[f]]
        if changed:
            got = self._query(prop="revisions", rvprop="ids|content", rvslots="main", titles="|".join(changed)).get("query", {})
            for p in got.get("pages", []):
                rv = (p.get("revisions") or [{}])[0]
                text = rv.get("slots", {}).get("main", {}).get("content", rv.get("content", ""))
                page = Page(p["title"], rv.get("revid", revs.get(p["title"], 0)), text)
                self.bytes += len(text.encode())
                self.fresh += 1
                self._save(page)
                have[page.title] = page
        return {t: have[f] for t, f in final.items() if have.get(f)}


# --------------------------------------------------------------------------- recipe tree

@dataclass
class Walk:
    nodes: dict[str, dict] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    pages_by_depth: dict[int, int] = field(default_factory=dict)


def node_id_for(name: str, item_id: int | None) -> str:
    if item_id:
        return f"item:{item_id}"
    return "name:" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def walk(store: PageStore, roots: list[str], currencies: dict[str, int],
         untradeable: Callable[[list[int]], set[int]], always_expand: set[str] = frozenset(),
         max_depth: int = 12) -> Walk:
    """Recipe tree from root pages down.

    A page's {{Recipe}}s become its sources and their ingredients its children. An
    ingredient whose name is a wallet currency becomes a currency node. Every item can
    be bought (the addon decides by live price), so its node also gets a trading_post
    source; recipes of tradeable items are followed only for roots and ``always_expand``
    (else the walk would descend into every basic material).
    ``untradeable(ids)`` says which item ids can't be traded (API flags).
    """
    w = Walk()
    cur_by_name = {k.lower(): v for k, v in currencies.items()}
    level = list(dict.fromkeys(roots))
    seen: set[str] = set()
    depth = 0
    while level and depth <= max_depth:
        pages = store.pages(level)
        w.pages_by_depth[depth] = len(pages)
        ids = [i for i in (infobox_id(p.text) for p in pages.values()) if i]
        locked = untradeable(ids) if ids else set()
        nxt: list[str] = []
        for name in level:
            seen.add(name)
            page = pages.get(name)
            if page is None:
                w.warnings.append(f"no wiki page: {name}")
                nid = node_id_for(name, None)
                w.nodes.setdefault(nid, {"id": nid, "type": "drop", "name": name, "item_id": None,
                                         "sources": [{"kind": "other", "inputs": []}]})
                continue
            iid = infobox_id(page.text)
            nid = node_id_for(page.title, iid)
            if nid in w.nodes:
                continue
            expand = depth == 0 or name in always_expand or page.title in always_expand or (iid is not None and iid in locked)
            srcs = recipes(page.text) if expand else []
            node = {"id": nid, "type": "drop", "name": page.title, "item_id": iid, "sources": [],
                    "guidance": {"wiki_url": "https://wiki.guildwars2.com/wiki/" + urllib.parse.quote(page.title.replace(" ", "_")),
                                 "text": "", "waypoint_chat_code": None}}
            for s in srcs:
                for inp in s["inputs"]:
                    cid = cur_by_name.get(inp["name"].lower())
                    if cid:
                        inp["node_id"] = f"currency:{cid}"
                        w.nodes.setdefault(inp["node_id"], {"id": inp["node_id"], "type": "currency", "name": inp["name"],
                                                            "currency_id": cid, "sources": []})
                    else:
                        inp["node_id"] = "@" + inp["name"]   # resolved to the child's id after the walk
                        if inp["name"] not in seen:
                            nxt.append(inp["name"])
                node["sources"].append(s)
            if any(s["kind"] == "mystic_forge" for s in srcs):
                node["type"] = "forge"
            elif srcs:
                node["type"] = "craft"
            if iid and iid not in locked:   # bound items never reach the Trading Post
                node["sources"].append({"kind": "trading_post", "inputs": []})
            if not node["sources"]:
                node["sources"].append({"kind": "other", "inputs": []})
                w.warnings.append(f"no source: {page.title}")
            w.nodes[nid] = node
            if len(w.nodes) > MAX_NODES:
                raise SystemExit(f"walk passed {MAX_NODES} nodes: link loop or missing stop rule")
        level = [n for n in dict.fromkeys(nxt) if n not in seen]
        depth += 1
    if level:
        w.warnings.append(f"stopped at depth {max_depth}: {len(level)} pages not walked")
    # "@name" placeholders -> node ids
    by_name: dict[str, str] = {}
    for n in w.nodes.values():
        by_name.setdefault(n["name"], n["id"])
    for t, f in store.redirects.items():
        if f in by_name:
            by_name.setdefault(t, by_name[f])
    for n in w.nodes.values():
        for s in n["sources"]:
            for inp in s["inputs"]:
                if inp.get("node_id", "").startswith("@"):
                    inp["node_id"] = by_name.get(inp["node_id"][1:], node_id_for(inp["node_id"][1:], None))
    return w
