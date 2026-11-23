"""The match table between wiki activities and the addon's [fast] overlay (Data 3b step 2, owner
2026-11-23 after reviewing the addon's docs/research/DUPLICATE_ACTIVITIES.md).

A wiki `evt:` / `loc:` / `li:` record and a [fast] record can be the same activity (Janthir
Syntri: Of Mists and Monsters is both `evt:jw-js:of_mists_and_monsters` and the [fast] meta).
The addon appends every overlay activity next to the pack's, so without a link the planner can
run both on one play day and count the daily reward twice. ``config/activity_matches.json``
lists each such copy by id (no [fast] data, as ``config/castora.json`` does); this module writes
on each listed wiki record:

  same_as      the [fast] record's id (what the addon may merge it into later)
  group        the [fast] record's variant group (its id when it has none): engine v2 plans at
               most one activity of a group per play day
  cap_groups   with `weekly_cap`: the [fast] raid's weekly-chest cap group, one run a week, so
               the weekly chest counts once a week whichever record the plan picks

With the overlay on, the addon merges each listed wiki record into its [fast] record and drops it
(Data 3, MergeOverlay in src/plan/FastOverlay.cpp): start times, waypoint, map ids, wiki link, festival
and the yields the [fast] loot lacks move over; `group` / `cap_groups` then matter only when that
[fast] record is not added. With the overlay off the wiki record stays as written here.

Nothing here reads the wiki.
"""
from __future__ import annotations

import json
from pathlib import Path

CONFIG = json.loads((Path(__file__).resolve().parent.parent / "config" / "activity_matches.json").read_text(encoding="utf-8"))
WEEKLY_CAP_NAME = "Weekly chest shared by these runs"


def apply(final: dict[str, dict]) -> tuple[dict, list[str]]:
    """-> (stats, warnings). Idempotent."""
    warn: list[str] = []
    linked = weekly = 0
    for m in CONFIG["matches"]:
        a = final.get(m["wiki"])
        if a is None:
            warn.append(f"activity matches: {m['wiki']} is not in the pack")
            continue
        group = m.get("group", m["fast"])
        old = a.get("group")
        if old and old != group:
            warn.append(f"activity matches: {m['wiki']} had group {old}, now {group} (the [fast] record's)")
        a["same_as"] = m["fast"]
        a["group"] = group
        linked += 1
        if m.get("weekly_cap"):
            caps = a.setdefault("cap_groups", [])
            if not any(c["id"] == m["weekly_cap"] for c in caps):
                caps.append({"id": m["weekly_cap"], "name": WEEKLY_CAP_NAME, "max_runs_per_week": 1})
            weekly += 1
    return {"linked": linked, "weekly_caps": weekly, "listed": len(CONFIG["matches"])}, warn
