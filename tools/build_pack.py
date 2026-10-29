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

class Http:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._last = 0.0

    def get_json(self, url: str, max_age_h: float = 24.0):
        key = hashlib.sha256(url.encode()).hexdigest()
        path = self.cache_dir / f"{key}.json"
        if path.exists() and (time.time() - path.stat().st_mtime) < max_age_h * 3600:
            return json.loads(path.read_text(encoding="utf-8"))
        wait = MIN_INTERVAL_S - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read().decode("utf-8"))
                break
            except Exception as e:  # noqa: BLE001 - retry any transport error
                if attempt == 3:
                    raise
                print(f"  retry {attempt + 1} for {url}: {e}", file=sys.stderr)
                time.sleep(2 ** attempt * 2)
        self._last = time.monotonic()
        path.write_text(json.dumps(data), encoding="utf-8")
        return data


def wiki(http: Http, **params) -> dict:
    params = {"format": "json", "formatversion": "2", **params}
    return http.get_json(f"{WIKI_API}?{urllib.parse.urlencode(params)}")


# --------------------------------------------------------------------------- wikitext parsing

def find_templates(wikitext: str, name: str) -> list[dict[str, str]]:
    """Return params of every {{name ...}} template (handles nested braces)."""
    out = []
    pat = re.compile(r"\{\{\s*" + re.escape(name) + r"\s*\|", re.IGNORECASE)
    for m in pat.finditer(wikitext):
        depth, i = 0, m.start()
        while i < len(wikitext) - 1:
            pair = wikitext[i:i + 2]
            if pair == "{{":
                depth += 1
                i += 2
                continue
            if pair == "}}":
                depth -= 1
                i += 2
                if depth == 0:
                    break
                continue
            i += 1
        body = wikitext[m.end():i - 2]
        out.append(_split_params(body))
    return out


def _split_params(body: str) -> dict[str, str]:
    parts, depth, cur = [], 0, []
    i = 0
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


_ING_RE = re.compile(r"^\s*(\d+)\s+(.+?)\s*$")


def parse_recipe(params: dict[str, str]) -> dict | None:
    """Convert a {{Recipe}} template into a pack source option."""
    source = params.get("source", "").lower()
    ingredients = []
    for n in range(1, 9):
        raw = params.get(f"ingredient{n}")
        if not raw:
            continue
        m = _ING_RE.match(raw)
        count, name = (int(m.group(1)), m.group(2)) if m else (1, raw)
        ingredients.append({"name": name, "count": count})
    if not ingredients:
        return None
    if "mystic forge" in source:
        kind = "mystic_forge"
    elif source in ("automatic", "discovery", "recipe sheet"):
        kind = "craft"
    else:
        kind = "other"
    return {
        "kind": kind,
        "inputs": ingredients,
        "output_count": int(params.get("quantity", "1") or 1),
        "disciplines": [d.strip() for d in params.get("disciplines", "").split(",") if d.strip()],
        "recipe_id": params.get("id") or None,
    }


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


def page_wikitext(http: Http, title: str) -> str:
    res = wiki(http, action="parse", page=title, prop="wikitext", redirects="1")
    return res.get("parse", {}).get("wikitext", "")


def build(out: Path, cache: Path, limit: int | None) -> dict:
    http = Http(cache)
    items = legendary_items(http)
    by_name = {i["name"]: i for i in items}

    legendaries, nodes, warnings = [], {}, []
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
        for title in members:
            item = by_name[title]
            node_id = f"item:{item['id']}"
            text = page_wikitext(http, title)
            sources = [s for s in (parse_recipe(p) for p in find_templates(text, "Recipe")) if s]
            if not sources:
                warnings.append(f"{title}: no recipe found")
            nodes[node_id] = {
                "id": node_id,
                "type": "forge" if any(s["kind"] == "mystic_forge" for s in sources) else "craft",
                "name": title,
                "item_id": item["id"],
                "sources": sources,
                "guidance": {
                    "wiki_url": "https://wiki.guildwars2.com/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
                    "text": "",
                    "waypoint_chat_code": None,  # TODO(0.2): nearest waypoint via /v2/continents
                },
            }
            legendaries.append({
                "id": f"{fam_id}:{item['id']}",
                "family": fam_id,
                "name": title,
                "item_id": item["id"],
                "root_node_id": node_id,
                "weapon_type": item.get("details", {}).get("type"),
            })
            # TODO(0.2): recurse into ingredients (gifts, precursor collections) by name -> page.

    pack = {
        "format_version": FORMAT_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "legendaries": legendaries,
        "nodes": list(nodes.values()),
        "activities": [],  # TODO(0.6): activity catalog with wiki-seeded yields
        "all_legendary_item_ids": sorted(i["id"] for i in items),
    }
    out.mkdir(parents=True, exist_ok=True)
    body = json.dumps(pack, ensure_ascii=False, separators=(",", ":"))
    (out / "pack.json").write_text(body, encoding="utf-8")
    (out / "pack.json.sha256").write_text(hashlib.sha256(body.encode()).hexdigest() + "  pack.json\n")
    (out / "warnings.txt").write_text("\n".join(warnings) + "\n", encoding="utf-8")
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
    ap.add_argument("--cache", type=Path, default=ROOT / ".cache")
    ap.add_argument("--previous", type=Path, help="previous pack.json for new-legendary diff")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    pack = build(args.out, args.cache, args.limit)
    if args.previous:
        new = diff_new_legendaries(args.previous, pack, args.out)
        if new:
            print(f"NEW legendary item ids: {new}")
    print(f"pack: {len(pack['legendaries'])} legendaries, {len(pack['nodes'])} nodes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
