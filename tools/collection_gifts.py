"""Collection-gated gifts: the collection's items as the gift's inputs (Data 4o (2)(a)).

Gift of Shining Arms and Gift of Tenebral Armor (Wages of Stars) are the reward of a collection
(Shining Ward Weapon Collector, 17 weapons; Tenebral Ward Armor Collection, 6 armour slots), and
the vendors that sell more copies require that collection. The walk records the reward with no
inputs and the vendor with only its price, so a plan never costed the 17 weapons or 6 pieces.
``config/collection_gifts.json`` names the collections; this module

  items     reads each collection's items from the official API (``/v2/achievements`` Skin bits,
            ``/v2/skins`` names; snapshot-kept like every API answer) where the API serves the
            achievement, else the config list (9363 is not served: one Medium piece per slot,
            any weight class unlocks the slot);
  walk      walks those items (and each weapon's recipe sheet) like recipe ingredients: wiki
            recipes, SMW vendors (Alliance Field Quartermaster's price and token routes, the
            renown vendors' shard prices), achievements; a token with only chance sources has
            no source, so its vendor route drops out (only the helmet's and the chest's are
            guaranteed);
  inputs    adds one of each item to the gift's reward source (with ``step_minutes``) and to
            every vendor source that requires the collection; a crafted weapon's craft
            source also takes its recipe sheet.

A collection is applied whole or not at all: an item with no page or no source (or the wiki
down and the pages never stored) leaves the gift as it was, with a warning, so a half-costed
collection never makes the gift look cheaper than it is.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import wiki_pack as wp
import wiki_sources as wsrc

CONFIG = json.loads((Path(__file__).resolve().parent.parent / "config" / "collection_gifts.json").read_text(encoding="utf-8"))
GW2_API = "https://api.guildwars2.com"
RESEARCH_DATA = "Shadowstone Research Data"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def api_items(get_json, achievement_id: int) -> list[str] | None:
    """Skin names of the achievement's bits, in bit order; None when the API doesn't serve it."""
    ach = get_json(f"{GW2_API}/v2/achievements?ids={achievement_id}")
    ach = next((a for a in ach or [] if isinstance(a, dict) and a.get("id") == achievement_id), None)
    if not ach:
        return None
    skins = [b["id"] for b in ach.get("bits", []) if b.get("type") == "Skin" and b.get("id")]
    if not skins or len(skins) != len(ach.get("bits", [])):
        return None
    got = {s["id"]: s.get("name") for s in get_json(f"{GW2_API}/v2/skins?ids={','.join(map(str, skins))}") or [] if isinstance(s, dict)}
    names = [got.get(i) for i in skins]
    return names if all(names) else None


def _requires(src: dict, name: str) -> bool:
    return any(_norm(r) == _norm(name) for v in src.get("vendors", []) for r in v.get("requires", []))


def _check_armor(cfg: dict, item: str, node: dict, nodes: dict[str, dict], warn: list[str]) -> None:
    exp = cfg.get("per_slot_expect", {}).get(item)
    if not exp:
        return
    rd = next((k for k, n in nodes.items() if n.get("name") == RESEARCH_DATA), None)
    vend = [s for s in node["sources"] if s["kind"] == "vendor"]
    price = {(i["node_id"], i["count"]) for s in vend for i in s["inputs"]}
    full = {("currency:1", exp["gold"] * 10000), ("currency:2", exp["karma"])} | ({(rd, exp["research_data"])} if rd else set())
    if not any({(i["node_id"], i["count"]) for i in s["inputs"]} == full for s in vend):
        warn.append(f"collection gifts: {item} has no vendor price {exp['gold']} gold + {exp['research_data']} {RESEARCH_DATA} + "
                    f"{exp['karma']} Karma (wiki 2026-11-12); the walked prices are used: {sorted(price)}")


def apply(nodes: dict[str, dict], acts: dict[str, dict], random_seen: dict, get_json, store, smw,
          currencies: dict[str, int], untradeable, expand: set[str]) -> tuple[dict, list[str]]:
    """Adds the collections' item trees to ``nodes`` / ``acts`` and the items to the gifts' sources."""
    warn: list[str] = []
    stats: dict = {}
    by_name = {n["name"]: n for n in nodes.values()}
    for cfg in CONFIG["collections"]:
        key = cfg["gift"]
        st = stats[key] = {"items": 0, "item_source": "config", "nodes_added": 0, "sources_changed": 0, "recipe_sheets": 0}
        gift = by_name.get(cfg["gift"])
        if gift is None:
            warn.append(f"collection gifts: no pack node '{cfg['gift']}'")
            continue
        items = list(cfg["items"])
        if cfg.get("items_from_api"):
            try:
                got = api_items(get_json, cfg["achievement_id"])
                if got is None:
                    warn.append(f"collection gifts: the API has no Skin bits for {cfg['achievement_id']}: config items used")
            except Exception as e:  # noqa: BLE001 - API down and nothing stored: the config list
                warn.append(f"collection gifts: {cfg['achievement_name']} not read from the API ({e}): config items used")
                got = None
            if got:
                st["item_source"] = "api"
                if sorted(map(_norm, got)) != sorted(map(_norm, items)):
                    warn.append(f"collection gifts: the API's {cfg['achievement_name']} lists {got}, config {items}: API used")
                items = got
        sheets = {i: cfg["recipe_sheet"].format(item=i) for i in items} if cfg.get("recipe_sheet") else {}
        try:
            w = wp.walk(store, items + list(sheets.values()), currencies, untradeable, expand, sources=smw)
        except (wp.WikiDown, wsrc.WikiSourcesDown) as e:
            warn.append(f"collection gifts: {cfg['achievement_name']} not walked ({e}): {cfg['gift']} keeps its old sources")
            continue
        wnames = {n["name"]: n for n in w.nodes.values()}
        for t, f in store.redirects.items():
            if f in wnames:
                wnames.setdefault(t, wnames[f])
        missing = [i for i in items if i not in wnames or all(s["kind"] == "other" for s in wnames[i]["sources"])]
        if missing:
            warn.append(f"collection gifts: {cfg['achievement_name']}: no page or no source for {missing}: {cfg['gift']} keeps its old sources")
            continue
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
        item_ids = [wnames[i]["id"] for i in items]
        st["items"] = len(item_ids)
        # a crafted weapon learns its recipe first: the sheet (vendor: research data) joins the craft
        for i, sheet in sheets.items():
            sn = wnames.get(sheet)
            if sn is None or all(s["kind"] == "other" for s in sn["sources"]):
                continue
            for s in nodes[wnames[i]["id"]]["sources"]:
                if s["kind"] == "craft" and not any(x["node_id"] == sn["id"] for x in s["inputs"]):
                    s["inputs"].append({"node_id": sn["id"], "count": 1})
                    st["recipe_sheets"] += 1
        for i in items:
            _check_armor(cfg, i, nodes[wnames[i]["id"]], nodes, warn)
        ins = [{"node_id": nid, "count": 1} for nid in item_ids]
        for s in gift["sources"]:
            ach = s["kind"] == "reward" and s.get("achievement_id") == cfg["achievement_id"]
            if ach or (s["kind"] == "vendor" and _requires(s, cfg["achievement_name"])):
                s["inputs"] = [x for x in s["inputs"] if x["node_id"] not in set(item_ids)] + ins
                if ach:
                    s["step_minutes"] = CONFIG["step_minutes"]
                st["sources_changed"] += 1
        if not st["sources_changed"]:
            warn.append(f"collection gifts: {cfg['gift']} has no reward / vendor source for {cfg['achievement_name']}")
    return stats, warn
