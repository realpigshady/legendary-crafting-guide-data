"""One-off probe: fetch a few gw2.app pages (robots.txt allows everything except /api/ and
/preview/) so the page structure can be inspected before writing the real exporter.

    python tools/probe_gw2app.py --out probe
"""
import argparse
import time
import urllib.request
from pathlib import Path

UA = "LegendaryCraftingGuide-databuilder/0.1 (one-off interim export while the GW2 wiki is down; github.com/realpigshady)"
PAGES = [
    "/robots.txt",
    "/recipes/mystic/997",      # Nevermore
    "/recipes/mystic/1064",     # Mystic Tribute
    "/items/71383",             # Nevermore
    "/items/71820",             # Mystic Tribute
    "/items/74300",             # Gift of Nevermore
    "/items/74068",             # The Raven Staff (precursor)
    "/sitemap-en-mysticforgerecipe.xml",
    "/sitemap-en-timer.xml",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("probe"))
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    for p in PAGES:
        if p.startswith(("/api/", "/preview/")):
            continue  # disallowed by robots.txt
        req = urllib.request.Request("https://gw2.app" + p, headers={"User-Agent": UA})
        name = p.strip("/").replace("/", "_") or "index"
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                (a.out / name).write_bytes(r.read())
                print(p, r.status)
        except Exception as e:  # noqa: BLE001 - probe only, record and continue
            (a.out / (name + ".error")).write_text(str(e))
            print(p, "ERROR", e)
        time.sleep(3)  # polite: well under one request per second


if __name__ == "__main__":
    main()
