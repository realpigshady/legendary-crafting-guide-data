"""Links between the pack's activities and a short description per activity (owner feedback
2026-11-10, addon docs/owner/2026-11-10-v2-feedback.md F10/F11).

  cap_groups    rewards several different activities share (config/shared_caps.json): the runs
                of every member together count against one cap, e.g. the three Ley-Line
                Anomalies give their reward once a day in all. Written on each member as
                [{id, name, max_runs_per_day | max_runs_per_week}]; the addon's [fast] overlay
                uses the same ids for its copies of the same activities.
  description   1-2 lines for the expanded Overview row, built from what the pack already says
                (kind, place, run length, schedule, caps). The "More info" link is the
                activity's guidance.wiki_url.

Nothing here reads the wiki: it only rearranges the pack's own fields and the owner's config.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

CONFIG = json.loads((Path(__file__).resolve().parent.parent / "config" / "shared_caps.json").read_text(encoding="utf-8"))


def apply_caps(final: dict[str, dict]) -> tuple[int, list[str]]:
    """-> (activities given a cap group, warnings for config members not in the pack)."""
    n, warn = 0, []
    for g in CONFIG["groups"]:
        d = {"id": g["id"], "name": g["name"]}
        for k in ("max_runs_per_day", "max_runs_per_week"):
            if g.get(k):
                d[k] = g[k]
        members = [m for m in g["members"] if m in final]
        for m in g["members"]:
            if m not in final:
                warn.append(f"shared caps: {g['id']} member {m} is not in the pack")
        if len(members) < 2:
            continue
        for m in members:
            caps = final[m].setdefault("cap_groups", [])
            if not any(c["id"] == g["id"] for c in caps):
                caps.append(d)
                n += 1
    return n, warn


def _text(a: dict) -> str:
    g = a.get("guidance") or {}
    t = (g.get("text") or "").strip()
    mins = a.get("est_minutes")
    aid = a["id"]
    if aid.startswith("evt:"):
        m = re.match(r"(.*?): (.*?) \((UTC [^;)]*)", t)
        where, when, utc = (m.group(1), m.group(2), m.group(3)) if m else ("", "", "")
        s = "Timed event" + (f" ({where})" if where else "")
        s += f", about {mins} min" if mins else ""
        s += f": {when} ({utc})." if when else "."
        return s + " Its reward counts once a day."
    if aid.startswith("loc:"):
        kind = t.split(" (")[0]
        return (kind[0].upper() + kind[1:] + "." if kind else a["name"] + ".") + " No run length on the wiki: your own runs (DRF) time it."
    if aid.startswith("rt:"):
        return f"{a.get('mode', '').upper()} reward track" + (" (once per account)." if a.get("repeatable") is False else ".")
    return t if t.endswith(".") or not t else t + "."


def describe(final: dict[str, dict]) -> int:
    """Adds "description" to every activity that has none; -> how many."""
    names = {i: a["name"] for i, a in final.items()}
    by_group: dict[str, list[str]] = {}
    for i, a in final.items():
        for c in a.get("cap_groups", []):
            by_group.setdefault(c["id"], []).append(i)
    n = 0
    for i, a in final.items():
        if a.get("description"):
            continue
        s = _text(a)
        for c in a.get("cap_groups", []):
            others = [names[o] for o in by_group.get(c["id"], []) if o != i]
            if others:
                per = "a day" if c.get("max_runs_per_day") else "a week"
                s += f" Reward shared with {', '.join(others[:3])}{'…' if len(others) > 3 else ''} (once {per} in all)."
        if s:
            a["description"] = s
            n += 1
    return n
