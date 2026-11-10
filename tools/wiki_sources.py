"""Non-recipe sources from the wiki's Semantic MediaWiki data (`action=ask`).

Item pages don't hold their vendors, collections or rewards as text: templates like
{{Sold by}} and {{Rewarded by}} query SMW properties stored on vendor, achievement and
location pages. This asks for the same properties in bulk (several items per query,
OR'ed with ||) and turns the rows into pack sources:

  vendor       [[Sells item::X]] (current vendors) -> kind vendor, costs as inputs, caps
  achievement  [[Gives item::X]]                   -> kind reward + achievement_id + page
  reward track [[Contains item in reward track::X;?;?;?]] -> activity (wvw / pvp), yields
  rewarded by  [[Rewards item::X]] (zones, events, map completion...) -> activity, yields
  salvage      [[Salvages into::X]]                -> guidance text only (no pack kind)

Activities made here carry no time estimate (the wiki has none): the planner ignores an
activity without one until DRF measures it, so no number is invented.

Replies are kept per item under snapshot/wiki-sources/ (not per query: the chunks depend
on how the walk goes), so a run with the wiki down rebuilds the same sources.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.parse
from pathlib import Path
from typing import Callable

CHUNK = 6               # items per OR'ed query (URL length stays far under limits)
LIMIT = 500             # SMW rows per reply; paged with query-continue-offset
MAX_AGE_S = 6 * 24 * 3600   # daily retries reuse the weekly build's answers

KINDS = ("vendor", "achievement", "rewarded", "salvage")


def _q_vendor(names: list[str]) -> str:
    return ("[[Sells item::" + "||".join(names) + "]][[Has vendor.Is historical::N]]"
            "|?Sells item|?Has vendor|?Has vendor.Has context|?Has item cost|?Has item quantity|?Located in|?Has requirement"
            "|?Is historical|?Has daily purchase cap|?Has weekly purchase cap|?Has total purchase cap")


def _q_achievement(names: list[str]) -> str:
    return ("[[Gives item::" + "||".join(names) + "]]"
            "|?Gives item|?Gives item quantity|?Has game id|?Has canonical name|?Has achievement page|?Is historical")


def _q_rewarded(names: list[str]) -> str:
    return ("[[Rewards item::" + "||".join(names) + "]]"
            "|?Rewards item|?Rewarded by|?Rewarded by.Has context|?Rewarded by.Has location type"
            "|?Has rewarded item chance|?Has rewarded item quantity")


def _q_salvage(names: list[str]) -> str:
    return "[[Salvages into::" + "||".join(names) + "]]|?Salvages into|?Has canonical name"


def _q_track(name: str) -> str:
    return (f"[[Contains item in reward track::{name};?;?;?]]"
            "|?Contains item in reward track")


def _q_rewards_of(pages: list[str]) -> str:   # everything an event / location page rewards
    return ("[[Rewarded by::" + "||".join(pages) + "]]"
            "|?Rewarded by|?Rewards item|?Rewards item.Has game id|?Has rewarded item chance|?Has rewarded item quantity")


def _q_locations(types: list[str]) -> str:     # every location page of these types
    return ("[[Has context::Location]][[Has location type::" + "||".join(types) + "]]"
            "|?Has location type|?Has canonical name|?Located in|?Is historical|?Has map id|?Category")


def _q_contained(names: list[str]) -> str:     # containers that hold the item
    return ("[[Contains item::" + "||".join(names) + "]]"
            "|?Contains item|?Contained in|?Has contained item chance|?Has contained item quantity|?Contained in.Is historical")


def _q_place(names: list[str]) -> str:          # a location page's parent area and map (vendor waypoints)
    return "[[" + "||".join(names) + "]]|?Located in|?Has map id"


def _q_pagecats(names: list[str]) -> str:     # the categories of these pages (festival tags)
    # not an SMW ask (its query-size limit allows ~a dozen ORs): action=query prop=categories,
    # 50 titles a request; this string only names the query shape for the snapshot signature
    return "prop=categories|clshow=|redirects=1|titles=" + "|".join(names)


def _q_category(cats: list[str]) -> str:       # every page in one category (asked one at a time)
    return f"[[{cats[0]}]]|?Has location type|?Has canonical name|?Located in|?Is historical"


QUERIES = {"category": _q_category, "vendor": _q_vendor, "achievement": _q_achievement, "rewarded": _q_rewarded, "salvage": _q_salvage,
           "rewards_of": _q_rewards_of, "locations": _q_locations, "contained": _q_contained, "place": _q_place,
           "pagecats": _q_pagecats}
CHUNK_BY = {"pagecats": 50}   # MediaWiki's titles limit per query
# which printout names the item a row is about
ROW_ITEM = {"vendor": "Sells item", "achievement": "Gives item", "rewarded": "Rewards item", "salvage": "Salvages into",
            "rewards_of": "Rewarded by", "locations": "Has location type", "contained": "Contains item"}


# --------------------------------------------------------------------------- SMW values

def flat(v):
    """SMW printout value -> plain python (page -> title, record -> dict, text/number -> value)."""
    if isinstance(v, dict):
        if "fulltext" in v:
            return v["fulltext"]
        if "item" in v and "label" in v:
            items = [flat(x) for x in v["item"]]
            return items[0] if len(items) == 1 else items
        return {k: flat(x) for k, x in v.items()}
    if isinstance(v, list):
        return [flat(x) for x in v]
    return v


def rows(reply: dict) -> list[dict]:
    res = reply.get("query", {}).get("results", {})
    if isinstance(res, list):   # empty result is [] in formatversion 2
        return []
    return [{"_subject": k, **{p: flat(x) for p, x in v.get("printouts", {}).items()}} for k, v in res.items()]


def _int(v) -> int | None:
    if isinstance(v, list):
        v = v[0] if v else None
    try:
        return int(float(str(v).replace(",", ""))) if v not in (None, "") else None
    except ValueError:
        return None


def _true(v) -> bool:
    if isinstance(v, list):
        v = v[0] if v else None
    return str(v).lower() in ("t", "true", "1", "yes")


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


# --------------------------------------------------------------------------- store

class SourceStore:
    """ask() with a per-item snapshot. ``post(params) -> dict`` (one api.php POST) raises when the wiki is down."""

    def __init__(self, root: Path, post: Callable[[dict], dict], api: str = "https://wiki.guildwars2.com/api.php"):
        self.dir = root / "wiki-sources"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.post = post
        self.api = api
        self.down = False
        self.requests = 0
        self.stale = 0

    def _path(self, kind: str, name: str) -> Path:
        return self.dir / f"{kind}-{hashlib.sha256(name.encode()).hexdigest()[:24]}.json"

    def _load(self, kind: str, name: str) -> dict | None:
        p = self._path(kind, name)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    @staticmethod
    def _sig(kind: str) -> str:
        """The query's shape (printouts): a stored answer to an older query is fetched again."""
        q = _q_track("X") if kind == "track" else QUERIES[kind](["X"])
        return hashlib.sha256(q.encode()).hexdigest()[:12]

    def _save(self, kind: str, name: str, rws: list[dict], now: float) -> None:
        old = self._load(kind, name)
        if old and old["rows"] == rws and old.get("q") == self._sig(kind):   # unchanged: keep the file (and git blob); the age lives in fetched
            old["fetched"] = now
            rws, data = old["rows"], old
        else:
            data = {"item": name, "kind": kind, "fetched": now, "rows": rws, "q": self._sig(kind)}
        self._path(kind, name).write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=0), encoding="utf-8")

    def _ask(self, query: str) -> list[dict]:
        out, offset = [], 0
        while True:
            params = {"action": "ask", "format": "json", "formatversion": "2",
                      "query": f"{query}|limit={LIMIT}|offset={offset}"}
            self.requests += 1
            reply = self.post(params)
            if "error" in reply:
                raise SmwError(f"SMW ask: {reply['error'].get('info', reply['error'])} [{query[:200]}]")
            out += rows(reply)
            nxt = reply.get("query-continue-offset")
            if not nxt or nxt <= offset:
                return out
            offset = nxt

    def _categories(self, names: list[str]) -> list[dict]:
        """[{_subject: requested title, Category: [...]}] via action=query prop=categories (redirects followed)."""
        titles = [n.split("#")[0] for n in names]
        cats: dict[str, list[str]] = {}
        params = {"action": "query", "format": "json", "formatversion": "2", "prop": "categories",
                  "cllimit": "max", "redirects": "1", "titles": "|".join(dict.fromkeys(titles))}
        alias: dict[str, str] = {}
        while True:
            self.requests += 1
            reply = self.post(params)
            if "error" in reply:
                raise SmwError(f"categories: {reply['error'].get('info', reply['error'])}")
            q = reply.get("query", {})
            for m in q.get("normalized", []) + q.get("redirects", []):
                alias[m["from"]] = m["to"]
            for pg in q.get("pages", []):
                cats.setdefault(pg["title"], []).extend(c["title"] for c in pg.get("categories", []))
            if "continue" not in reply:
                break
            params = {**params, **reply["continue"]}
        out = []
        for n, t in zip(names, titles):
            final = t
            for _ in range(3):   # normalized, then redirect
                final = alias.get(final, final)
            if final in cats:
                out.append({"_subject": n, "Category": sorted(set(cats[final]))})
        return out

    def get(self, kind: str, names: list[str]) -> dict[str, list[dict]]:
        """kind -> rows per item name (every name gets an entry, possibly empty)."""
        now = time.time()
        out: dict[str, list[dict]] = {}
        todo = []
        for n in names:
            have = self._load(kind, n)
            if have and (self.down or (now - have.get("fetched", 0) < MAX_AGE_S and have.get("q", OLD_SIG.get(kind, self._sig(kind))) == self._sig(kind))):
                out[n] = have["rows"]
                self.stale += self.down
            else:
                todo.append(n)
        if kind == "track":
            for n in todo:
                out[n] = self._live_or_stored(kind, [n], lambda ns: self._ask(_q_track(ns[0])), now).get(n, [])
            return out
        if kind == "category":   # rows don't name their category: one query each, every row is its
            for n in todo:
                out[n] = self._live_or_stored(kind, [n], lambda ns: self._ask(_q_category(ns)), now, whole=True).get(n, [])
            return out
        if kind == "pagecats":
            for i in range(0, len(todo), CHUNK_BY[kind]):
                part = todo[i:i + CHUNK_BY[kind]]
                out.update(self._live_or_stored(kind, part, self._categories, now))
            return out
        step = CHUNK_BY.get(kind, CHUNK)
        for i in range(0, len(todo), step):
            part = todo[i:i + step]
            out.update(self._live_or_stored(kind, part, lambda ns: self._ask(QUERIES[kind](ns)), now))
        return out

    def _live_or_stored(self, kind, names, ask, now, whole=False) -> dict[str, list[dict]]:
        if not self.down:
            try:
                got = ask(names)
                by: dict[str, list[dict]] = {n: [] for n in names}
                if whole:
                    by[names[0]] = got
                    self._save(kind, names[0], got, now)
                    return by
                low = {n.lower(): n for n in names}
                for r in got:
                    for item in _items_of(kind, r):
                        if item.lower() in low:
                            by[low[item.lower()]].append(r)
                for n, rws in by.items():
                    self._save(kind, n, rws, now)
                return by
            except SmwError:
                raise                        # a bad query isn't an outage: say what the wiki said
            except Exception:  # noqa: BLE001 - one failure = down for this run
                self.down = True
        out = {}
        for n in names:
            have = self._load(kind, n)
            if have is None:
                raise WikiSourcesDown(f"wiki down and no stored {kind} data for '{n}'")
            self.stale += 1
            out[n] = have["rows"]
        return out


# answers stored before signatures existed: only these kinds' queries changed since
OLD_SIG = {"locations": "pre-map-id"}


class WikiSourcesDown(Exception):
    pass


class SmwError(Exception):
    pass


def _items_of(kind: str, row: dict) -> list[str]:
    if kind == "pagecats":              # the row names the title as it was asked
        return [row["_subject"]]
    if kind == "place":   # the row is the page itself
        return [row["_subject"].split("#")[0]]
    if kind == "track":
        recs = row.get("Contains item in reward track") or []
        return [r.get("Has reward track item") for r in recs if isinstance(r, dict) and r.get("Has reward track item")]
    v = row.get(ROW_ITEM[kind]) or []
    return v if isinstance(v, list) else [v]


# --------------------------------------------------------------------------- rows -> sources

COIN_PER_GOLD = 10000

# Vendors with identical costs share a weekly limit (the wiki's rule, and our grouping);
# config/vendor_caps.json lists the exceptions: a vendor with its own limit ("own_cap"), and
# offers of one vendor that share a limit despite different prices ("shared_cap" -> cap_group).
_CAPS_PATH = Path(__file__).resolve().parent.parent / "config" / "vendor_caps.json"
VENDOR_CAPS = json.loads(_CAPS_PATH.read_text(encoding="utf-8")) if _CAPS_PATH.exists() else {}
# config/excluded_trades.json: offers the owner rules out, item -> cost items that exclude the offer.
_EXCL_PATH = Path(__file__).resolve().parent.parent / "config" / "excluded_trades.json"
EXCLUDED_TRADES = {k: {x.lower() for x in v} for k, v in
                   (json.loads(_EXCL_PATH.read_text(encoding="utf-8")) if _EXCL_PATH.exists() else {}).items()
                   if not k.startswith("_")}


def vendor_sources(item: str, rws: list[dict], currencies: dict[str, int]) -> tuple[list[dict], list[str]]:
    """One source per distinct (cost, quantity, caps); the vendors that sell it that way are listed.
    Returns (sources, names of cost items that are items, not wallet currencies)."""
    cur = {k.lower(): v for k, v in currencies.items()}
    groups: dict[str, dict] = {}
    cost_items: list[str] = []
    for r in rws:
        if _true(r.get("Is historical")):
            continue
        if "Item" in (r.get("Has context") or []):
            continue   # the "vendor" is an item: a chest's choices or a container exchange, not a shop
        inputs = []
        for c in r.get("Has item cost") or []:
            if not isinstance(c, dict):
                continue
            n, v = c.get("Has item currency"), _int(c.get("Has item value"))
            if not n or not v:
                continue
            if n.lower() == "gold":
                inputs.append({"node_id": "currency:1", "count": v * COIN_PER_GOLD})
            elif n.lower() == "coin" or n.lower() in cur:
                inputs.append({"node_id": f"currency:{cur.get(n.lower(), 1)}", "count": v})
            else:
                inputs.append({"node_id": "@" + n, "count": v, "name": n})
                cost_items.append(n)
        if not inputs:
            continue
        banned = EXCLUDED_TRADES.get(item, set())
        if banned and any((c.get("Has item currency") or "").lower() in banned
                          for c in r.get("Has item cost") or [] if isinstance(c, dict)):
            continue   # ruled out by the owner (config/excluded_trades.json)
        qty = _int(r.get("Has item quantity")) or 1
        src = {"kind": "vendor", "inputs": sorted(inputs, key=lambda i: i["node_id"]), "output_count": qty}
        for key, prop in (("daily_cap", "Has daily purchase cap"), ("weekly_cap", "Has weekly purchase cap"),
                          ("max_units", "Has total purchase cap")):
            cap = _int(r.get(prop))
            if cap:
                src[key] = cap
        npc = (r.get("Has vendor") or [r["_subject"].split("#")[0]])[0]
        k = json.dumps(src, sort_keys=True)
        if npc in VENDOR_CAPS.get("own_cap", {}).get(item, []):
            k += "|own:" + npc   # its own limit: not shared with the vendors that charge the same
        g = groups.setdefault(k, {**src, "vendors": []})
        reqs = [re.sub(r"\[\[(?:[^]|]*\|)?([^]]*)\]\]", r"\1", x) for x in (r.get("Has requirement") or [])]
        g["vendors"].append({"npc": npc, "locations": r.get("Located in") or [], "requires": reqs})
    out = []
    shared = VENDOR_CAPS.get("shared_cap", {}).get(item, [])
    for g in groups.values():
        g["vendors"].sort(key=lambda v: (bool(v["requires"]), v["npc"]))   # no requirement first
        npcs = {v["npc"] for v in g["vendors"]}
        for grp in shared:
            if npcs <= set(grp) and (g.get("weekly_cap") or g.get("daily_cap")):
                g["cap_group"] = item + ": " + " + ".join(grp)   # one limit across these offers
        out.append(g)
    out.sort(key=lambda s: json.dumps(s["inputs"]))
    return out, cost_items


def achievement_sources(item: str, rws: list[dict]) -> list[dict]:
    out, seen = [], set()
    for r in rws:
        aid = _int(r.get("Has game id"))
        if not aid or aid in seen or _true(r.get("Is historical")):
            continue
        seen.add(aid)
        name = (r.get("Has canonical name") or [None])[0] or r["_subject"].split("#")[0]
        page = (r.get("Has achievement page") or [r["_subject"].split("#")[0]])[0]
        src = {"kind": "reward", "inputs": [], "achievement_id": aid, "achievement_name": name,
               "wiki_page": page}
        q = _int(r.get("Gives item quantity"))
        if q and q > 1:
            src["output_count"] = q
        out.append(src)
    return out


def activity_rows(item: str, rewarded: list[dict], tracks: list[dict]) -> list[dict]:
    """[{id, name, mode, count, chance, note}] for the item: reward tracks and 'rewarded by' places."""
    out = []
    for r in tracks:
        for rec in r.get("Contains item in reward track") or []:
            if not isinstance(rec, dict) or str(rec.get("Has reward track item", "")).lower() != item.lower():
                continue
            track = r["_subject"].split("#")[0]
            gm = str(rec.get("Has reward track item game mode") or "").lower()
            modes = ["wvw", "pvp"] if gm in ("", "both", "pvp and wvw") else ["wvw" if "wvw" in gm else "pvp"]
            num = _int(rec.get("Has reward track item number"))
            for m in modes:
                out.append({"id": f"rt:{slug(track)}:{m}", "name": f"{track} ({'WvW' if m == 'wvw' else 'PvP'})",
                            "mode": m, "count": _int(rec.get("Has reward track item quantity")) or 1,
                            "chance": "Guaranteed",
                            "note": f"reward track, reward {num}" if num else "reward track"})
    for r in rewarded:
        by = (r.get("Rewarded by") or [r["_subject"].split("#")[0]])[0]
        ctx = ", ".join(r.get("Has location type") or r.get("Has context") or [])
        chance = (r.get("Has rewarded item chance") or ["Chance"])[0]
        out.append({"id": f"wiki:{slug(by)}", "name": by, "mode": "pve",
                    "count": _int(r.get("Has rewarded item quantity")) or 1, "chance": chance,
                    "note": f"{chance.lower()} from {by}" + (f" ({ctx.lower()})" if ctx else "")})
    return out


def container_sources(item: str, rws: list[dict]) -> tuple[list[dict], list[str]]:
    """Boxes that surely give the item (guaranteed or the player's choice; not chance drops).
    Source: kind vendor with the box as its one input and open_container set (opening it)."""
    out, names = [], []
    for r in rws:
        chance = str((r.get("Has contained item chance") or [""])[0]).lower()
        if any(w in chance for w in ("chance", "rare", "uncommon", "unlikely")) or _true(r.get("Is historical")):
            continue
        box = (r.get("Contained in") or [r["_subject"].split("#")[0]])[0]
        if not box or box in names:
            continue
        names.append(box)
        out.append({"kind": "vendor", "inputs": [{"node_id": "@" + box, "count": 1}], "open_container": True,
                    "output_count": _int(r.get("Has contained item quantity")) or 1})
    return out, names
