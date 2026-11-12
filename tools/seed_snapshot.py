#!/usr/bin/env python3
"""Store probe replies (probe/out) in snapshot/ in the builder's own format, so an offline
build has pages the live build has not fetched yet (Data 4o: the wiki is reachable only from
GitHub Actions, and the probe job is how pages get there).

  pages:  every page of a prop=revisions probe reply -> snapshot/wiki-pages (PageStore format,
          same title and revision id: the next live build sees the revision unchanged and keeps it)
  ask:    an SMW ask probe whose query is exactly the builder's (wiki_sources.QUERIES[kind]) ->
          snapshot/wiki-sources rows per item (SourceStore format)

    python3 tools/seed_snapshot.py pages voe_metas voe_chests
    python3 tools/seed_snapshot.py ask rewarded voe_rewarded_exact "Aether-Rich Sap" "Antiquated Ducat"
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wiki_pack as wp  # noqa: E402
import wiki_sources as wsrc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SNAP = ROOT / "snapshot"


def reply(name: str) -> dict:
    return json.loads((ROOT / "probe" / "out" / f"{name}.json").read_text(encoding="utf-8"))


def pages(names: list[str]) -> None:
    store = wp.PageStore(SNAP, lambda url: (_ for _ in ()).throw(RuntimeError("offline")))
    for n in names:
        q = reply(n)["reply"].get("query", {})
        if q.get("redirects") or q.get("normalized"):
            sys.exit(f"{n}: has redirects / normalized titles; fetch through the build instead")
        for p in q.get("pages", []):
            if p.get("missing") or not p.get("revisions"):
                print(f"skip {p['title']} (missing)")
                continue
            rv = p["revisions"][0]
            text = rv.get("slots", {}).get("main", {}).get("content", rv.get("content", ""))
            store._save(wp.Page(p["title"], rv["revid"], text))
            print(f"page {p['title']} rev {rv['revid']}")


def ask(kind: str, name: str, items: list[str]) -> None:
    r = reply(name)
    want = wsrc.QUERIES[kind](items) + f"|limit={wsrc.LIMIT}|offset=0"
    if r["params"].get("query") != want:
        sys.exit(f"{name}: query is not the builder's {kind} query:\n  {r['params'].get('query')}\n  {want}")
    if r["reply"].get("query-continue-offset"):
        sys.exit(f"{name}: reply is paged; widen the probe")
    store = wsrc.SourceStore(SNAP, lambda params: r["reply"])
    got = store.get(kind, items)
    for i in items:
        print(f"{kind} {i}: {len(got.get(i, []))} rows")


if __name__ == "__main__":
    if sys.argv[1] == "pages":
        pages(sys.argv[2:])
    else:
        ask(sys.argv[2], sys.argv[3], sys.argv[4:])
