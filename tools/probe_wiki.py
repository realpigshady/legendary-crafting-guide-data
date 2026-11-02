#!/usr/bin/env python3
"""Developer probe (not part of the pack build): runs the api.php queries in
probe/queries.json and saves each reply to probe/out/<name>.json, so the data shapes
can be inspected without wiki access. One request per second."""
import json, sys, time, urllib.parse, urllib.request
from pathlib import Path

API = "https://wiki.guildwars2.com/api.php"
UA = "legendary-crafting-guide-data probe (github.com/realpigshady/legendary-crafting-guide-data)"
root = Path(__file__).resolve().parent.parent / "probe"
out = root / "out"
out.mkdir(parents=True, exist_ok=True)
for name, params in json.loads((root / "queries.json").read_text()).items():
    url = f"{API}?{urllib.parse.urlencode({'format': 'json', 'formatversion': '2', **params})}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
            body = r.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {"_raw": body[:2000]}
    except Exception as e:  # noqa: BLE001
        data = {"_error": str(e)}
    (out / f"{name}.json").write_text(json.dumps({"params": params, "reply": data}, indent=1, ensure_ascii=False))
    print(name, len(json.dumps(data)))
    time.sleep(1)
