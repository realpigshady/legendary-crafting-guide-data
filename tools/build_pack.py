#!/usr/bin/env python3
"""Build the Legendary Crafting Guide data pack.

Sources:
  * Official GW2 API (no key)           -> legendary item list, item metadata
  * GW2 wiki via MediaWiki API (api.php) -> Mystic Forge / collection recipes, guidance

Polite by design: one request per second, on-disk cache between runs,
descriptive User-Agent. Never scrapes rendered HTML.

Usage:
  python tools/build_pack.py --out dist            # full build (CI)
  python tools/build_pack.py --out dist --limit 3  # quick local check
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wiki_pack as wp  # noqa: E402
import wiki_sources as wsrc  # noqa: E402
import event_timers as etm  # noqa: E402
import activity_catalog as acat  # noqa: E402
import vendor_waypoints as vwp  # noqa: E402
import festivals as fest  # noqa: E402
import forge_outcomes as fo  # noqa: E402

FORMAT_VERSION = 1
GW2_API = "https://api.guildwars2.com"
WIKI_API = "https://wiki.guildwars2.com/api.php"
USER_AGENT = (
    "LegendaryCraftingGuide-DataPack/0.1 "
    "(+https://github.com/realpigshady/legendary-crafting-guide-data; weekly batch job)"
)
MIN_INTERVAL_S = 1.0
ASK_INTERVAL_S = 2.0        # SMW ask queries are heavier: half the pace

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config" / "families.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- http

class SourceDown(Exception):
    """Source unreachable and no snapshot to fall back on."""


class Http:
    """Fetch with a persistent snapshot.

    Every successful response is written to the snapshot directory, which CI
    commits back to the repo. If a source is down, the last snapshot of that
    URL is used instead (any age) and the hit is recorded as stale.

    Size discipline: page files hold only the response, serialised
    deterministically and rewritten only when content changes, so git stores a
    new blob only for pages that actually changed. Fetch times live in one
    small index.json (file mtimes are useless: git checkout resets them).
    """

    def __init__(self, snapshot_dir: Path, offline: bool = False):
        self.dir = snapshot_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self._last = 0.0
        self._down_hosts: set[str] = set()
        self.stale: dict[str, int] = {}   # host -> stale hits
        self.fresh: dict[str, int] = {}   # host -> fresh fetches
        self._index_path = self.dir / "index.json"
        self.index: dict[str, float] = (
            json.loads(self._index_path.read_text(encoding="utf-8")) if self._index_path.exists() else {})
        self._used: set[str] = set()

    def _path(self, url: str) -> Path:
        host = urllib.parse.urlparse(url).netloc
        return self.dir / host / f"{hashlib.sha256(url.encode()).hexdigest()}.json"

    def _key(self, path: Path) -> str:
        return path.relative_to(self.dir).as_posix()

    def get_json(self, url: str, max_age_h: float = 24.0):
        host = urllib.parse.urlparse(url).netloc
        path = self._path(url)
        key = self._key(path)
        self._used.add(key)
        fetched_at = self.index.get(key, 0.0)
        if path.exists() and (time.time() - fetched_at) < max_age_h * 3600:
            return json.loads(path.read_text(encoding="utf-8"))["body"]
        if not self.offline and host not in self._down_hosts:
            try:
                data = self._fetch(url)
                path.parent.mkdir(parents=True, exist_ok=True)
                text = json.dumps({"url": url, "body": data}, sort_keys=True, ensure_ascii=False, indent=0)
                if not path.exists() or path.read_text(encoding="utf-8") != text:
                    path.write_text(text, encoding="utf-8")
                self.index[key] = time.time()
                self.fresh[host] = self.fresh.get(host, 0) + 1
                return data
            except Exception as e:  # noqa: BLE001
                # One host failing all retries = treat as down for the rest of the run,
                # so an outage costs one retry cycle, not one per page.
                print(f"  {host} unreachable ({e}); using snapshots for this run", file=sys.stderr)
                self._down_hosts.add(host)
        if path.exists():
            self.stale[host] = self.stale.get(host, 0) + 1
            return json.loads(path.read_text(encoding="utf-8"))["body"]
        raise SourceDown(f"{url}: source down and no snapshot")

    def save_index(self):
        lines = [f"{json.dumps(k)}:{round(v)}" for k, v in sorted(self.index.items())]
        self._index_path.write_text("{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8")

    def prune(self) -> int:
        """Delete snapshot pages this full run did not use. Skipped during outages,
        so a partial run never throws away fallback copies."""
        if self._down_hosts or self.offline:
            return 0
        removed = 0
        for p in self.dir.rglob("*.json"):
            if p == self._index_path or p.name in ("item-flags.json", "activity-catalog.json", "map-index.json") or p.relative_to(self.dir).parts[0] in ("wiki-pages", "wiki-sources"):
                continue   # wiki-pages/ belongs to the PageStore (revision-checked, not URL-keyed)
            key = self._key(p)
            if key not in self._used:
                p.unlink()
                self.index.pop(key, None)
                removed += 1
        return removed

    def get_live(self, url: str):
        """One request, no snapshot (callers keep their own copy, e.g. the wiki PageStore)."""
        host = urllib.parse.urlparse(url).netloc
        if self.offline or host in self._down_hosts:
            raise SourceDown(f"{host} offline for this run")
        try:
            data = self._fetch(url)
        except Exception:
            self._down_hosts.add(host)
            raise
        self.fresh[host] = self.fresh.get(host, 0) + 1
        return data

    def post_live(self, url: str, params: dict):
        """One POST (SMW ask: long queries, kinder to the wiki's cache layer), no snapshot.
        Slower pace and long backoff on 403/429/503: the wiki rate-limits query bursts."""
        host = urllib.parse.urlparse(url).netloc
        if self.offline or host in self._down_hosts:
            raise SourceDown(f"{host} offline for this run")
        body = urllib.parse.urlencode(params).encode()
        for attempt in range(6):
            wait = ASK_INTERVAL_S - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            req = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT,
                                                                  "Content-Type": "application/x-www-form-urlencoded"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = json.loads(r.read().decode("utf-8", errors="replace"))
                self.fresh[host] = self.fresh.get(host, 0) + 1
                return data
            except urllib.error.HTTPError as e:
                if e.code not in (403, 429, 502, 503, 504) or attempt == 5:
                    self._down_hosts.add(host)
                    raise
                pause = min(300, 15 * 2 ** attempt)
                print(f"  ask {e.code}, waiting {pause}s (try {attempt + 1})", file=sys.stderr)
                time.sleep(pause)
            except Exception:
                if attempt == 5:
                    self._down_hosts.add(host)
                    raise
                time.sleep(10 * (attempt + 1))
            finally:
                self._last = time.monotonic()

    def _fetch(self, url: str):
        wait = MIN_INTERVAL_S - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            for attempt in range(6):
                try:
                    if attempt:
                        self._last = time.monotonic()
                    with urllib.request.urlopen(req, timeout=30) as r:
                        raw = r.read().decode("utf-8", errors="replace")
                        try:
                            return json.loads(raw)
                        except json.JSONDecodeError:
                            # a maintenance page or bot check instead of the API: say what came back
                            snippet = " ".join(raw[:200].split())
                            raise ValueError(f"HTTP {r.status} {r.headers.get('Content-Type', '?')}, not JSON: {snippet!r}") from None
                except Exception as e:  # noqa: BLE001 - retry any transport error
                    if attempt == 5:
                        raise
                    limited = isinstance(e, urllib.error.HTTPError) and e.code in (403, 429, 502, 503, 504)
                    pause = min(300, 15 * 2 ** attempt) if limited else 2 ** attempt * 2   # rate limit: long backoff
                    print(f"  retry {attempt + 1} for {url} in {pause}s: {e}", file=sys.stderr)
                    time.sleep(pause)
        finally:
            self._last = time.monotonic()


def wiki(http: Http, **params) -> dict:
    params = {"format": "json", "formatversion": "2", **params}
    return http.get_json(f"{WIKI_API}?{urllib.parse.urlencode(params)}")


# --------------------------------------------------------------------------- wikitext parsing
# (in wiki_pack.py; kept here under the old names)
find_templates = wp.find_templates
parse_recipe = wp.parse_recipe


# --------------------------------------------------------------------------- build

def legendary_items(http: Http) -> list[dict]:
    ids = http.get_json(f"{GW2_API}/v2/legendaryarmory")
    ids = [i["id"] if isinstance(i, dict) else i for i in ids]
    items = []
    for chunk in range(0, len(ids), 200):
        part = ids[chunk:chunk + 200]
        items += http.get_json(f"{GW2_API}/v2/items?ids={','.join(map(str, part))}")
    return items


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def family_members(http: Http, family: dict) -> set[str]:
    titles: set[str] = set()
    cont: dict = {}
    while True:
        res = wiki(http, action="query", list="categorymembers",
                   cmtitle=family["wiki_category"], cmlimit="500", cmnamespace="0", **cont)
        titles |= {m["title"] for m in res.get("query", {}).get("categorymembers", [])}
        if "continue" not in res:
            return titles
        cont = {"cmcontinue": res["continue"]["cmcontinue"]}


def match_family(family: dict, category: set[str], api_names: set[str],
                 resolve=lambda title: None) -> tuple[dict[str, str], list[str]]:
    """Wiki title -> API item name for the family's members, plus warnings.

    Without wiki_titles every category member that is a legendary item counts. With them
    (the wiki has one category for all weapon generations) only those titles count; each is
    matched to the category and the API ignoring case and punctuation (HOPE vs H.O.P.E.);
    a title not in the category is looked up directly (``resolve`` follows redirects)."""
    api = {_norm(n): n for n in api_names}
    if "wiki_titles" not in family:
        return {t: api[_norm(t)] for t in category if _norm(t) in api}, []
    cat = {_norm(t): t for t in category}
    out, warn = {}, []
    for want in family["wiki_titles"]:
        title = cat.get(_norm(want)) or resolve(want)
        if not title:
            warn.append(f"{want}: not in {family['wiki_category']}")
            continue
        if _norm(title) not in api:
            warn.append(f"{title}: no API legendary of that name")
            continue
        out[title] = api[_norm(title)]
    return out, warn


def untradeable_ids(http: Http, ids: list[int]) -> set[int]:
    """Items the Trading Post can't have (account bound / soulbound on acquire).

    Flags are also kept per item in snapshot/item-flags.json: the batches asked for depend
    on how far the walk got, so a URL-keyed snapshot alone misses them when the API is down."""
    path = http.dir / "item-flags.json"
    known: dict[str, bool] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    before = dict(known)
    out: set[int] = set()
    want = sorted(set(ids))
    for i in range(0, len(want), 200):
        part = want[i:i + 200]
        try:
            items = http.get_json(f"{GW2_API}/v2/items?ids={','.join(map(str, part))}", max_age_h=24 * 7)
        except SourceDown:
            items = []
        for it in items if isinstance(items, list) else []:
            known[str(it["id"])] = bool(set(it.get("flags", [])) & {"AccountBound", "SoulbindOnAcquire"})
    for i in want:
        if known.get(str(i)):
            out.add(i)
    if known != before:
        path.write_text(json.dumps(dict(sorted(known.items(), key=lambda kv: int(kv[0]))), indent=0) + "\n", encoding="utf-8")
    return out


def currency_ids(http: Http) -> dict[str, int]:
    try:
        cur = http.get_json(f"{GW2_API}/v2/currencies?ids=all", max_age_h=24 * 7)
    except SourceDown:
        return {}
    return {c["name"]: c["id"] for c in cur if c.get("name")}


def build(out: Path, cache: Path, limit: int | None, offline: bool = False) -> dict:
    http = Http(cache, offline=offline)
    try:
        return _build(http, out, limit)
    finally:
        # Persist fetch times even if the build aborts, so successful fetches count.
        http.save_index()


def _build(http: Http, out: Path, limit: int | None) -> dict:
    items = legendary_items(http)
    by_name = {i["name"]: i for i in items}
    store = wp.PageStore(http.dir, http.get_live, WIKI_API)
    smw = wsrc.SourceStore(http.dir, lambda params: http.post_live(WIKI_API, params), WIKI_API)
    currencies = currency_ids(http)
    expand = set(CONFIG.get("always_expand", []))

    legendaries, nodes, warnings, acts = [], {}, [], {}
    random_seen: dict[str, list] = {}
    stats: dict[str, dict] = {}
    for fam_id, fam in CONFIG["families"].items():
        if not fam.get("enabled", False):
            continue
        def resolve(title: str) -> str | None:
            try:
                page = store.pages([title]).get(title)
            except wp.WikiDown:
                return None
            return page.title if page else None
        if fam.get("from") == "armory":   # every legendary of these API types, by its wiki page
            want = sorted({i["name"] for i in items if i.get("type") in fam["types"] and i.get("name")})
            try:
                pages = store.pages(want)
            except wp.WikiDown as e:
                raise SourceDown(str(e)) from e
            matched = {pages[n].title: n for n in want if n in pages}
            miss = [f"{n}: no wiki page" for n in want if n not in pages]
        else:
            matched, miss = match_family(fam, family_members(http, fam), set(by_name), resolve)
        warnings += [f"{fam_id}: {w}" for w in miss]
        for w in miss:
            print(f"  {fam_id}: {w}", file=sys.stderr)
        members = sorted(matched)
        if not members:
            raise SystemExit(f"{fam_id}: 0 legendaries matched — check config/families.json")
        if limit:
            members = members[:limit]
        print(f"{fam_id}: {len(members)} legendaries")
        before = (store.requests, store.bytes, store.fresh)
        try:
            ids = [i["id"] for i in items if i.get("name") in set(matched.values())]
            tree = wp.walk(store, members, currencies, lambda ids_: untradeable_ids(http, ids_), expand, sources=smw, root_ids=ids)
        except (wp.WikiDown, wsrc.WikiSourcesDown) as e:
            raise SourceDown(str(e)) from e
        for n in tree.nodes.values():
            nodes.setdefault(n["id"], n)
        for a in tree.activities.values():
            acts.setdefault(a["id"], a)
        for k, v in tree.random_seen.items():
            random_seen.setdefault(k, v)
        warnings += [f"{fam_id}: {w}" for w in tree.warnings]
        stats[fam_id] = {"legendaries": len(members), "nodes": len(tree.nodes), "activities": len(tree.activities),
                         "smw_requests": smw.requests, "requests": store.requests - before[0],
                         "bytes": store.bytes - before[1], "pages_downloaded": store.fresh - before[2],
                         "pages_by_depth": tree.pages_by_depth}
        root_of = {n["name"]: n["id"] for n in tree.nodes.values()}
        for title in members:
            # every API item of that name (two items can share one, e.g. Selachimorpha): same tree
            for item in [i for i in items if i.get("name") == matched[title]] or [by_name[matched[title]]]:
                legendaries.append({
                    "id": f"{fam_id}:{item['id']}",
                    "family": fam_id,
                    "name": title,
                    "item_id": item["id"],
                    "root_node_id": f"item:{item['id']}" if f"item:{item['id']}" in tree.nodes else root_of.get(title, f"item:{item['id']}"),
                    "weapon_type": item.get("details", {}).get("type"),
                })

    # random forge recipes: expected output and byproducts from the wiki's drop research
    try:
        stats["forge_outcomes"], fwarn = fo.apply(nodes, store, random_seen)
    except wp.WikiDown as e:
        raise SourceDown(str(e)) from e
    warnings += fwarn
    fs = stats["forge_outcomes"]
    for nm, v in fs["items"].items():
        print(f"forge outcomes {nm}: {v['sources']} sources, {v['expected_per_unit']}/unit ({v['expected_from']}; "
              f"research {v['research_expected_per_unit']} over {v['attempts']} attempts), {v['byproducts']} byproducts")
    print(f"forge outcomes: no research {fs['no_research']}; random recipes not in the pack (cut as cycles or unreached): {len(fs['not_in_pack'])}")
    for n in nodes.values():
        n.pop("_page_text", None)

    try:
        events = etm.load(store)
    except wp.WikiDown:
        events = None
    if not events:
        warnings.append("event timers: wiki data unavailable, timed events missing from the catalog")
    try:
        catalog = acat.build(events, smw, currencies)
    except wsrc.WikiSourcesDown as e:
        raise SourceDown(str(e)) from e
    # full catalog for other packs (test pack): every activity, every guaranteed yield
    (http.dir / "activity-catalog.json").write_text(
        json.dumps([{k: v for k, v in a.items() if not k.startswith("_")} for a in catalog], ensure_ascii=False, sort_keys=True, indent=0) + "\n",
        encoding="utf-8")
    # one activity per wiki page: places found from item rewards fold into the catalog's entry
    by_page = {a["_page"].lower(): a for a in catalog}
    rename = {}
    for a in acts.values():
        c = by_page.get(a["name"].split(" (")[0].lower())
        if c:
            rename[a["id"]] = c["id"]
            for y in a["yields"]:
                if not any(z["node_id"] == y["node_id"] for z in c["yields"]):
                    c["yields"].append(y)
    final = {a["id"]: a for a in acts.values() if a["id"] not in rename}
    for a in catalog:
        a = {k: v for k, v in a.items() if not k.startswith("_")}
        a["yields"] = [y for y in a["yields"] if y["node_id"] in nodes]   # only what this pack's trees use
        final[a["id"]] = a
    for n in nodes.values():
        for src in n["sources"]:
            if src.get("activity_id") in rename:
                src["activity_id"] = rename[src["activity_id"]]
        n["sources"] = [x for i, x in enumerate(n["sources"]) if x not in n["sources"][:i]]
    for a in final.values():
        for y in a["yields"]:
            srcs = nodes[y["node_id"]]["sources"]
            if not any(x.get("activity_id") == a["id"] for x in srcs):
                srcs[:] = [x for x in srcs if x["kind"] != "other"] + [{"kind": "activity", "inputs": [], "activity_id": a["id"]}]
    # nearest waypoint per vendor (Crafting > Vendor button): wiki area -> API sector -> waypoint
    maps, _ = vwp.load(http.dir, http._fetch, http.offline)   # paced, retried; a failure here doesn't mark the API down
    def places(names: list[str]) -> dict[str, list[dict]]:
        try:
            return smw.get("place", names)
        except wsrc.WikiSourcesDown:   # never asked before and the wiki is down: those vendors stay without
            return {}
    stats["vendor_waypoints"] = vwp.annotate(list(nodes.values()), maps, places)
    vw = stats["vendor_waypoints"]
    print(f"vendor waypoints: {vw['with_waypoint']}/{vw['vendors']} vendors ({vw['maps_indexed']} maps indexed)")
    if not maps:
        warnings.append("vendor waypoints: no map index (API down and nothing stored)")
    # festivals: windows from each festival page, tags on activities and vendor sources.
    # Catalog entries keep their _page/_cats/_map until here (stripped below).
    festivals, fwarn = fest.windows(store)
    warnings += fwarn
    by_cat = {a["id"]: a for a in catalog}
    tag_acts = [by_cat.get(a["id"], a) for a in final.values()]

    def page_cats(pages: list[str]) -> dict[str, list[dict]]:
        try:
            return smw.get("pagecats", pages)
        except wsrc.WikiSourcesDown as e:   # never asked before and the wiki is down: untagged this run
            warnings.append(f"festivals: page categories unavailable ({e}); festival tags incomplete")
            return {}
    stats["festivals"] = fest.tag(tag_acts, list(nodes.values()), page_cats)
    for a in tag_acts:
        if a.get("festival") and a["id"] in final:
            final[a["id"]]["festival"] = a["festival"]
    for a in final.values():
        for k in [k for k in a if k.startswith("_")]:
            del a[k]
    nwin = {w["name"]: len(w["windows"]) for w in festivals}
    for f, v in stats["festivals"]["by_festival"].items():
        print(f"festival {f}: {v['activities']} activities, {v['vendor_sources']} vendor sources, {nwin.get(f, 0)} windows")
    for f, n in stats["festivals"]["unknown_festivals"].items():
        warnings.append(f"festivals: vendor requirement names unknown festival '{f}' ({n}x): add it to config/festivals.json")
    act_list = sorted(final.values(), key=lambda a: a["id"])
    timed_n = sum(1 for a in act_list if a.get("est_minutes"))
    print(f"activities: {len(act_list)} ({timed_n} timed, {sum(1 for a in act_list if a['yields'])} with yields for these trees)")
    pack = {
        "format_version": FORMAT_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "legendaries": legendaries,
        "nodes": list(nodes.values()),
        "activities": act_list,   # wiki places/tracks; metas timed from the wiki event timer, the rest by DRF
        "all_legendary_item_ids": sorted(i["id"] for i in items),
        "festivals": festivals,   # windows per festival; sources with "festival" are planned only inside one
        # Hosts that were down this run and served from snapshot instead.
        # The addon shows "wiki data from snapshot" when non-empty.
        "stale_sources": sorted(set(http.stale) | ({"wiki.guildwars2.com"} if store.stale or smw.down else set())),
    }
    if smw.down:
        warnings.insert(0, f"STALE: wiki item sources (vendors, achievements, rewards) from snapshot ({smw.stale})")
    if http.stale or store.stale:
        warnings.insert(0, "STALE: served from snapshot for " +
                        ", ".join([f"{h} ({n} pages)" for h, n in sorted(http.stale.items())] +
                                  ([f"wiki pages ({store.stale})"] if store.stale else [])))
    if limit is None:
        removed = http.prune()
        if removed:
            print(f"pruned {removed} unused snapshot pages")
    out.mkdir(parents=True, exist_ok=True)
    body = json.dumps(pack, ensure_ascii=False, separators=(",", ":"))
    (out / "pack.json").write_text(body, encoding="utf-8")
    (out / "pack.json.sha256").write_text(hashlib.sha256(body.encode()).hexdigest() + "  pack.json\n")
    (out / "warnings.txt").write_text("\n".join(warnings) + "\n", encoding="utf-8")
    (out / "stats.json").write_text(json.dumps(stats, indent=1) + "\n", encoding="utf-8")   # per-family size log
    for fam_id, st in stats.items():
        if "nodes" not in st:
            continue
        print(f"{fam_id}: {st['nodes']} nodes, {st['requests']} wiki requests, {st['pages_downloaded']} pages, {st['bytes'] // 1024} KB")
    return pack


def diff_new_legendaries(prev_pack: Path, pack: dict, out: Path) -> list[int]:
    if not prev_pack.exists():
        return []
    prev = set(json.loads(prev_pack.read_text(encoding="utf-8")).get("all_legendary_item_ids", []))
    new = [i for i in pack["all_legendary_item_ids"] if i not in prev]
    if new:
        (out / "new_legendaries.txt").write_text("\n".join(map(str, new)) + "\n")
    return new


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "dist")
    ap.add_argument("--snapshot", type=Path, default=ROOT / "snapshot",
                    help="committed snapshot of every fetched response (fallback when a source is down)")
    ap.add_argument("--previous", type=Path, help="previous pack.json for new-legendary diff")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--offline", action="store_true", help="never hit the network; snapshot only")
    args = ap.parse_args()

    try:
        pack = build(args.out, args.snapshot, args.limit, args.offline)
    except SourceDown as e:
        print(f"ABORT: {e}", file=sys.stderr)
        return 2
    if args.previous:
        new = diff_new_legendaries(args.previous, pack, args.out)
        if new:
            print(f"NEW legendary item ids: {new}")
    print(f"pack: {len(pack['legendaries'])} legendaries, {len(pack['nodes'])} nodes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
