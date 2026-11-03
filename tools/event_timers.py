"""Run length, start cycle and waypoint for timed metas, from the wiki's event timer
(Widget:Event timer/data.json, the data behind https://wiki.guildwars2.com/wiki/Event_timers).

Each map in the data has phases (segments: name, wiki link, chat link) and a schedule:
'partial' from 00:00 UTC, then 'pattern' repeating. config/event_timers.json says which
phases are one run of an activity; their minutes are added.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

PAGE = "Widget:Event timer/data.json"
CONFIG = json.loads((Path(__file__).resolve().parent.parent / "config" / "event_timers.json").read_text(encoding="utf-8"))


def parse(text: str) -> dict:
    return json.loads(text[text.find("{"):text.rfind("}") + 1])["events"]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", re.sub(r"^the\s+", "", s.lower()))


def timer_for(events: dict, key: str) -> dict | None:
    """{minutes, every, starts (UTC minute of day of each run), waypoint, region, map} for one map."""
    m = events.get(key)
    conf = CONFIG["events"].get(key)
    if not m or not conf:
        return None
    pattern = m["sequences"].get("pattern", [])
    every = sum(x["d"] for x in pattern)
    if not every:
        return None
    want = set(conf["segments"])
    seg = m["segments"]
    minutes = sum(x["d"] for x in pattern if seg[str(x["r"])].get("name") in want)
    if not minutes:
        return None
    # a run starts where a block of wanted phases begins in the repeating pattern (the
    # 'partial' prefix only shifts it); one start per cycle, as UTC minutes of the day
    offset = sum(x["d"] for x in m["sequences"].get("partial", []))
    blocks, t, prev = [], 0, False
    for x in pattern:
        hit = seg[str(x["r"])].get("name") in want
        if hit and not prev:
            blocks.append(t)
        prev, t = hit, t + x["d"]
    if prev and blocks and blocks[0] == 0 and seg[str(pattern[0]["r"])].get("name") in want and len(blocks) > 1:
        blocks.pop(0)   # the block wraps round the cycle end: it starts at its later position
    starts = sorted({(offset + b + k * every) % 1440 for b in blocks for k in range(1440 // every + 1)})
    chat = next((seg[str(x["r"])].get("chatlink") for x in pattern
                 if seg[str(x["r"])].get("name") in want and seg[str(x["r"])].get("chatlink")), None)
    return {"minutes": minutes, "every": every, "starts": starts, "waypoint": chat,
            "region": m.get("category", ""), "map": m.get("name", "")}


def apply(activities: list[dict], events: dict) -> int:
    """Sets est_minutes / guidance on matching activities; returns how many got a time."""
    by_map = {_norm(m.get("name", "")): k for k, m in events.items()}
    done = 0
    for a in activities:
        key = CONFIG["activities"].get(a["id"])
        if not key and a["id"].startswith("wiki:"):
            key = by_map.get(_norm(a["name"]))
        t = timer_for(events, key) if key else None
        if not t:
            continue
        a["est_minutes"] = t["minutes"]
        g = a.setdefault("guidance", {})
        hhmm = ", ".join(f"{s // 60:02d}:{s % 60:02d}" for s in t["starts"][:3])
        g["text"] = f"{t['region']}: every {t['every']} min (UTC {hhmm}{'...' if len(t['starts']) > 3 else ''}; wiki event timer)"
        if t["waypoint"]:
            g["waypoint_chat_code"] = t["waypoint"]
        a["starts_utc"] = t["starts"]
        done += 1
    return done


def load(store) -> dict | None:
    """The timer data through a wiki_pack.PageStore (live, or its stored copy when the wiki is down)."""
    page = store.pages([PAGE]).get(PAGE)
    return parse(page.text) if page else None


def load_stored(snapshot: Path) -> dict | None:
    """The stored copy only (test-pack builds, no wiki access)."""
    import wiki_pack as wp

    def offline(_url):
        raise OSError("offline")
    store = wp.PageStore(snapshot, offline)
    store.down = True
    try:
        return load(store)
    except wp.WikiDown:
        return None
