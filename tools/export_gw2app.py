"""One-off INTERIM export of legendary crafting trees from gw2.app while the GW2 wiki is down.

Policy (see legendary-crafting-guide/docs/SPEC.md and claude/DATA_SOURCES_RESEARCH.md):
  * pages only; /api/ and /preview/ are disallowed by gw2.app's robots.txt and never fetched
  * one request every 3 s, descriptive User-Agent, resumable, capped per run
  * TESTING ONLY: the export stays on the gw2app-export branch, feeds local test packs only,
    and is never used in a published pack or release; wiki data replaces it

What it does: starting from every legendary in the official /v2/legendaryarmory, it reads each
item page's "Acquisition" section (recipes, mystic recipes, achievements, vendors...), fetches
each Mystic Forge recipe page (ingredients are not in the official API), and follows ingredients
down the tree. Discipline recipes come from the official API (/v2/recipes) and their ingredients
are followed too. Output: export/gw2app.json (+ export/state.json for resuming).

    python tools/export_gw2app.py --out export --max-pages 3000
"""
from __future__ import annotations

import argparse
import html
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

UA = ("LegendaryCraftingGuide-databuilder/0.1 (one-off interim export while the GW2 wiki is down; "
      "github.com/realpigshady/legendary-crafting-guide-data)")
SITE = "https://gw2.app"
API = "https://api.guildwars2.com"
DELAY = 3.0

_last = 0.0


def get(url: str, polite: bool) -> str | None:
    global _last
    if url.startswith(SITE + "/api/") or url.startswith(SITE + "/preview/"):
        raise SystemExit("refusing a path robots.txt disallows: " + url)
    if polite:
        wait = _last + DELAY - time.time()
        if wait > 0:
            time.sleep(wait)
        _last = time.time()
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 429 or e.code >= 500:
                time.sleep(30 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            time.sleep(15 * (attempt + 1))
    return None


def section(page: str, start: str, end: str) -> str:
    i = page.find(">" + start + "<")
    if i < 0:
        return ""
    j = page.find(">" + end + "<", i)
    return page[i: j if j > 0 else len(page)]


def text(s: str) -> str:
    s = re.sub(r"<svg.*?</svg>|<script.*?</script>|<style.*?</style>", "", s, flags=re.S)
    s = re.sub(r"<[^>]+>", " | ", s)
    s = re.sub(r"\s+", " ", html.unescape(s))
    return re.sub(r"(\|\s*)+", "| ", s).strip(" |")


def parse_item(page: str) -> dict:
    acq = section(page, "Acquisition", "Used in")
    links = []
    for m in re.finditer(r'<a [^>]*href="(/[^"]+)"[^>]*>(.*?)</a>', acq, flags=re.S):
        href = html.unescape(m.group(1))
        if href.startswith(("/users/", "/search", "/crafting")):
            continue
        links.append({"href": href, "label": text(m.group(2))})
    title = re.search(r"<title>(.*?)</title>", page, flags=re.S)
    return {
        "name": html.unescape(title.group(1)).rsplit(" – GW2.app", 1)[0].strip() if title else "",
        "acquisition": links,
        "acquisition_text": text(acq)[:2000],
    }


def parse_mystic(page: str) -> dict:
    ings = []
    for li in re.finditer(r'<li class="ingredient-item.*?</li>', page, flags=re.S):
        t = li.group(0)
        href = re.search(r'href="/(items|currencies)/(\d+)"', t)
        qty = re.search(r'tabular-nums">\s*([\d,]+)\s*×', t)
        alt = re.search(r'alt="([^"]+)"', t)
        if not href:
            continue
        ings.append({"type": "item" if href.group(1) == "items" else "currency", "id": int(href.group(2)),
                     "count": int(qty.group(1).replace(",", "")) if qty else 1,
                     "name": html.unescape(alt.group(1)) if alt else ""})
    body = text(page[page.find("<body"):])
    m = re.search(r"Creates \| (?:([\d,]+) × \| )?([^|]+)\|", body)
    out_id = None
    if m:
        # the output link is the /items/<id> link whose text is the output name, outside the ingredient list
        for h in re.finditer(r'href="/items/(\d+)"', page):
            if int(h.group(1)) not in {i["id"] for i in ings}:
                out_id = int(h.group(1))
    return {"ingredients": ings, "output_id": out_id,
            "output_count": int(m.group(1).replace(",", "")) if m and m.group(1) else 1,
            "output_name": m.group(2).strip() if m else ""}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("export"))
    ap.add_argument("--max-pages", type=int, default=3000)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    state_f, data_f = a.out / "state.json", a.out / "gw2app.json"
    data = json.loads(data_f.read_text()) if data_f.exists() else {"items": {}, "mystic_recipes": {}, "recipes": {}}
    state = json.loads(state_f.read_text()) if state_f.exists() else {"queue": [], "done": []}
    done = set(state["done"])
    queue: list[str] = state["queue"]
    if not queue and not done:
        legends = json.loads(get(API + "/v2/legendaryarmory", False) or "[]")
        queue = [f"item:{i}" for i in legends]
        data["legendary_ids"] = legends

    def push(key: str) -> None:
        if key not in done and key not in queue:
            queue.append(key)

    pages = 0
    started = time.time()
    while queue and pages < a.max_pages and time.time() - started < 5 * 3600:
        key = queue.pop(0)
        kind, ident = key.split(":", 1)
        if kind == "item":
            page = get(f"{SITE}/items/{ident}", True); pages += 1
            if page:
                it = parse_item(page)
                data["items"][ident] = it
                for l in it["acquisition"]:
                    if m := re.fullmatch(r"/recipes/mystic/(\d+)", l["href"]):
                        push("mystic:" + m.group(1))
                    elif m := re.fullmatch(r"/recipes/(\d+)", l["href"]):
                        push("recipe:" + m.group(1))
        elif kind == "mystic":
            page = get(f"{SITE}/recipes/mystic/{ident}", True); pages += 1
            if page:
                r = parse_mystic(page)
                data["mystic_recipes"][ident] = r
                for i in r["ingredients"]:
                    if i["type"] == "item":
                        push(f"item:{i['id']}")
        elif kind == "recipe":   # official API, not gw2.app
            body = get(f"{API}/v2/recipes/{ident}", False)
            if body:
                r = json.loads(body)
                data["recipes"][ident] = r
                for i in r.get("ingredients", []):
                    if i.get("type", "Item") == "Item":
                        push(f"item:{i['id']}")
        done.add(key)
        if pages % 50 == 0:
            print(f"{pages} pages, queue {len(queue)}, items {len(data['items'])}, mystic {len(data['mystic_recipes'])}", flush=True)
            data_f.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
            state_f.write_text(json.dumps({"queue": queue, "done": sorted(done)}))
    data["source"] = "gw2.app (interim export while the GW2 wiki is down; data are ArenaNet game facts)"
    data["complete"] = not queue
    data_f.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    state_f.write_text(json.dumps({"queue": queue, "done": sorted(done)}))
    print(f"run done: {pages} pages; queue left {len(queue)}; items {len(data['items'])}, "
          f"mystic {len(data['mystic_recipes'])}, recipes {len(data['recipes'])}")


if __name__ == "__main__":
    main()
