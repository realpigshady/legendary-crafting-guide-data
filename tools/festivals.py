"""Festival tags and windows (Lunar New Year, Super Adventure Festival, Dragon Bash,
Festival of the Four Winds, Halloween, Wintersday).

  windows   Each festival's wiki page lists every release under '== Releases ==' as
            "* [[<release page>]] <small>(YYYY-MM-DD — YYYY-MM-DD)</small>", the current
            year's included once ArenaNet has announced it. All of them go into the pack
            (pack["festivals"]); the addon plans a festival source only inside a window.
  tags      A page is festival content when it is in the festival's category (the wiki
            files festival pages under a category named like the festival page) and in
            Category:Temporary content. Applied to:
              activities  the activity's own page (places: the categories stored with the
                          location query; event timer phases: the phase's page, else the
                          timer map, which for Dragon Bash and Halloween is the festival
                          itself; 'rewarded by' pages: their page). Festival reward tracks
                          have no such category: a track whose title starts with a prefix
                          in config/festivals.json (reward_track_prefixes) is tagged.
              vendors     an explicit wiki requirement "the festival X", else every listed
                          location being festival content of the same festival, else (no
                          location listed) the NPC page's festival category
            A vendor source gets "festival" only when every vendor selling it that way is
            festival-only; one year-round vendor (e.g. Lion's Arch, Hooligan's Route) keeps
            it available all year.

Nothing here is guessed: no window on the wiki = no window in the pack = the addon keeps
that festival's sources off.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path

CONFIG = json.loads((Path(__file__).resolve().parent.parent / "config" / "festivals.json").read_text(encoding="utf-8"))
FESTIVALS: list[str] = CONFIG["festivals"]
TEMPORARY = "Temporary content"
WIKI = "https://wiki.guildwars2.com/wiki/"
_CANON = {f.lower(): f for f in FESTIVALS}
TRACK_PREFIXES: dict[str, list[str]] = CONFIG.get("reward_track_prefixes", {})

_RELEASE = re.compile(
    r"^\*\s*\[\[([^\]|]+)(?:\|[^\]]*)?\]\][^\n]*?<small>\(\s*(\d{4}-\d{2}-\d{2})\s*[—–-]+\s*(\d{4}-\d{2}-\d{2})?\s*\)</small>",
    re.M)
_REQ = re.compile(r"^the festival (.+)$", re.I)


def canonical(name: str) -> str | None:
    """Festival name as the pack writes it, matched ignoring case ('Festival of the four Winds')."""
    return _CANON.get(str(name).strip().lower())


def parse_windows(text: str) -> list[dict]:
    """[{release, start, end}] from a festival page's '== Releases ==' section (end None = not announced)."""
    m = re.search(r"^==\s*Releases\s*==\s*$", text, re.M)
    if not m:
        return []
    body = text[m.end():]
    nxt = re.search(r"^==[^=].*==\s*$", body, re.M)
    body = body[:nxt.start()] if nxt else body
    out = [{"release": r.strip(), "start": s, "end": e or None} for r, s, e in _RELEASE.findall(body)]
    return sorted(out, key=lambda w: w["start"])


def windows(store) -> tuple[list[dict], list[str]]:
    """pack["festivals"] from the festival pages (a wiki_pack.PageStore), plus warnings."""
    import wiki_pack as wp
    warn = []
    try:
        pages = store.pages(FESTIVALS)
    except wp.WikiDown as e:
        pages = {}
        warn.append(f"festivals: wiki pages unavailable ({e}); no windows, so every festival source stays off")
    out = []
    for f in FESTIVALS:
        page = pages.get(f)
        ws = parse_windows(page.text) if page else []
        if page and not ws:
            warn.append(f"festivals: {f}: no releases found on its wiki page")
        out.append({"name": f, "wiki_url": WIKI + urllib.parse.quote(f.replace(" ", "_")), "windows": ws})
    return out, warn


def festival_of(cats, *, need_temporary: bool = True) -> str | None:
    """The one festival a page's categories put it in (None when none, or more than one)."""
    cs = {str(c).split(":", 1)[-1] for c in (cats or [])}
    hits = [f for f in FESTIVALS if f in cs]
    if len(hits) != 1 or (need_temporary and TEMPORARY not in cs):
        return None
    return hits[0]


def track_festival(a: dict) -> str | None:
    """A reward track (rt:) named after a festival: festival tracks have no festival category on the wiki."""
    if not a["id"].startswith("rt:"):
        return None
    for f, prefixes in TRACK_PREFIXES.items():
        if any(a["name"].lower().startswith(x.lower() + " ") for x in prefixes):
            return f
    return None


def _page_of(a: dict) -> str:
    if a.get("_page"):
        return a["_page"]
    return re.sub(r" \((?:PvP|WvW)\)$", "", a["name"])


def pages_to_ask(activities: list[dict], nodes: list[dict]) -> list[str]:
    """Every page whose categories the tagging reads (places carry theirs already)."""
    want = set()
    for a in activities:
        if "_cats" in a:
            continue
        want.add(_page_of(a))
        if a.get("_map"):
            want.add(a["_map"])
    for n in nodes:
        for s in n["sources"]:
            for v in s.get("vendors") or []:
                want.update(v.get("locations") or [])
                want.add(v["npc"])
    return sorted(w for w in want if w)


def tag(activities: list[dict], nodes: list[dict], cats_of) -> dict:
    """Sets "festival" on activities, vendor entries and vendor sources.
    ``cats_of(pages) -> {page: [rows]}`` (wiki_sources pagecats). Returns stats."""
    rows = cats_of(pages_to_ask(activities, nodes))
    cats: dict[str, list] = {}
    for page, rws in rows.items():
        cats[page] = [c for r in rws for c in (r.get("Category") or [])]

    def page_fest(page: str) -> str | None:
        return canonical(page) or festival_of(cats.get(page))

    stats = {f: {"activities": 0, "vendor_sources": 0, "vendors": 0} for f in FESTIVALS}
    unknown: dict[str, int] = {}
    for a in activities:
        if "_cats" in a:
            f = festival_of(a["_cats"])
        else:
            f = (page_fest(_page_of(a)) or (page_fest(a["_map"]) if a.get("_map") else None)
                 or track_festival(a))
        if f:
            a["festival"] = f
            stats[f]["activities"] += 1
    for n in nodes:
        for s in n["sources"]:
            vs = s.get("vendors") or []
            for v in vs:
                f = None
                for r in v.get("requires") or []:
                    m = _REQ.match(r.strip())
                    if m:
                        f = canonical(m.group(1)) or m.group(1).strip()
                        if f not in FESTIVALS:
                            unknown[f] = unknown.get(f, 0) + 1
                if not f and v.get("locations"):
                    fs = {festival_of(cats.get(loc)) for loc in v["locations"]}
                    f = fs.pop() if len(fs) == 1 else None
                if not f and not v.get("locations"):
                    f = festival_of(cats.get(v["npc"]), need_temporary=False)
                if f:
                    v["festival"] = f
                    if f in stats:
                        stats[f]["vendors"] += 1
            fs = {v.get("festival") for v in vs}
            if vs and len(fs) == 1 and None not in fs:
                s["festival"] = fs.pop()
                if s["festival"] in stats:
                    stats[s["festival"]]["vendor_sources"] += 1
    return {"by_festival": stats, "unknown_festivals": unknown, "pages_asked": len(rows)}
