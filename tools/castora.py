"""Visions of Eternity (Castora) daily currency sources, from the wiki (Data 4o (1), wiki-fact part).

Wages of Stars needs 2,500+ each of Raw Enchanting Stone and Chromatic Sap and hundreds of
Shadowstone Fragment / Research Data; the pack had two once-a-day sources per currency with the
wrong amounts (+834 days). ``config/castora.json`` names the activities and the wiki page each
number comes from; this module reads the numbers from those pages (``PageStore``, so they are
kept in snapshot/wiki-pages and an offline rebuild gives the same pack) and from the
rewarded-by rows of Aether-Rich Sap / Antiquated Ducat (``SourceStore``, snapshot/wiki-sources).

Rules (owner): guaranteed currency only (chance drops never counted), once a day per account
(F1: the day's first run, which includes the repeatable bonus chest), mastery bonuses counted
(no mastery gating: "10 with Prosperity Unending" is 10).

  metas          the four timed metas get their real guaranteed yield and map id. A Hero's Choice
                 Chest (pick one) becomes one variant per option ("<id>:hc-<item>") in the meta's
                 variant group, all options of all metas that open the chest sharing one daily
                 cap group, so the chest counts once a day. Hammerhart Rumble! and Secrets of
                 the Weald join the [fast] overlay's record of the same meta through the variant
                 group (its id), so the planner runs one of them a day (meta dedupe).
  activities     The Basilica Vaults (three ducat events + the Seer chests), owner's time.
  hearts         the seven renown hearts that pay 10 Aether-Rich Sap / Antiquated Ducats, owner's time.
  masteries      reward sources of the Castora mastery achievements get their tier amount as
                 output_count (the pack counted 1).
  exchanges      checked only: 5 ducats -> 1 stone and 5 sap -> 1 Chromatic Sap are already vendor
                 sources (no limit).
"""
from __future__ import annotations

import copy
import json
import re
import urllib.parse
from pathlib import Path

import wiki_pack as wp

CONFIG = json.loads((Path(__file__).resolve().parent.parent / "config" / "castora.json").read_text(encoding="utf-8"))
WIKI = "https://wiki.guildwars2.com/wiki/"
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_ITEM_T = re.compile(r"\{\{\s*(?:rewards item|gather)\s*\|([^{}]*)\}\}", re.IGNORECASE)
_ROW_T = re.compile(r"\{\{\s*vendor table row\s*\|([^{}]*)\}\}", re.IGNORECASE)


def _norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9]", "", s.lower())
    return s[:-1] if s.endswith("s") else s


def _qty(raw: str | None) -> float:
    """'25' -> 25, '1-2' -> 1.5 (the average of a guaranteed range); missing -> 1."""
    if not raw:
        return 1.0
    m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+))?\s*", raw)
    if not m:
        return 0.0
    lo = int(m.group(1))
    return (lo + int(m.group(2))) / 2 if m.group(2) else float(lo)


def _section(text: str) -> str:
    """The page's Rewards / Contents section (level 2), or the whole page."""
    m = re.search(r"^==\s*(Rewards|Contents)\s*==\s*$", text, re.MULTILINE)
    if not m:
        return text
    end = re.search(r"^==[^=].*?==\s*$", text[m.end():], re.MULTILINE)
    return text[m.end():m.end() + end.start()] if end else text[m.end():]


def guaranteed(text: str, item: str) -> float:
    """Sum of the guaranteed amounts of ``item`` in the page's rewards ({{rewards item}} / {{gather}}),
    with the Prosperity Unending amount where the note gives one."""
    total = 0.0
    for m in _ITEM_T.finditer(_section(_COMMENT.sub("", text))):
        parts = [p.strip() for p in m.group(1).split("|")]
        if not parts or _norm(parts[0]) != _norm(item):
            continue
        kv = {k.strip().lower(): v.strip() for k, v in (p.split("=", 1) for p in parts[1:] if "=" in p)}
        if kv.get("chance") and kv["chance"].lower() != "guaranteed":
            continue
        q = _qty(kv.get("qty"))
        notes = kv.get("notes", "")
        pu = re.search(r"(\d+)\s+with \[\[Prosperity Unending", notes)
        if pu:
            q = float(pu.group(1))
        elif re.search(r"doubled with \[\[Prosperity Unending", notes):
            q *= 2
        total += q
    return total


def choice_options(text: str) -> dict[str, float]:
    """Pick-one container page ({{vendor table row|quantity=N|item=X}}): normalised item -> N."""
    out = {}
    for m in _ROW_T.finditer(_COMMENT.sub("", text)):
        kv = {k.strip().lower(): v.strip() for k, v in (p.split("=", 1) for p in m.group(1).split("|") if "=" in p)}
        if kv.get("item"):
            out[_norm(kv["item"])] = _qty(kv.get("quantity"))
    return out


def tier_rewards(text: str) -> dict[tuple[int, str], int]:
    """Achievement table rows: (achievement id, normalised item) -> amount over all tiers."""
    out: dict[tuple[int, str], int] = {}
    for row in re.split(r"\{\{\s*Achievement table row", _COMMENT.sub("", text))[1:]:
        aid = re.search(r"\|\s*id\s*=\s*(\d+)", row)
        tr = re.search(r"\|\s*tier rewards\s*=([^|}]*)", row)
        if not aid or not tr:
            continue
        for line in tr.group(1).splitlines():
            bits = [b.strip() for b in line.split(";")]
            if len(bits) == 3 and bits[2].isdigit():
                k = (int(aid.group(1)), _norm(bits[1]))
                out[k] = out.get(k, 0) + int(bits[2])
    return out


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _url(page: str) -> str:
    return WIKI + urllib.parse.quote(page.replace(" ", "_"))


class _Pages:
    def __init__(self, store):
        self.store = store
        self.got: dict[str, str] = {}
        self.missing: set[str] = set()

    def load(self, titles: list[str]) -> None:
        want = [t for t in dict.fromkeys(titles) if t not in self.got]
        try:
            self.got.update({t: p.text for t, p in self.store.pages(want).items()})
        except wp.WikiDown:   # one never-stored page while the wiki is down: try the rest one by one
            for t in want:
                try:
                    self.got.update({k: p.text for k, p in self.store.pages([t]).items()})
                except wp.WikiDown:
                    pass
        self.missing |= {t for t in want if t not in self.got}

    def text(self, title: str) -> str | None:
        return self.got.get(title)


def _area_waypoint(maps: list[dict], map_name: str, area: str | None) -> tuple[int | None, str | None, str | None]:
    """(map id, waypoint chat code, waypoint name) nearest the named sector / waypoint (else the map centre)."""
    m = next((x for x in maps if x.get("name") == map_name), None)
    if not m:
        return None, None, None
    xy = None
    if area:
        xy = next(((s[1], s[2]) for s in m.get("sectors", []) if s[0] == area), None) or \
             next(((w[1], w[2]) for w in m.get("waypoints", []) if w[0].startswith(area)), None)
    xy = xy or tuple(m["centre"])
    wps = m.get("waypoints") or []
    if not wps:
        return m["id"], None, None
    w = min(wps, key=lambda w: ((w[1] - xy[0]) ** 2 + (w[2] - xy[1]) ** 2, w[3]))
    return m["id"], w[3], w[0]


def apply(final: dict[str, dict], nodes: dict[str, dict], store, smw, maps: list[dict]) -> tuple[dict, list[str]]:
    """Adds / corrects the Castora activities in ``final`` and their node sources. -> (stats, warnings)."""
    warn: list[str] = []
    stats = {"metas": 0, "variants": 0, "activities": 0, "hearts": 0, "mastery_amounts": 0, "pages_missing": 0}
    by_name = {_norm(n["name"]): n for n in nodes.values()}
    pages = _Pages(store)
    titles = [f["page"] for m in CONFIG["metas"].values() for f in m["facts"]]
    titles += [h["page"] for h in CONFIG["heros_choice"].values()]
    titles += [a["page"] for a in CONFIG["activities"]] + [f["page"] for a in CONFIG["activities"] for f in a["facts"]]
    titles += CONFIG["mastery_pages"] + [e["page"] for e in CONFIG["exchanges"]]
    pages.load(titles)
    stats["pages_missing"] = len(pages.missing)
    for t in sorted(pages.missing):
        warn.append(f"castora: wiki page '{t}' unavailable: its config amounts are used")

    def fact(f: dict, item: str | None = None) -> float:
        text = pages.text(f["page"])
        want = float(f["expect"])
        if text is None:
            return want
        got = guaranteed(text, item or f["item"]) * f.get("chests", 1)
        if got <= 0:
            warn.append(f"castora: no guaranteed {f['item']} on '{f['page']}': config amount {want:g} used")
            return want
        if abs(got - want) > 1e-9:
            warn.append(f"castora: '{f['page']}' now gives {got:g} {f['item']} (config said {want:g}): page used")
        return got

    def node_of(item: str) -> dict | None:
        n = by_name.get(_norm(item))
        if n is None:
            warn.append(f"castora: no pack node for '{item}'")
        return n

    def yields(pairs: list[tuple[str, float]]) -> list[dict]:
        out: dict[str, float] = {}
        for item, c in pairs:
            n = node_of(item)
            if n is not None and c > 0:
                out[n["id"]] = out.get(n["id"], 0) + c
        return [{"node_id": k, "count": round(v, 4)} for k, v in out.items()]

    def link(a: dict) -> None:
        for y in a["yields"]:
            srcs = nodes[y["node_id"]]["sources"]
            if not any(x.get("activity_id") == a["id"] for x in srcs):
                srcs[:] = [x for x in srcs if x["kind"] != "other"] + [{"kind": "activity", "inputs": [], "activity_id": a["id"]}]

    # Hero's Choice options, read once per chest
    hc: dict[str, list[tuple[str, float]]] = {}
    for key, h in CONFIG["heros_choice"].items():
        text = pages.text(h["page"])
        opts = choice_options(text) if text else {}
        hc[key] = []
        for o in h["options"]:
            got = opts.get(_norm(o["item"]))
            if text is not None and got is None:
                warn.append(f"castora: '{h['page']}' has no {o['item']} option: config amount used")
            elif got is not None and abs(got - o["expect"]) > 1e-9:
                warn.append(f"castora: '{h['page']}' now offers {got:g} {o['item']} (config said {o['expect']:g}): page used")
            hc[key].append((o["item"], got if got is not None else float(o["expect"])))

    # the timed metas: real yields, map id, variant group; one variant per Hero's Choice option
    for mid, m in CONFIG["metas"].items():
        base = final.get(mid)
        if base is None:
            warn.append(f"castora: meta {mid} is not in the pack (event timer?)")
            continue
        map_id, _, _ = _area_waypoint(maps, m["map"], None)
        own = [(f["item"], fact(f)) for f in m["facts"]]
        base["yields"] = yields(own)
        if map_id:
            base["map_ids"] = [map_id]
        base["group"] = m.get("group", mid)
        g = base.setdefault("guidance", {})
        g["text"] = (g.get("text", "").rstrip(".") + "; guaranteed once a day: "
                     + ", ".join(f"{c:g} {i}" for i, c in own) + " (wiki)").lstrip("; ")
        base.pop("description", None)
        link(base)
        stats["metas"] += 1
        h = CONFIG["heros_choice"][m["heros_choice"]]
        cap = {"id": h["cap_group"], "name": h["cap_name"], "max_runs_per_day": 1}
        for item, count in hc[m["heros_choice"]]:
            v = copy.deepcopy(base)
            v["id"] = f"{mid}:hc-{_slug(item)}"
            v["name"] = f"{base['name']} + Hero's Choice: {count:g} {item}"
            v["yields"] = yields(own + [(item, count)])
            v["cap_groups"] = [c for c in v.get("cap_groups", []) if c["id"] != cap["id"]] + [cap]
            v["guidance"]["text"] = g["text"] + f"; take {count:g} {item} from the {h['page']} (once a day per account)"
            final[v["id"]] = v
            link(v)
            stats["variants"] += 1

    # untimed activities (owner's time, wiki rewards)
    for a in CONFIG["activities"]:
        map_id, wp_code, wp_name = _area_waypoint(maps, a["map"], a.get("area"))
        act = {"id": a["id"], "name": a["name"], "mode": "pve", "expansion": "voe", "est_minutes": a["est_minutes"],
               "max_runs_per_day": 1, "yields": yields([(f["item"], fact(f)) for f in a["facts"]]),
               "guidance": {"text": a["text"] + f" ({a['est_minutes']} min: {a['time_source']})", "wiki_url": _url(a["page"])}}
        if map_id:
            act["map_ids"] = [map_id]
        if wp_code:
            act["guidance"]["waypoint_chat_code"], act["guidance"]["waypoint_name"] = wp_code, wp_name
        final[act["id"]] = act
        link(act)
        stats["activities"] += 1

    # renown hearts: the rewarded-by rows of the two map currencies
    hearts = CONFIG["hearts"]
    try:
        rows = smw.get("rewarded", sorted({h["item"] for h in hearts["list"]}))
    except Exception as e:  # noqa: BLE001 - wiki down and never stored: config amounts
        warn.append(f"castora: rewarded-by rows unavailable ({e}): heart amounts from config")
        rows = {}
    for h in hearts["list"]:
        got = None
        for r in rows.get(h["item"], []):
            by = (r.get("Rewarded by") or [r.get("_subject", "").split("#")[0]])[0]
            chance = str((r.get("Has rewarded item chance") or [""])[0]).lower()
            if _norm(by) == _norm(h["event"]) and chance == "guaranteed":
                got = (got or 0) + _qty(str((r.get("Has rewarded item quantity") or ["1"])[0]))
        if got is None:
            if rows:
                warn.append(f"castora: no guaranteed {h['item']} from heart '{h['event']}': config amount used")
            got = float(h["expect"])
        elif abs(got - h["expect"]) > 1e-9:
            warn.append(f"castora: heart '{h['event']}' now gives {got:g} {h['item']} (config said {h['expect']:g})")
        map_id, wp_code, wp_name = _area_waypoint(maps, h["map"], h["area"])
        act = {"id": f"voe:heart:{_slug(h['area'])}", "name": f"{h['map']}: {h['area']} renown heart",
               "mode": "pve", "expansion": "voe", "est_minutes": hearts["est_minutes"], "max_runs_per_day": 1,
               "yields": yields([(h["item"], got)]),
               "guidance": {"text": f"Renown heart '{h['event']}' ({h['area']}, {h['map']}): {got:g} {h['item']} once a day per account "
                                    f"({hearts['est_minutes']} min: {hearts['time_source']})", "wiki_url": _url(h["event"])}}
        if map_id:
            act["map_ids"] = [map_id]
        if wp_code:
            act["guidance"]["waypoint_chat_code"], act["guidance"]["waypoint_name"] = wp_code, wp_name
        final[act["id"]] = act
        link(act)
        stats["hearts"] += 1

    # mastery amounts on the reward sources (the pack counted 1)
    amounts: dict[tuple[int, str], int] = {}
    for t in CONFIG["mastery_pages"]:
        if pages.text(t):
            amounts.update(tier_rewards(pages.text(t)))
    for n in nodes.values():
        for s in n["sources"]:
            if s["kind"] == "reward" and s.get("achievement_id"):
                q = amounts.get((s["achievement_id"], _norm(n["name"])))
                if q and q > 1 and s.get("output_count") != q:
                    s["output_count"] = q
                    stats["mastery_amounts"] += 1

    # exchanges: already vendor sources; say so if one went missing
    for e in CONFIG["exchanges"]:
        n, cur = by_name.get(_norm(e["item"])), by_name.get(_norm(e["input"]))
        if n and cur and not any(s["kind"] == "vendor" and s["inputs"] == [{"node_id": cur["id"], "count": e["count"]}]
                                 for s in n["sources"]):
            warn.append(f"castora: no vendor source {e['count']} {e['input']} -> 1 {e['item']} in the pack")
    return stats, warn
