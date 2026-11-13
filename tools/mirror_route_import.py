#!/usr/bin/env python3
"""Magic Mirror routes from the route author's trails (Data 4o (1b), owner 2026-11-13).

The routes are 12Tables' (GW2 forum; the owner has his permission for public use), shipped in Lady
Elyssa's TacO marker pack ("Chests > Magic Mirrors > <map> > 12Tables Routes"). This one-off importer
reads the pack's trails and markers and writes
  config/mirror_route_geometry.json   "route" per map: start / waypoint legs, the route line, and the
                                      mirrors and chests the route opens, in route order (image pixels)
  assets/mirror-maps/mirror-<map>-route.jpg   the map in the efficient style: route mirrors and chests
                                      at full strength, every other marker and mirror-to-chest line at
                                      10%, one thin solid route line in the map's blue (#2696c4), the
                                      waypoint map icon + name at each leg start
The pack itself is not committed. Run:
  python tools/mirror_route_import.py <LadyElyssa_LadyElyssaTacoTrails.taco>

Owner rules (2026-11-13), in config/mirror_routes.json "chests": the medium route of each map; the
first large chest the route reaches is opened where the rule says so, other large spots are skipped.
The pack's medium markers include shared spawns, so a medium marker on a small-chest icon (known for
Eternity's Garden and Leyspring Hollows) is not a stop.
"""
from __future__ import annotations

import json
import math
import re
import struct
import sys
import zipfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage as ndi

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config" / "mirror_routes.json").read_text(encoding="utf-8"))
GEO_PATH = ROOT / "config" / "mirror_route_geometry.json"
ASSETS = ROOT / "assets" / "mirror-maps"
BLUE = (38, 150, 196)
FONT = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
NEAR_M = 10.0      # a marker within 10 m of the trail is on the route (the next one is > 20 m away)
GAP = [0.0, 0.0, 0.0]


def read_pack(path: Path):
    z = zipfile.ZipFile(path)
    xml = z.read("Chests.xml").decode("utf-8", "replace")
    trails, pois = {}, []
    for m in re.finditer(r"<(POI|Trail)\b([^>]*)/?>", xml):
        a = dict(re.findall(r'(\w+)="([^"]*)"', m.group(2)))
        ty = a.get("type", "")
        if not ty.startswith("legs.chests.magic"):
            continue
        if m.group(1) == "Trail":
            name = a["trailData"].replace("\\", "/")
            b = z.read(name)
            _, mid = struct.unpack("<ii", b[:8])
            pts = [list(struct.unpack("<fff", b[8 + 12 * i:20 + 12 * i])) for i in range((len(b) - 8) // 12)]
            trails[name.split("VoE/")[-1]] = {"map": mid, "pts": pts}
        else:
            pois.append({"kind": ty.split(".")[3], "map": int(a["MapID"]), "q": (float(a["xpos"]), float(a["ypos"]), float(a["zpos"]))})
    return trails, pois


def to_cont(m: dict, x: float, z: float):
    (mx0, my0), (mx1, my1) = m["map_rect"]
    (cx0, cy0), (cx1, cy1) = m["continent_rect"]
    return (cx0 + (x * 39.3701 - mx0) / (mx1 - mx0) * (cx1 - cx0),
            cy0 + (my1 - z * 39.3701) / (my1 - my0) * (cy1 - cy0))


def fit_image(m: dict, g: dict, mirrors_cont):
    """continent -> image pixels: axis-aligned scale + offset fitted on the pack's mirrors vs the image's."""
    ours = [(x["x"], x["y"]) for x in g["full"]["mirrors"]]
    (cx0, cy0), (cx1, cy1) = m["continent_rect"]
    W, H = g["image_size"]
    f = lambda p: ((p[0] - cx0) / (cx1 - cx0) * W, (p[1] - cy0) / (cy1 - cy0) * H)
    for _ in range(4):
        pa, pb = [], []
        for c in mirrors_cont:
            q = f(c)
            j = min(ours, key=lambda o: math.dist(o, q))
            if math.dist(j, q) < 150:
                pa.append(c), pb.append(j)
        A, B = np.array(pa), np.array(pb)
        o = np.ones(len(A))
        sx = np.linalg.lstsq(np.c_[A[:, 0], o], B[:, 0], rcond=None)[0]
        sy = np.linalg.lstsq(np.c_[A[:, 1], o], B[:, 1], rcond=None)[0]
        f = (lambda sx, sy: lambda p: (sx[0] * p[0] + sx[1], sy[0] * p[1] + sy[1]))(sx, sy)
    resid = [min(math.dist(o, f(c)) for o in ours) for c in mirrors_cont]
    return f, max(resid)


def segments(files: list[list]):
    """trail files in route order -> [(a, b, arc length at a)]; a gap point splits, file ends are waypoint jumps."""
    out, s = [], 0.0
    for pts in files:
        prev = None
        for p in pts:
            if p == GAP:
                prev = None
                continue
            if prev is not None:
                out.append((prev, p, s))
                s += math.dist(prev, p)
            prev = p
        s += 1.0
    return out


def on_route(q, segs):
    best = None
    for a, b, s0 in segs:
        a_, b_, p = np.array(a), np.array(b), np.array(q)
        ab = b_ - a_
        L = float(np.linalg.norm(ab))
        t = 0.0 if L == 0 else max(0.0, min(1.0, float(np.dot(p - a_, ab)) / L ** 2))
        d = float(np.linalg.norm(p - (a_ + t * ab)))
        if best is None or d < best[0]:
            best = (d, s0 + t * L)
    return best


def rdp(P, eps):
    if len(P) < 3:
        return P
    A, B = np.array(P[0]), np.array(P[-1])
    AB = B - A
    n = float(np.linalg.norm(AB))
    d = [abs(AB[0] * (p[1] - A[1]) - AB[1] * (p[0] - A[0])) / n if n else float(np.linalg.norm(np.array(p) - A)) for p in P]
    i = int(np.argmax(d))
    return rdp(P[:i + 1], eps)[:-1] + rdp(P[i:], eps) if d[i] > eps else [P[0], P[-1]]


def build(pack: Path) -> int:
    trails, pois = read_pack(pack)
    geo = json.loads(GEO_PATH.read_text(encoding="utf-8"))
    idx = {m["id"]: m for m in json.loads((ROOT / "snapshot" / "map-index.json").read_text(encoding="utf-8"))["maps"]}
    icon = Image.open(ASSETS / "waypoint-map-icon.png").convert("RGBA")
    for m in CFG["maps"]:
        slug, r = m["slug"], m["route"]
        g = geo["maps"][slug]
        mid = m["map_id"]
        files = [trails[f]["pts"] for f in r["trail_files"]]
        f, resid = fit_image(m, g, [to_cont(m, p["q"][0], p["q"][2]) for p in pois if p["map"] == mid and p["kind"] == "mirror"])
        img_xy = lambda q: f(to_cont(m, q[0], q[2]))
        segs = segments(files)
        ev = []
        for p in pois:
            if p["map"] == mid and p["kind"] in ("mirror", "medium", "large"):
                d, s = on_route(p["q"], segs)
                if d <= NEAR_M:
                    ev.append((s, p["kind"], p["q"]))
        ev.sort(key=lambda e: e[0])
        # image: marker masks from colour, painted out, laid back at 10%
        full = np.array(Image.open(ASSETS / g["full_image"]).convert("RGB"))
        R, G, B = (full[..., i].astype(int) for i in range(3))
        yellow = (R > 200) & (G > 165) & (B < 110)
        red = (R > 190) & (G < 90) & (B < 90)
        blue = (np.abs(full.astype(int) - np.array(BLUE)).sum(-1) < 90) | ((B > R + 70) & (B > G - 10) & (G > R + 40) & (B > 120))
        zone = ndi.binary_dilation(ndi.binary_dilation(yellow, iterations=2) | red, iterations=28)
        arrows = zone & (((G > 170) & (R < 130) & (B < 140)) | ((G > 170) & (B > 200) & (R < 120)))
        chest = ndi.binary_fill_holes(ndi.binary_dilation(yellow, iterations=2))
        mirror = ndi.binary_fill_holes(ndi.binary_dilation(red, iterations=2))
        mask = ndi.binary_dilation(chest | mirror | blue | arrows, iterations=2)
        clean = cv2.inpaint(np.ascontiguousarray(full[..., ::-1]), mask.astype(np.uint8) * 255, 4, cv2.INPAINT_TELEA)[..., ::-1]
        faded = np.where(mask[..., None], 0.1 * full + 0.9 * clean, full).astype(np.uint8)
        lab = {"chest": ndi.label(chest)[0], "mirror": ndi.label(mirror)[0]}
        cen = {k: dict(zip(range(1, v.max() + 1), ndi.center_of_mass(v > 0, v, range(1, v.max() + 1)))) for k, v in lab.items()}
        size = {k: ndi.sum(v > 0, v, range(1, v.max() + 1)) for k, v in lab.items()}
        known = [(c["x"], c["y"]) for c in g["full"]["chests"] if c["size"] != "small"]
        small = [(c["x"], c["y"]) for c in g["full"]["chests"] if c["size"] == "small"]
        restore = np.zeros(mask.shape, bool)
        stops, used, first_large, skipped = [], set(), False, {"large": 0, "shared_spawn": 0}
        for s, kind, q in ev:
            if kind == "large":
                if r["large"] != "first" or first_large:
                    skipped["large"] += 1
                    continue
                first_large = True
            x, y = img_xy(q)
            k = "mirror" if kind == "mirror" else "chest"
            cand = [(math.dist((x, y), (c[1], c[0])), l) for l, c in cen[k].items() if 60 < size[k][l - 1] < 6000]
            if k == "chest" and known:
                near_small = lambda c: min([math.dist(c, o) for o in small] + [1e9])
                cand = [(d, l) for d, l in cand if min(math.dist((cen[k][l][1], cen[k][l][0]), o) for o in known) < near_small((cen[k][l][1], cen[k][l][0]))]
            d, l = min(cand) if cand else (1e9, None)
            if d > 45 or (k, l) in used:
                skipped["shared_spawn"] += 1
                continue
            used.add((k, l))
            restore |= ndi.binary_dilation(lab[k] == l, iterations=3)
            cy, cx = cen[k][l]
            st = {"n": len(stops) + 1, "kind": k, "x": round(cx), "y": round(cy)}
            if k == "chest":
                st["size"] = "large" if kind == "large" else "medium"
            else:
                st["name"] = min(g["full"]["mirrors"], key=lambda o: math.dist((o["x"], o["y"]), (cx, cy)))["name"]
            stops.append(st)
        im = Image.fromarray(np.where(restore[..., None], full, faded).astype(np.uint8)).convert("RGBA")
        W, H = im.size
        SS = 3
        ov = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
        d = ImageDraw.Draw(ov)
        legs = []
        for fi, pts in enumerate(files):
            p0 = next(p for p in pts if p != GAP)
            c0 = to_cont(m, p0[0], p0[2])
            w = min(idx[mid]["waypoints"], key=lambda w: math.dist(c0, (w[1], w[2])))
            wx, wy = f((w[1], w[2]))
            seg, lines = [], []
            for p in pts + [GAP]:
                if p == GAP:
                    if len(seg) > 1:
                        lines.append(rdp([img_xy(q) for q in seg], 1.5))
                    seg = []
                else:
                    seg.append(p)
            for L in lines:
                d.line([(x * SS, y * SS) for x, y in L], fill=BLUE + (255,), width=3 * SS, joint="curve")
            legs.append({"start": w[0], "chat_code": w[3], "x": round(wx), "y": round(wy),
                         "points": [[round(x), round(y)] for L in lines for x, y in L]})
        im.alpha_composite(ov.resize((W, H), Image.LANCZOS))
        dd = ImageDraw.Draw(im)
        font = ImageFont.truetype(FONT, 22)
        isz = 44
        ic = icon.resize((isz, isz), Image.LANCZOS)
        for leg in legs:
            x, y = leg["x"], leg["y"]
            im.alpha_composite(ic, (int(x - isz / 2), int(y - isz / 2)))
            tw = dd.textlength(leg["start"], font=font)
            tx = x + isz / 2 + 4 if x + isz / 2 + 4 + tw < W - 8 else x - isz / 2 - 4 - tw
            dd.text((tx, y), leg["start"], font=font, fill=(255, 255, 255), anchor="lm", stroke_width=3, stroke_fill=(0, 0, 0))
        out = ASSETS / f"mirror-{slug}-route.jpg"
        im.convert("RGB").save(out, quality=88, optimize=True, progressive=True)
        n_m = sum(s["kind"] == "mirror" for s in stops)
        n_c = {z: sum(s.get("size") == z for s in stops) for z in ("medium", "large")}
        g["route"] = {"image": out.name, "legs": legs, "stops": stops,
                      "counts_on_image": {"mirrors": n_m, **n_c}, "skipped": skipped,
                      "fit_max_mirror_px": round(resid, 1), "trail_files": r["trail_files"]}
        print(f"{slug}: {n_m} mirrors, {n_c} chests on the image, skipped {skipped}, legs {[l['start'] for l in legs]}, fit {resid:.1f}px")
    geo["_comment"] = (geo["_comment"].split(" Route:")[0] + " Route: tools/mirror_route_import.py from 12Tables' routes "
                       "(Lady Elyssa's TacO pack); positions on the route image, which has the full image's size.")
    GEO_PATH.write_text(json.dumps(geo, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(build(Path(sys.argv[1])))
