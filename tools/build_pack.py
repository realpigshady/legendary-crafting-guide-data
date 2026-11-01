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
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wiki_pack as wp  # noqa: E402

FORMAT_VERSION = 1
GW2_API = "https://api.guildwars2.com"
WIKI_API = "https://wiki.guildwars2.com/api.php"
USER_AGENT = (
    "LegendaryCraftingGuide-DataPack/0.1 "
    "(+https://github.com/realpigshady/legendary-crafting-guide-data; weekly batch job)"
)
MIN_INTERVAL_S = 1.0

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
            if p == self._index_path:
                continue
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

    def _fetch(self, url: str):
        wait = MIN_INTERVAL_S - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            for attempt in range(4):
                try:
                    with urllib.request.urlopen(req, timeout=30) as r:
                        return json.loads(r.read().decode("utf-8"))
                except Exception as e:  # noqa: BLE001 - retry any transport error
                    if attempt == 3:
                        raise
                    print(f"  retry {attempt + 1} for {url}: {e}", file=sys.stderr)
                    time.sleep(2 ** attempt * 2)
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


def untradeable_ids(http: Http, ids: list[int]) -> set[int]:
    """Items the Trading Post can't have (account bound / soulbound on acquire)."""
    out: set[int] = set()
    for i in range(0, len(ids), 200):
        part = sorted(set(ids[i:i + 200]))
        try:
            items = http.get_json(f"{GW2_API}/v2/items?ids={','.join(map(str, part))}", max_age_h=24 * 7)
        except SourceDown:
            continue
        for it in items if isinstance(items, list) else []:
            flags = set(it.get("flags", []))
            if flags & {"AccountBound", "SoulbindOnAcquire"}:
                out.add(it["id"])
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
    currencies = currency_ids(http)
    expand = set(CONFIG.get("always_expand", []))

    legendaries, nodes, warnings = [], {}, []
    stats: dict[str, dict] = {}
    for fam_id, fam in CONFIG["families"].items():
        if not fam.get("enabled", False):
            continue
        members = sorted(family_members(http, fam) & set(by_name))
        if not members:
            raise SystemExit(f"{fam_id}: 0 legendaries matched — check wiki_category "
                             f"'{fam['wiki_category']}' in config/families.json")
        if limit:
            members = members[:limit]
        print(f"{fam_id}: {len(members)} legendaries")
        before = (store.requests, store.bytes, store.fresh)
        try:
            tree = wp.walk(store, members, currencies, lambda ids: untradeable_ids(http, ids), expand)
        except wp.WikiDown as e:
            raise SourceDown(str(e)) from e
        for n in tree.nodes.values():
            nodes.setdefault(n["id"], n)
        warnings += [f"{fam_id}: {w}" for w in tree.warnings]
        stats[fam_id] = {"legendaries": len(members), "nodes": len(tree.nodes), "requests": store.requests - before[0],
                         "bytes": store.bytes - before[1], "pages_downloaded": store.fresh - before[2],
                         "pages_by_depth": tree.pages_by_depth}
        for title in members:
            item = by_name[title]
            legendaries.append({
                "id": f"{fam_id}:{item['id']}",
                "family": fam_id,
                "name": title,
                "item_id": item["id"],
                "root_node_id": f"item:{item['id']}",
                "weapon_type": item.get("details", {}).get("type"),
            })

    pack = {
        "format_version": FORMAT_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "legendaries": legendaries,
        "nodes": list(nodes.values()),
        "activities": [],  # TODO(0.6): activity catalog with wiki-seeded yields
        "all_legendary_item_ids": sorted(i["id"] for i in items),
        # Hosts that were down this run and served from snapshot instead.
        # The addon shows "wiki data from snapshot" when non-empty.
        "stale_sources": sorted(set(http.stale) | ({"wiki.guildwars2.com"} if store.stale else set())),
    }
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
