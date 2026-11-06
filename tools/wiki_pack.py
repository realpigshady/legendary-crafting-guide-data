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
MAX_NODES = 20000          # guard against a runaway walk (a bad link loop)

# How the wiki writes the parts we read. Edit here if the wiki's templates change.
TEMPLATES = {
    "recipe": ["Recipe"],                          # {{Recipe | source = .. | ingredient1 = 250 Mystic Coin ..}}
    "infobox_suffix": "infobox",                   # {{Item infobox | id = 19976}}, {{Weapon infobox ..}}
    "id_param": "id",
}

# Sources the general rules don't reach for a few items (config/extra_sources.json):
# chosen boxes it comes in, the item page's reward-track table, repeatable achievements.
_CONFIG = Path(__file__).resolve().parent.parent / "config"
_EXTRA_CFG = json.loads((_CONFIG / "extra_sources.json").read_text(encoding="utf-8")) if (_CONFIG / "extra_sources.json").exists() else {}
EXTRA: dict[str, dict] = _EXTRA_CFG.get("items", {})
REPEATABLE_ACH = [re.compile(x) for x in _EXTRA_CFG.get("repeatable_achievements", [])]

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
    name = m.group(1).strip() if m else _LINK.sub(r"\1", raw).strip()
    name = name.split("#", 1)[0].strip()   # "Ardent Glorious Armguards#item1": the page's own (ascended) item
    return re.sub(r"^\d+\s*[x×]?\s+(?=[A-Za-z])", "", name)   # "1 Essence of ..." / "5x Mystic Coin": the count is a separate field


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


def item_type(wikitext: str) -> str:
    """The item infobox's ``type`` (lower case), '' if none."""
    m = re.search(r"\{\{\s*Item infobox(.*?)\n\}\}", wikitext, re.IGNORECASE | re.DOTALL)
    t = re.search(r"\|\s*type\s*=\s*([^\n|]*)", m.group(1)) if m else None
    return t.group(1).strip().lower() if t else ""


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
    # wiki spellings: "mystic forge" / "mystic"; "recipe sheet" / "sheet", "automatic", "discovery"
    kind = "mystic_forge" if source.startswith("mystic") else "craft" if source in ("automatic", "discovery", "recipe sheet", "sheet") else "other"
    raw_q = (params.get("quantity", "1") or "1").strip()
    if params.get("base ingredients calculation", "").strip().lower() == "y":
        # the wiki's hidden "average" recipe (Mystic Clover: quantity 0.31): not a recipe of its
        # own, the average output of the visible random recipe with these ingredients
        try:
            return {"kind": "average", "avg": float(raw_q), "inputs": inputs}
        except ValueError:
            return None
    try:
        out_count = max(1, int(raw_q))
    except ValueError:
        out_count = 1
    upper = params.get("upper quantity", "").strip()
    random_out = kind == "mystic_forge" and (upper.isdigit() or "(random)" in params.get("name", "").lower())
    extra = {"_random": {"quantity": out_count, "upper": int(upper) if upper.isdigit() else None,
                         "type": params.get("type", "").strip()}} if random_out else {}
    return {
        "kind": kind,
        "inputs": inputs,
        "output_count": out_count,
        "disciplines": [d.strip() for d in params.get("disciplines", "").split(",") if d.strip()],
        "recipe_id": params.get("id") or None,
        "_output_id": int(params["output item id"]) if (params.get("output item id") or "").strip().isdigit() else None,
        "_output_name": clean_name(params.get("name", "")) if params.get("name") else None,
        **extra,
    }


def reward_track_table(wikitext: str) -> list[dict]:
    """Rows of an item page's '=== Reward tracks ===' table (Mystic Clover): track, modes, the
    count per completion, repeatable, first-time count when the first completion gives more
    ("7, 2" / "no, yes"), retiring when the row is marked (†)."""
    i = wikitext.find("=== Reward tracks ===")
    if i < 0:
        return []
    j = wikitext.find("\n|}", i)
    out = []
    for chunk in wikitext[i:j if j > 0 else None].split("\n|-")[1:]:
        cells = []
        for line in chunk.strip().split("\n"):
            if not line.startswith("|") or line.startswith("|}"):
                continue
            c = line[1:].strip()
            if "style=" in c and "|" in c:
                c = c.rsplit("|", 1)[1].strip()
            cells.append(c)
        if len(cells) < 5:
            continue
        m = re.search(r"\[\[([^|\]]+)", cells[0])
        if not m:
            continue
        qty = [int(x) for x in re.findall(r"\d+", cells[2])]
        rep = [x == "yes" for x in re.findall(r"\{\{\s*(yes|no)\s*\}\}", cells[3], re.I)]
        if not qty:
            continue
        rep = (rep + [rep[-1] if rep else False] * len(qty))[:len(qty)]
        again = [q for q, r in zip(qty, rep) if r]
        once = [q for q, r in zip(qty, rep) if not r]
        modes = [x for x, k in (("pvp", "pvp"), ("wvw", "wvw")) if k in cells[4].lower()]
        row = {"track": m.group(1).strip(), "modes": modes or ["pvp", "wvw"], "retiring": "†" in cells[0],
               "repeatable": bool(again), "count": again[0] if again else once[0]}
        if again and once:
            row["first_time_count"] = once[0]
        out.append(row)
    return out


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
        # redirects are kept too: offline, a title that was a redirect must still find its page
        self._redirects_path = self.dir / "_redirects.json"
        self.redirects: dict[str, str] = (
            json.loads(self._redirects_path.read_text(encoding="utf-8")) if self._redirects_path.exists() else {})
        self._redirects_saved = dict(self.redirects)
        # titles the wiki has no page for: offline they stay missing instead of aborting
        self._missing_path = self.dir / "_missing.json"
        self.missing: set[str] = set(json.loads(self._missing_path.read_text(encoding="utf-8"))) if self._missing_path.exists() else set()

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
            if page is None and target in self.missing:
                continue
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
        if self.redirects != self._redirects_saved:
            self._redirects_path.write_text(json.dumps(self.redirects, ensure_ascii=False, sort_keys=True, indent=0), encoding="utf-8")
            self._redirects_saved = dict(self.redirects)
        revs = {p["title"]: p.get("lastrevid", 0) for p in info.get("pages", []) if not p.get("missing") and not p.get("invalid")}
        gone = {p["title"] for p in info.get("pages", []) if p.get("missing") or p.get("invalid")} | \
               {final[t] for t in titles if final[t] not in revs and not any(p.get("title") == final[t] for p in info.get("pages", []))}
        if gone - self.missing or self.missing & set(revs):
            self.missing = (self.missing | gone) - set(revs)
            self._missing_path.write_text(json.dumps(sorted(self.missing), ensure_ascii=False, indent=0), encoding="utf-8")
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

def _add_sources(w: "Walk", store, level_nodes, currencies: dict[str, int], seen: set[str]) -> list[str]:
    """Vendor / achievement sources for every node of the level; reward tracks, rewarding
    places and salvage for bound items nothing crafts. Returns cost items to walk next."""
    import wiki_sources as ws
    names = [n["name"] for n, _, _ in level_nodes]
    bound = [n["name"] for n, locked, crafted in level_nodes if locked and not crafted]
    got = {k: store.get(k, names) for k in ("vendor", "achievement")}
    for k in ("rewarded", "track", "salvage", "contained"):
        got[k] = store.get(k, bound) if bound else {}
    extra = [n for n in names if n in EXTRA and EXTRA[n].get("containers") and n not in got["contained"]]
    if extra:
        got["contained"] = {**got["contained"], **store.get("contained", extra)}
    nxt: list[str] = []
    for node, locked, crafted in level_nodes:
        nm = node["name"]
        new = []
        vend, cost_items = ws.vendor_sources(nm, got["vendor"].get(nm, []), currencies)
        new += vend
        cur_name = {v: k for k, v in currencies.items()}
        for src in vend:
            for i in src["inputs"]:
                if i["node_id"].startswith("currency:"):
                    cid = int(i["node_id"].split(":")[1])
                    w.nodes.setdefault(i["node_id"], {"id": i["node_id"], "type": "currency",
                                                      "name": cur_name.get(cid, "Coin" if cid == 1 else i["node_id"]),
                                                      "currency_id": cid, "sources": []})
        for c in cost_items:
            if c not in seen:
                nxt.append(c)
        for a in ws.achievement_sources(nm, got["achievement"].get(nm, [])):
            if any(rx.search(a["achievement_name"]) for rx in REPEATABLE_ACH):
                a["repeatable"] = True   # e.g. Glory to the Legions: completes again like a reward track
            new.append(a)
        cfg = EXTRA.get(nm)
        page_text = node.pop("_page_text", "")
        if cfg:
            allow = set(cfg.get("containers", []))
            picked = [r for r in got["contained"].get(nm, []) if (r.get("Contained in") or [""])[0] in allow]
            boxes, cn = ws.container_sources(nm, picked)
            new += boxes
            for b in cn:
                w.opened.add(b)
                if b not in seen:
                    nxt.append(b)
            if cfg.get("reward_track_table"):
                for t in reward_track_table(page_text):
                    for m in t["modes"]:
                        aid = f"rt:{ws.slug(t['track'])}:{m}"
                        act = w.activities.setdefault(aid, {"id": aid, "name": f"{t['track']} ({'WvW' if m == 'wvw' else 'PvP'})",
                                                            "mode": m, "expansion": "core", "yields": [],
                                                            "guidance": {"text": "", "wiki_url": WIKI + urllib.parse.quote(t["track"].replace(" ", "_"))}})
                        act["reward_track"] = True
                        act["repeatable"] = t["repeatable"]
                        if t["retiring"]:
                            act["retiring"] = True
                        y = next((y for y in act["yields"] if y["node_id"] == node["id"]), None)
                        if y is None:
                            y = {"node_id": node["id"], "count": t["count"]}
                            act["yields"].append(y)
                        else:
                            y["count"] = t["count"]   # the item page's table beats a per-reward SMW record
                        if t.get("first_time_count"):
                            y["first_time_count"] = t["first_time_count"]
                        if not any(s.get("activity_id") == aid for s in new):
                            new.append({"kind": "activity", "inputs": [], "activity_id": aid})
        notes = []
        for a in ws.activity_rows(nm, got["rewarded"].get(nm, []), got["track"].get(nm, [])):
            notes.append(a["note"])
            if a["chance"] != "Guaranteed":
                continue   # a chance with no rate: guidance only
            act = w.activities.setdefault(a["id"], {"id": a["id"], "name": a["name"], "mode": a["mode"],
                                                    "expansion": "core", "yields": [],
                                                    "guidance": {"text": "", "wiki_url": WIKI + urllib.parse.quote(a["name"].split(" (")[0].replace(" ", "_"))}})
            if not any(y["node_id"] == node["id"] for y in act["yields"]):
                act["yields"].append({"node_id": node["id"], "count": a["count"]})
            if not any(s.get("activity_id") == a["id"] for s in new):
                new.append({"kind": "activity", "inputs": [], "activity_id": a["id"]})
        if not new and locked and not crafted:   # still nothing: the box it comes in (opened = one of it)
            boxes, cn = ws.container_sources(nm, got["contained"].get(nm, []))
            new += boxes
            for b in cn:
                w.opened.add(b)
                if b not in seen:
                    nxt.append(b)
        for r in got["salvage"].get(nm, []):
            notes.append("salvaged from " + (r.get("Has canonical name") or [r["_subject"]])[0])
        if notes:
            node.setdefault("guidance", {})["text"] = "; ".join(dict.fromkeys(notes))[:300]
        if new:
            node["sources"] = [s for s in node["sources"] if s["kind"] != "other"] + new
            w.warnings[:] = [x for x in w.warnings if x != f"no source: {nm}"]
    return nxt


WIKI = "https://wiki.guildwars2.com/wiki/"


@dataclass
class Walk:
    nodes: dict[str, dict] = field(default_factory=dict)
    activities: dict[str, dict] = field(default_factory=dict)
    opened: set = field(default_factory=set)   # boxes reached as "contained in" sources: walked, not stopped
    warnings: list[str] = field(default_factory=list)
    pages_by_depth: dict[int, int] = field(default_factory=dict)
    random_seen: dict[str, list] = field(default_factory=dict)   # random-output forge recipes met on the walk


def node_id_for(name: str, item_id: int | None) -> str:
    if item_id:
        return f"item:{item_id}"
    return "name:" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def walk(store: PageStore, roots: list[str], currencies: dict[str, int],
         untradeable: Callable[[list[int]], set[int]], always_expand: set[str] = frozenset(),
         max_depth: int = 12, sources=None, root_ids: list[int] | None = None) -> Walk:
    """Recipe tree from root pages down.

    A page's {{Recipe}}s become its sources and their ingredients its children. An
    ingredient whose name is a wallet currency becomes a currency node. Every item can
    be bought (the addon decides by live price), so its node also gets a trading_post
    source. Every recipe is followed, refinements too (log -> plank), so the planner can
    compare crafting with buying at every level.
    ``sources`` (a wiki_sources.SourceStore) adds vendors, achievements and, for bound items,
    reward tracks / rewarding places; vendor cost items join the walk like ingredients.
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
        level_nodes: list[tuple[dict, bool, bool]] = []
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
            container = item_type(page.text) in ("container", "gizmo") and name not in w.opened and page.title not in w.opened   # chests as prices: stop
            srcs = [] if container else recipes(page.text)   # a container (chest) only appears as a price: stop there
            avgs = [r for r in srcs if r["kind"] == "average"]
            srcs = [r for r in srcs if r["kind"] != "average"]
            for r in srcs:
                if r.get("_random"):
                    w.random_seen.setdefault(page.title, []).append(dict(r["_random"]))
            node = {"id": nid, "type": "drop", "name": page.title, "item_id": iid, "sources": [],
                    "guidance": {"wiki_url": "https://wiki.guildwars2.com/wiki/" + urllib.parse.quote(page.title.replace(" ", "_")),
                                 "text": "", "waypoint_chat_code": None}}
            if avgs:
                node["_avg_output"] = avgs[0]["avg"]   # per unit of the random recipe (forge_outcomes.apply)
            if page.title in EXTRA:
                node["_page_text"] = page.text   # reward-track table (extra sources)
            for s in srcs:
                for inp in s["inputs"]:
                    cid = cur_by_name.get(inp["name"].lower())
                    if inp["name"].lower() in ("gold", "coin", "copper"):   # recipe cost in coin (the API's currency 1)
                        cid = 1
                        inp["count"] *= {"gold": 10000, "coin": 1, "copper": 1}[inp["name"].lower()]
                        inp["name"] = "Coin"
                    if cid:
                        inp["node_id"] = f"currency:{cid}"
                        w.nodes.setdefault(inp["node_id"], {"id": inp["node_id"], "type": "currency", "name": inp["name"],
                                                            "currency_id": cid, "sources": []})
                    else:
                        inp["node_id"] = "@" + inp["name"]   # resolved to the child's id after the walk
                        if inp["name"] not in seen:
                            nxt.append(inp["name"])
                node["sources"].append(s)
            # A recipe whose output is another item (the legendary version of an ascended armor
            # piece, on the same page): that item's source, with this page's item as an input.
            for s in list(node["sources"]):
                oid, oname = s.pop("_output_id", None), s.pop("_output_name", None)
                if oid and oid != iid:
                    node["sources"].remove(s)
                    vid = f"item:{oid}"
                    v = w.nodes.setdefault(vid, {"id": vid, "type": "forge" if s["kind"] == "mystic_forge" else "craft",
                                                 "name": oname or f"{page.title} ({oid})", "item_id": oid, "sources": [],
                                                 "guidance": dict(node["guidance"])})
                    if s not in v["sources"]:
                        v["sources"].append(s)
            srcs = node["sources"][:]
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
            if container:
                node["container"] = True
            else:
                level_nodes.append((node, iid is not None and iid in locked, bool(srcs)))
            if len(w.nodes) > MAX_NODES:
                raise SystemExit(f"walk passed {MAX_NODES} nodes: link loop or missing stop rule")
        if sources is not None and level_nodes:
            nxt += _add_sources(w, sources, level_nodes, currencies, seen)
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
                    inp.pop("name", None) if inp["node_id"] in w.nodes else None
    # vendors that take containers (chest exchanges) aren't shops for this; then keep only
    # what the legendaries actually reach
    # Recipe cycles (A from B, B from A: material promotions, refinements both ways) make
    # the pack unplannable. Walking down from the legendaries, a source whose input leads
    # back to a node on the current path is dropped; buying and other sources stay.
    import sys as _sys
    _sys.setrecursionlimit(max(10000, _sys.getrecursionlimit()))
    state: dict[str, int] = {}   # 1 = on the path, 2 = done
    cut = 0

    def visit(nid: str) -> None:
        nonlocal cut
        state[nid] = 1
        node = w.nodes.get(nid)
        if node:
            keep = []
            for src in node["sources"]:
                ins = [i.get("node_id") for i in src["inputs"] if i.get("node_id") in w.nodes]
                if any(state.get(i) == 1 for i in ins):
                    cut += 1
                    continue
                for i in ins:
                    if state.get(i) is None:
                        visit(i)
                if any(state.get(i) == 1 for i in ins):   # a deeper visit closed a loop back here
                    cut += 1
                    continue
                keep.append(src)
            node["sources"] = keep or [{"kind": "other", "inputs": []}]
        state[nid] = 2

    root_nodes = [by_name.get(r, node_id_for(r, None)) for r in roots] + [f"item:{i}" for i in (root_ids or [])]
    for rid in root_nodes:
        if rid in w.nodes and state.get(rid) is None:
            visit(rid)
    if cut:
        w.warnings.append(f"recipe cycles: {cut} sources dropped")

    # A vendor price that includes something with no source (a chest, a mini, an old token)
    # isn't a way to get the item here: drop it, repeat until nothing changes.
    boxes = {nid for nid, n in w.nodes.items() if n.pop("container", False)}
    for n in w.nodes.values():
        if n["id"] in boxes:
            n["sources"] = [s for s in n["sources"] if s["kind"] == "trading_post"] or [{"kind": "other", "inputs": []}]

    def dead(nid: str) -> bool:
        x = w.nodes.get(nid)
        return x is None or (x.get("type") != "currency" and all(s["kind"] == "other" for s in x["sources"]))
    changed = True
    while changed:
        changed = False
        for n in w.nodes.values():
            keep = [s for s in n["sources"] if not (s["kind"] == "vendor" and any(dead(i.get("node_id", "")) for i in s["inputs"]))]
            if len(keep) != len(n["sources"]):
                n["sources"] = keep or [{"kind": "other", "inputs": []}]
                changed = True
    reach, stack = set(), list(root_nodes)
    while stack:
        x = stack.pop()
        if x in reach or x not in w.nodes:
            continue
        reach.add(x)
        stack += [i["node_id"] for s in w.nodes[x]["sources"] for i in s["inputs"] if i.get("node_id")]
    w.nodes = {k: v for k, v in w.nodes.items() if k in reach}
    for a in list(w.activities.values()):
        a["yields"] = [y for y in a["yields"] if y["node_id"] in reach]
        if not a["yields"]:
            del w.activities[a["id"]]
    used = {s.get("activity_id") for n in w.nodes.values() for s in n["sources"]}
    w.activities = {k: v for k, v in w.activities.items() if k in used}
    w.warnings[:] = [x for x in w.warnings if not x.startswith("no source: ")]
    w.warnings += [f"no source: {n['name']}" for n in w.nodes.values()
                   if n.get("type") != "currency" and all(s["kind"] == "other" for s in n["sources"])]
    return w
