"""Random Mystic Forge outcomes: expected output and byproducts per attempt.

Some forge recipes don't always give what they're named for. The Mystic Clover recipes give
a clover about 31% of the time and otherwise one of a list of materials; the Mystic Facet
recipe gives 1 facet, rarely 5. The wiki marks such recipes (``upper quantity``, "(random)"
in the recipe name) and records player results on a ``<item>/Drop rate`` page in
{{SDRH}} (columns) / {{SDRL}} (one line per contributor) templates.

Per item in ``config/forge_outcomes.json``:

* expected output per unit of the recipe: the item page's hidden "average" recipe
  (``base ingredients calculation = y``, e.g. quantity 0.31) when there is one, else the
  drop research (columns that are the item itself, times their quantity);
* byproducts per unit: each other research column's share of the non-item outcomes, times
  (1 - chance of the item), times its quantity from the page's "Possible drops" list; a
  column naming a group ("Lodestone") is split evenly over the items the config lists;
* every random forge source of the node gets ``expected_output`` and ``byproducts`` scaled
  by its ``output_count`` (the 10-clover recipe: same odds, ten times the amounts), plus
  ``research`` (page, attempts, where the expected output came from).

The research page is a normal wiki page (PageStore: revision-checked, kept in snapshot/).
"""
from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path

import wiki_pack as wp

CONFIG = Path(__file__).resolve().parent.parent / "config" / "forge_outcomes.json"
WIKI = "https://wiki.guildwars2.com/wiki/"


def _num(v: str) -> int:
    m = re.match(r"\s*(\d+)", v or "")
    return int(m.group(1)) if m else 0


def research_tables(wikitext: str) -> list[dict]:
    """[{section, columns: {key: link or ''}, rows: [{key: count}]}] from {{SDRH}}/{{SDRL}}."""
    text = re.sub(r"<!--.*?-->", "", wikitext, flags=re.S)   # the "add a line like this" examples
    starts = [m.start() for m in re.finditer(r"\{\{\s*SDRH\b", text)]
    out = []
    for n, st in enumerate(starts):
        chunk = text[st:starts[n + 1] if n + 1 < len(starts) else None]
        head = wp.find_templates(chunk, "SDRH")[0]
        cols = {}
        for k, v in head.items():
            m = re.fullmatch(r"s(\d+)", k)
            if m:
                cols[v.strip().lower()] = head.get(f"l{m.group(1)}", "").strip()
        secs = re.findall(r"^=+\s*(.*?)\s*=+\s*$", text[:st], re.M)
        rows = []
        for r in wp.find_templates(chunk, "SDRL"):
            row = {c: _num(r.get(c, "")) for c in cols}
            if sum(row.values()):
                rows.append(row)
        out.append({"section": secs[-1] if secs else "", "columns": cols, "rows": rows})
    return out


def possible_drops(wikitext: str) -> dict[str, float]:
    """Item name -> average quantity per outcome, from the 'Possible drops' bullet list
    ('* 2-4 tier 6 fine ...: {{item icon|A}}, {{item icon|B}}' -> A: 3, B: 3)."""
    i = wikitext.find("Possible drops")
    if i < 0:
        return {}
    out: dict[str, float] = {}
    for line in wikitext[i:].split("\n")[1:]:
        if line.startswith("=="):
            break
        m = re.match(r"\*\s*(\d+)(?:\s*-\s*(\d+))?", line)
        if not m:
            continue
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        names = re.findall(r"\{\{\s*item icon\s*\|\s*([^|}]+)", line, re.I) + re.findall(r"\[\[([^|\]]+)", line)
        for nm in names:
            out.setdefault(nm.strip(), (lo + hi) / 2)
    return out


def outcomes(item: str, cfg: dict, text: str, avg_output: float | None) -> tuple[dict | None, list[str]]:
    """{p_unit, from, attempts, items: {name: avg per unit}} for the 1x recipe, plus warnings."""
    warn = []
    tables = research_tables(text)
    if not cfg.get("pool_tables", True):
        tables = tables[:1]
    if not tables:
        return None, [f"forge outcomes: {item}: no research table on {cfg['research']}"]
    cols: dict[str, str] = {}
    counts: dict[str, int] = {}
    for t in tables:
        for k, link in t["columns"].items():
            cols.setdefault(k, link)
        for r in t["rows"]:
            for k, v in r.items():
                counts[k] = counts.get(k, 0) + v
    attempts = sum(counts.values())
    if not attempts:
        return None, [f"forge outcomes: {item}: research table has no results"]
    drops = possible_drops(text)
    self_cols = cfg.get("self_columns") or {k: drops.get(item, 1) for k, link in cols.items() if link == item}
    hits = sum(counts.get(k, 0) for k in self_cols)
    research_p = sum(counts.get(k, 0) * q for k, q in self_cols.items()) / attempts
    if avg_output:
        p_unit, src = avg_output, "wiki average recipe"
    else:
        p_unit, src = research_p, "drop research"
    p_hit = hits / attempts   # chance the outcome is the item at all
    if avg_output and self_cols:
        p_hit = avg_output / (sum(self_cols.values()) / len(self_cols))
    rest = attempts - hits
    items: dict[str, float] = {}
    for k, link in cols.items():
        if k in self_cols or not counts.get(k):
            continue
        share = counts[k] / rest * (1 - p_hit)
        names = cfg.get("split", {}).get(link) or [link]
        qty = drops.get(link)
        if qty is None:
            warn.append(f"forge outcomes: {item}: no quantity for research column '{k}' ({link}) in Possible drops")
            continue
        for nm in names:
            items[nm] = items.get(nm, 0) + share * qty / len(names)
    return {"p_unit": p_unit, "from": src, "attempts": attempts, "research_p": research_p, "items": items}, warn


def apply(nodes: dict[str, dict], store, random_seen: dict[str, list]) -> tuple[dict, list[str]]:
    """Set expected_output / byproducts / research on random forge sources. Returns (stats, warnings)."""
    cfg_all = json.loads(CONFIG.read_text(encoding="utf-8")).get("items", {}) if CONFIG.exists() else {}
    warnings: list[str] = []
    by_name = {n["name"]: n for n in nodes.values()}
    stats = {"items": {}, "no_research": [], "not_in_pack": sorted(set(random_seen) - {
        n["name"] for n in nodes.values() if any(s.get("_random") for s in n["sources"])})}
    for name, cfg in sorted(cfg_all.items()):
        node = by_name.get(name)
        if node is None:
            continue
        try:
            page = store.pages([cfg["research"]]).get(cfg["research"])
        except wp.WikiDown:
            page = None
        if page is None:
            warnings.append(f"forge outcomes: {name}: research page '{cfg['research']}' unavailable")
            continue
        out, w = outcomes(name, cfg, page.text, node.get("_avg_output"))
        warnings += w
        if not out:
            continue
        ids = {}
        try:
            pages = store.pages(sorted(out["items"]))
        except wp.WikiDown:   # some never stored and the wiki is down: take what is stored
            pages = {}
            for nm in out["items"]:
                try:
                    pages.update(store.pages([nm]))
                except wp.WikiDown:
                    pass
        for nm in out["items"]:
            pg = pages.get(nm)
            ids[nm] = wp.node_id_for(pg.title if pg else nm, wp.infobox_id(pg.text) if pg else None)
            if pg is None:
                warnings.append(f"forge outcomes: {name}: byproduct '{nm}' has no wiki page here")
        n_src = 0
        for s in node["sources"]:
            if s["kind"] != "mystic_forge" or not s.get("_random"):
                continue
            k = s.get("output_count", 1)
            s["expected_output"] = round(out["p_unit"] * k, 4)
            s["byproducts"] = [{"node_id": ids[nm], "name": nm, "count": round(v * k, 4)}
                               for nm, v in sorted(out["items"].items(), key=lambda kv: -kv[1]) if v * k >= 0.0005]
            s["research"] = {"page": WIKI + urllib.parse.quote(cfg["research"].replace(" ", "_")),
                             "attempts": out["attempts"], "expected_from": out["from"]}
            n_src += 1
        stats["items"][name] = {"sources": n_src, "attempts": out["attempts"], "expected_per_unit": round(out["p_unit"], 4),
                                "expected_from": out["from"], "research_expected_per_unit": round(out["research_p"], 4),
                                "byproducts": len(out["items"])}
    for n in nodes.values():
        n.pop("_avg_output", None)
        for s in n["sources"]:
            if s.pop("_random", None) is not None and s["kind"] == "mystic_forge" and "expected_output" not in s:
                stats["no_research"].append(n["name"])
    stats["no_research"] = sorted(set(stats["no_research"]))
    for nm in stats["no_research"]:
        warnings.append(f"forge outcomes: {nm}: random forge recipe with no research data (treated as guaranteed)")
    return stats, warnings
