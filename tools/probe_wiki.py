#!/usr/bin/env python3
"""Developer probe (not part of the pack build): runs the api.php queries in
probe/queries.json and saves each reply to probe/out/<name>.json, so the data shapes
can be inspected without wiki access. One request per second.

A query {"download": "<File page title>"} instead saves that wiki file (through
Special:FilePath) to probe/out/files/<name>.<ext>. The wiki answers intermittently, so each
request is tried up to 10 times, 6 s apart (owner rule 2026-11-12: the wiki's errors clear after a few refreshes).
A query {"api": "/v2/..."} is one GET to the official GW2 API (no key) instead of the wiki."""
import json, sys, time, urllib.parse, urllib.request
from pathlib import Path

API = "https://wiki.guildwars2.com/api.php"
GW2_API = "https://api.guildwars2.com"
FILEPATH = "https://wiki.guildwars2.com/wiki/Special:FilePath/"
UA = "legendary-crafting-guide-data probe (github.com/realpigshady/legendary-crafting-guide-data)"
root = Path(__file__).resolve().parent.parent / "probe"
out = root / "out"
out.mkdir(parents=True, exist_ok=True)


def fetch(url: str) -> bytes:
    last = None
    for attempt in range(10):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < 9:
                time.sleep(6)
    raise last


for name, params in json.loads((root / "queries.json").read_text()).items():
    if "api" in params:
        try:
            data = json.loads(fetch(GW2_API + params["api"]).decode("utf-8", errors="replace"))
        except Exception as e:  # noqa: BLE001
            data = {"_error": str(e)}
    elif "download" in params:
        title = params["download"].removeprefix("File:")
        try:
            body = fetch(FILEPATH + urllib.parse.quote(title.replace(" ", "_")))
            (out / "files").mkdir(exist_ok=True)
            ext = Path(title).suffix or ".bin"
            (out / "files" / f"{name}{ext}").write_bytes(body)
            data = {"saved": f"files/{name}{ext}", "bytes": len(body)}
        except Exception as e:  # noqa: BLE001
            data = {"_error": str(e)}
    else:
        url = f"{API}?{urllib.parse.urlencode({'format': 'json', 'formatversion': '2', **params})}"
        try:
            body = fetch(url).decode("utf-8", errors="replace")
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                data = {"_raw": body[:2000]}
        except Exception as e:  # noqa: BLE001
            data = {"_error": str(e)}
    (out / f"{name}.json").write_text(json.dumps({"params": params, "reply": data}, indent=1, ensure_ascii=False))
    print(name, len(json.dumps(data)))
    time.sleep(1)
