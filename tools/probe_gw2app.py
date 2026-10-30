"""One-off probe: fetch sample gw2.app pages of each content type (robots.txt allows everything
except /api/ and /preview/) so the page structure can be inspected before extending the
interim TESTING-ONLY exporter.

    python tools/probe_gw2app.py --out probe
"""
import argparse
import re
import time
import urllib.request
from pathlib import Path

UA = "LegendaryCraftingGuide-databuilder/0.1 (one-off interim test export while the GW2 wiki is down; github.com/realpigshady)"
FIXED = [
    "/sitemap.xml",
    "/items/19925",   # Obsidian Shard (vendor)
    "/items/19675",   # Mystic Clover
    "/items/19976",   # Mystic Coin
    "/items/74356",   # sample precursor component
    "/achievements/2550",
    "/timers",
    "/maps",
    "/wizards-vault",
]
TYPES = ["timer", "mapchest", "location", "currency", "achievement", "map", "dungeon", "raid", "cat", "recipe"]


def fetch(p: str) -> bytes | None:
    if p.startswith(("/api/", "/preview/")):
        return None
    time.sleep(3)
    try:
        req = urllib.request.Request("https://gw2.app" + p, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()
    except Exception as e:  # noqa: BLE001 - probe only
        print(p, "ERROR", e)
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("probe"))
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    pages = list(FIXED)
    index = fetch("/sitemap.xml") or b""
    (a.out / "sitemap.xml").write_bytes(index)
    maps = re.findall(r"<loc>https://gw2\.app(/sitemap-en-[^<]+)</loc>", index.decode("utf-8", "replace"))
    for t in TYPES:
        sm = next((m for m in maps if re.match(rf"/sitemap-en-{t}(-\d+)?\.xml$", m)), None)
        if not sm:
            continue
        body = fetch(sm) or b""
        (a.out / sm.strip("/")).write_bytes(body)
        urls = re.findall(r"<loc>https://gw2\.app(/[^<]+)</loc>", body.decode("utf-8", "replace"))
        pages += urls[:2]
    for p in pages[1:]:
        body = fetch(p)
        name = p.strip("/").replace("/", "_").replace("?", "_") or "index"
        if body:
            (a.out / name).write_bytes(body)
            print(p, "ok", len(body))


if __name__ == "__main__":
    main()
