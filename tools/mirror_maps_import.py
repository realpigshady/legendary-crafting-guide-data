#!/usr/bin/env python3
"""One-off importer (Data 4o (1b)): the owner's Magic Mirror route maps -> data repo.

Reads the owner's folder (addon repo ``docs/owner/2026-11-12/mirror-maps/``) and writes
  config/mirror_route_geometry.json   positions on the route images: mirrors, chests, the
                                      efficient route's stops and drawn / suggested path, the
                                      start waypoints' image positions
  assets/mirror-maps/                 the release assets: one full-sweep and one efficient image
                                      per map (same size, so one set of coordinates), the
                                      waypoint map icon

Every coordinate is in pixels on the efficient image (= the full-sweep image, resized to the
same size). Full-sweep images: Eternity's Garden and Leyspring Hollows are the owner's styled
redraws; Shipwreck Strand and Starlit Weald are the wiki's chest maps (probe job download,
``probe/out/files/mm_full_ss.jpg`` / ``mm_full_sw.jpg``), resized. A map whose full image is
missing is listed and skipped (rerun once the probe has it).

Run: python tools/mirror_maps_import.py <owner mirror-maps folder>
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
MAPS = [  # slug, name, window index in mirror-window-maps.json / efficient-routes.json, full-sweep source
    ("shipwreck-strand", "Shipwreck Strand", 0, ROOT / "probe/out/files/mm_full_ss.jpg"),
    ("starlit-weald", "Starlit Weald", 1, ROOT / "probe/out/files/mm_full_sw.jpg"),
    ("eternitys-garden", "Eternity's Garden", 2, "eternitys-garden-styled.jpg"),
    ("leyspring-hollows", "Leyspring Hollows", 3, "leyspring-hollows-styled.jpg"),
]
SIZE = {"S": "small", "M": "medium", "L": "large", "M/L": "medium_or_large"}


def _xy(p, k=1.0):
    return [round(p[0] * k), round(p[1] * k)]


def main(src: Path) -> int:
    win = json.loads((src / "mirror-window-maps.json").read_text(encoding="utf-8"))
    eff = json.loads((src / "efficient-routes.json").read_text(encoding="utf-8"))
    wps = json.loads((src / "waypoints-on-images.json").read_text(encoding="utf-8"))
    pts = {"eternitys-garden": json.loads((src / "eternitys-garden-points.json").read_text(encoding="utf-8")),
           "leyspring-hollows": json.loads((src / "leyspring-hollows-points.json").read_text(encoding="utf-8"))}
    out_dir = ROOT / "assets" / "mirror-maps"
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src / "waypoint-map-icon.png", out_dir / "waypoint-map-icon.png")
    geo: dict[str, dict] = {}
    missing = []
    for slug, name, i, full_src in MAPS:
        w, e = win[i], eff[i]
        assert w["name"] == name, (w["name"], name)
        eimg = Image.open(src / "efficient" / f"{slug}.jpg")
        size = list(eimg.size)
        assert size == [w["w"], w["h"]], (slug, size, w["w"], w["h"])
        shutil.copyfile(src / "efficient" / f"{slug}.jpg", out_dir / f"mirror-{slug}-efficient.jpg")
        fpath = full_src if isinstance(full_src, Path) else src / full_src
        full_k = None
        if fpath.exists():
            fimg = Image.open(fpath).convert("RGB")
            full_k = size[0] / fimg.size[0]
            if abs(fimg.size[1] * full_k - size[1]) > 2:
                raise SystemExit(f"{slug}: full image {fimg.size} is not the efficient image's shape {size}")
            if list(fimg.size) != size:
                fimg = fimg.resize(tuple(size), Image.LANCZOS)
            fimg.save(out_dir / f"mirror-{slug}-full.jpg", quality=85, optimize=True, progressive=True)
        else:
            missing.append(f"{slug}: {fpath}")
        # start waypoints on the image: the owner's placed list, else the first point of a drawn leg
        starts = {x["name"]: _xy((x["x"], x["y"])) for x in wps.get(str(i), [])}
        for leg in e["legs"]:
            starts.setdefault(leg["start"], _xy(leg["points"][0]))
        if name == "Leyspring Hollows":   # the owner's full sweep starts where the drawn route does
            starts.setdefault("Doyen's Adytum Waypoint", _xy(e["legs"][0]["points"][0]))
        mirrors = [{"n": n, "name": m["name"], "x": m["x"], "y": m["y"]} for n, m in enumerate(w["mirrors"], 1)]
        chests = []
        if slug in pts:   # full-sweep chest spots from the owner's redraws (others: drawn on the wiki map)
            p = pts[slug]
            k = size[0] / (2011 if slug == "eternitys-garden" else size[0])
            pm = p["mirrors"]
            pm_xy = [_xy(v, k) for v in (pm.values() if isinstance(pm, dict) else [(m["x"], m["y"]) for m in pm])]

            def nearest(xy):
                return min(range(len(mirrors)), key=lambda j: (mirrors[j]["x"] - xy[0]) ** 2 + (mirrors[j]["y"] - xy[1]) ** 2) + 1
            for c in p["chests"]:
                x, y = _xy((c["x"], c["y"]), k)
                ci = c.get("mirror")
                chests.append({"size": SIZE[c["letter"]], "x": x, "y": y,
                               "mirror": nearest(pm_xy[ci]) if ci is not None and ci < len(pm_xy) else None})
        stops = []
        for s in e["stops"]:
            st = {"n": s["n"], "kind": s["kind"], "name": s["name"], "x": s["x"], "y": s["y"]}
            if s["kind"] == "chest":
                st["size"] = SIZE[s["letter"]]
            stops.append(st)
        geo[slug] = {
            "map": name,
            "image_size": size,
            "full_image": f"mirror-{slug}-full.jpg",
            "efficient_image": f"mirror-{slug}-efficient.jpg",
            "waypoints": [{"name": k_, "x": v[0], "y": v[1]} for k_, v in starts.items()],
            "full": {"mirrors": mirrors, "chests": chests},
            "efficient": {
                "stops": stops,
                "legs": [{"start": leg["start"], "drawn": bool(leg.get("drawn")), "points": [_xy(q) for q in leg["points"]]} for leg in e["legs"]],
                "path_note": e.get("note", ""),
                "skipped_mirrors": e.get("skip"),
            },
        }
    (ROOT / "config" / "mirror_route_geometry.json").write_text(
        json.dumps({"_comment": "Generated by tools/mirror_maps_import.py from the owner's mirror-maps folder (addon repo docs/owner/2026-11-12/mirror-maps/, 2026-11-12). Pixel positions on the route images in assets/mirror-maps/ (full and efficient image of a map have the same size). Do not edit by hand: rerun the importer.",
                    "maps": geo}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for m in missing:
        print("full-sweep image missing (rerun after the probe):", m)
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
