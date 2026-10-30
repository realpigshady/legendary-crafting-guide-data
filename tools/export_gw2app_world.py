"""TESTING-ONLY companion to export_gw2app.py: timers and daily metas from gw2.app (2 pages,
robots.txt-allowed, never /api/), plus achievement details from the OFFICIAL API for every
achievement the legendary export links to. Never used in a published pack or release.

    python tools/export_gw2app_world.py --out world [--export export/gw2app.json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
from pathlib import Path

from export_gw2app import API, SITE, get, text

INTERVALS_MIN = [15, 20, 30, 45, 60, 90, 120, 180, 240, 360, 480, 720, 1440]


def parse_timers(page: str, fetched: dt.datetime) -> list[dict]:
    """Map-level timers: group, name, waypoint, next start (UTC) and a derived interval."""
    t = text(page[page.find("<body"):])
    t = t[t.find("Test notification") + len("Test notification"):] if "Test notification" in t else t
    out, group = [], ""
    pat = re.compile(r"\|?\s*(?:([^|]+?) \| )?([^|]+?) \| (\[&[A-Za-z0-9+/=]+\]|\(No waypoint\)) \| (?:Now \| )?"
                     r"Next at (\d{1,2}):(\d{2}) (AM|PM) \(in [^)]*\) \| [^|]+ \| (\d+) more before reset")
    for m in pat.finditer(t):
        maybe_group, name, wp, hh, mm, ap, left = m.groups()
        if maybe_group and not maybe_group.strip().startswith(("Next", "Now")) and "left" not in maybe_group:
            group = maybe_group.strip()
        h = int(hh) % 12 + (12 if ap == "PM" else 0)
        nxt = fetched.replace(hour=h, minute=int(mm), second=0, microsecond=0)
        if nxt < fetched - dt.timedelta(minutes=1):
            nxt += dt.timedelta(days=1)
        reset = (fetched + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        k = int(left)
        mins_to_reset = (reset - nxt).total_seconds() / 60
        interval = next((i for i in INTERVALS_MIN if (k - 1) * i < mins_to_reset <= k * i), None)
        out.append({"group": group, "name": name.strip(), "waypoint": None if wp.startswith("(") else wp,
                    "next_utc": nxt.isoformat(), "left_before_reset": k, "interval_min": interval})
    return out


def parse_metas(page: str) -> list[dict]:
    t = text(page[page.find("<body"):])
    out = []
    for m in re.finditer(r'href="/daily-metas\?filter=([a-z0-9_:]+)"[^>]*>(.*?)</a>', page, flags=re.S):
        out.append({"id": m.group(1), "label": text(m.group(2))})
    return out or [{"raw": t[:20000]}]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("world"))
    ap.add_argument("--export", type=Path, default=Path("export/gw2app.json"))
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    fetched = dt.datetime.now(dt.timezone.utc)
    timers_page = get(SITE + "/timers", True) or ""
    metas_page = get(SITE + "/daily-metas", True) or ""
    world = {"fetched_utc": fetched.isoformat(), "timers": parse_timers(timers_page, fetched),
             "daily_metas": parse_metas(metas_page)}
    # achievements the legendary trees point at: details from the official API (allowed)
    ach_ids: set[int] = set()
    if a.export.exists():
        exp = json.loads(a.export.read_text())
        for it in exp.get("items", {}).values():
            for l in it.get("acquisition", []):
                if m := re.fullmatch(r"/achievements/(\d+)", l["href"]):
                    ach_ids.add(int(m.group(1)))
    ids = sorted(ach_ids)
    achievements = []
    for i in range(0, len(ids), 200):
        body = get(f"{API}/v2/achievements?ids={','.join(map(str, ids[i:i + 200]))}", False)
        if body:
            achievements += json.loads(body)
    world["achievements"] = achievements
    (a.out / "world.json").write_text(json.dumps(world, ensure_ascii=False, indent=1))
    (a.out / "timers.html").write_text(timers_page)        # raw copies for re-parsing (test branch only)
    (a.out / "daily-metas.html").write_text(metas_page)
    print(f"timers {len(world['timers'])}, metas {len(world['daily_metas'])}, achievements {len(achievements)}")


if __name__ == "__main__":
    main()
