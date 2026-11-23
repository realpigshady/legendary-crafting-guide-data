"""Unlock facts the general walk misses (Data R1 section 9 step 5, 2026-11-23), read from the wiki.

``config/unlock_sources.json`` names them; this module

  reward_containers  an item had only by opening a box that an achievement gives: Ad Infinitum's
                     Solutions (Ad Infinitum I: Finite Result gives Proof: Finite Result, which surely
                     contains Solution: Finite Result). The walk stops at the box (no source of its own),
                     so the item had none (Data 13b). The item gets a ``reward`` source for that
                     achievement (SMW ``Gives item`` of the box), with ``via_container`` (the box) and
                     ``unlock`` (the item that starts the achievement and the NPC that gives it, from the
                     unlock item's page: Theory of the Finite Result, Kelvei). No inputs: the collection's
                     own costs come from the research layer (addon), which merges into this source.
  vendor_unlocks     a vendor that sells only after an achievement and a weekly hand-in (Myung-Hee: The
                     Jade Vault, then the four Jade statuettes each week). The hand-in items come from the
                     vendor's SMW requirement text, are checked against the achievement's Item bits in the
                     official API, and are walked like recipe ingredients (Trading Post, meta events).
                     Every vendor source of that NPC gets ``unlock`` {achievement_id, achievement_name,
                     wiki_page, items, items_per}; the items are not inputs of the price (one hand-in
                     opens the vendor for the week, however many are bought).

``instance_entrances`` (same config) is read by vendor_waypoints.py.

Nothing is changed when the wiki has no answer: the build warns and the node keeps its sources.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import wiki_pack as wp
import wiki_sources as wsrc

CONFIG = json.loads((Path(__file__).resolve().parent.parent / "config" / "unlock_sources.json").read_text(encoding="utf-8"))
GW2_API = "https://api.guildwars2.com"

_TALK = re.compile(r"Talking to \[\[([^\]|]+)(?:\|[^\]]*)?\]\]", re.I)
_AFTER = re.compile(r"after completing \[\[(?:[^\]|]+\|)?([^\]|]+)\]\]", re.I)
_LOCATION = re.compile(r"^\s*\|\s*location\s*=\s*([^\n|]+)", re.I | re.M)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def unlock_of(store, item: str) -> tuple[dict | None, str | None]:
    """{item, item_id, npc, locations, after_achievement?} from the unlock item's page and its NPC's page."""
    pages = store.pages([item])
    page = pages.get(item)
    if page is None:
        return None, f"unlock sources: no wiki page for {item}"
    m = _TALK.search(page.text)
    if not m:
        return None, f"unlock sources: {item}'s page names no NPC ('Talking to [[...]]')"
    npc = m.group(1).strip()
    out = {"item": page.title, "item_id": wp.infobox_id(page.text), "npc": npc, "locations": []}
    a = _AFTER.search(page.text)
    if a:
        out["after_achievement"] = a.group(1).strip()
    npage = store.pages([npc]).get(npc)
    if npage is not None:
        out["npc"] = npage.title
        loc = _LOCATION.search(npage.text)
        if loc:
            out["locations"] = [x.strip() for x in loc.group(1).split(",") if x.strip()]
    return out, None


def _guaranteed(rows: list[dict], box: str) -> dict | None:
    for r in rows:
        if (r.get("Contained in") or [r.get("_subject", "").split("#")[0]])[0] != box:
            continue
        chance = str((r.get("Has contained item chance") or [""])[0]).lower()
        if chance == "guaranteed" and not wsrc._true(r.get("Is historical")):
            return r
    return None


def apply_reward_containers(nodes: dict[str, dict], store, smw) -> tuple[dict, list[str], list[str]]:
    """Adds the achievement reward to each configured item. Returns (stats, warnings, items given a source)."""
    warn: list[str] = []
    fixed: list[str] = []
    stats = {"items": 0, "sources_added": 0}
    by_name = {n["name"]: n for n in nodes.values()}
    items = {k: v for k, v in CONFIG.get("reward_containers", {}).get("items", {}).items()}
    for name, cfg in items.items():
        node = by_name.get(name)
        if node is None:
            continue   # not in any tree this build
        box = cfg["container"]
        row = _guaranteed(smw.get("contained", [name]).get(name, []), box)
        if row is None:
            warn.append(f"unlock sources: the wiki has no guaranteed '{box}' contents row for {name}: left as it was")
            continue
        rewards = wsrc.achievement_sources(box, smw.get("achievement", [box]).get(box, []))
        if not rewards:
            warn.append(f"unlock sources: no achievement gives {box} (SMW Gives item): {name} left as it was")
            continue
        unlock, w = unlock_of(store, cfg["unlock_item"]) if cfg.get("unlock_item") else (None, None)
        if w:
            warn.append(w)
        qty = wsrc._int(row.get("Has contained item quantity")) or 1
        stats["items"] += 1
        for r in rewards:
            if any(s["kind"] == "reward" and s.get("achievement_id") == r["achievement_id"] for s in node["sources"]):
                continue
            src = dict(r)
            src["via_container"] = box
            if qty * src.get("output_count", 1) > 1:
                src["output_count"] = qty * src.get("output_count", 1)
            if unlock:
                src["unlock"] = unlock
            node["sources"] = [s for s in node["sources"] if s["kind"] != "other"] + [src]
            stats["sources_added"] += 1
        if any(s["kind"] != "other" for s in node["sources"]):
            fixed.append(name)
    return stats, warn, fixed


def _requirement_items(text: str) -> list[str]:
    """'bringing her all four statuettes for the week: A, B, C, D' -> [A, B, C, D]."""
    if ":" not in text:
        return []
    return [x.strip(" .") for x in text.split(":", 1)[1].split(",") if x.strip(" .")]


def apply_vendor_unlocks(nodes: dict[str, dict], acts: dict[str, dict], random_seen: dict, get_json, store, smw,
                         currencies: dict[str, int], untradeable, expand: set[str]) -> tuple[dict, list[str]]:
    warn: list[str] = []
    stats: dict = {}
    for cfg in CONFIG.get("vendor_unlocks", {}).get("vendors", []):
        npc = cfg["npc"]
        st = stats[npc] = {"items": 0, "nodes_added": 0, "sources_changed": 0}
        srcs = [s for n in nodes.values() for s in n["sources"]
                if s["kind"] == "vendor" and any(v.get("npc") == npc for v in s.get("vendors") or [])]
        if not srcs:
            continue   # nothing in the pack is bought from this vendor
        reqs = [r for s in srcs for v in s.get("vendors") or [] if v.get("npc") == npc for r in v.get("requires") or []]
        items = next((x for x in (_requirement_items(r) for r in reqs) if x), [])
        if not items:
            warn.append(f"unlock sources: {npc}'s vendor rows name no hand-in items ({reqs}): no unlock added")
            continue
        try:
            w = wp.walk(store, items, currencies, untradeable, expand, sources=smw)
        except (wp.WikiDown, wsrc.WikiSourcesDown) as e:
            warn.append(f"unlock sources: {npc}'s hand-in items not walked ({e}): no unlock added")
            continue
        wnames = {n["name"]: n for n in w.nodes.values()}
        missing = [i for i in items if i not in wnames or all(s["kind"] == "other" for s in wnames[i]["sources"])]
        if missing:
            warn.append(f"unlock sources: {npc}: no page or no source for {missing}: no unlock added")
            continue
        # the achievement's Item bits (official API) must hold every hand-in item
        try:
            ach = next((a for a in get_json(f"{GW2_API}/v2/achievements?ids={cfg['achievement_id']}") or []
                        if isinstance(a, dict) and a.get("id") == cfg["achievement_id"]), None)
        except Exception as e:  # noqa: BLE001 - API down and nothing stored: the wiki list alone
            ach = None
            warn.append(f"unlock sources: {cfg['achievement_name']} not read from the API ({e}): wiki items used")
        if ach:
            bits = {b.get("id") for b in ach.get("bits", []) if b.get("type") == "Item"}
            off = [i for i in items if wnames[i].get("item_id") not in bits]
            if off:
                warn.append(f"unlock sources: {cfg['achievement_name']}'s API bits lack {off}: wiki items used")
        added = 0
        for nid, n in w.nodes.items():
            if nid not in nodes:
                nodes[nid] = n
                added += 1
        for aid, a in w.activities.items():
            acts.setdefault(aid, a)
        for k, v in w.random_seen.items():
            random_seen.setdefault(k, v)
        st["nodes_added"] = added
        st["items"] = len(items)
        unlock = {"achievement_id": cfg["achievement_id"], "achievement_name": cfg["achievement_name"],
                  "wiki_page": cfg["achievement_name"], "items": [{"node_id": wnames[i]["id"], "count": 1} for i in items],
                  "items_per": cfg.get("items_per", "once")}
        for s in srcs:
            s["unlock"] = json.loads(json.dumps(unlock))
            st["sources_changed"] += 1
    return stats, warn
