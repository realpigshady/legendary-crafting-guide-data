import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import build_pack as bp  # noqa: E402

URL = "https://wiki.guildwars2.com/api.php?action=parse&page=Nevermore"


def seed(http: bp.Http, url: str, body, age_h: float):
    p = http._path(url)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"url": url, "body": body}))
    old = time.time() - age_h * 3600
    os.utime(p, (old, old))


def test_fresh_snapshot_used_without_network(tmp_path):
    http = bp.Http(tmp_path)
    seed(http, URL, {"ok": 1}, age_h=1)
    http._fetch = lambda url: pytest.fail("should not fetch")
    assert http.get_json(URL) == {"ok": 1}
    assert http.stale == {}


def test_source_down_falls_back_to_old_snapshot(tmp_path):
    http = bp.Http(tmp_path)
    seed(http, URL, {"ok": "old"}, age_h=24 * 30)

    def down(url):
        raise OSError("503")
    http._fetch = down
    assert http.get_json(URL) == {"ok": "old"}
    assert http.stale == {"wiki.guildwars2.com": 1}


def test_host_marked_down_after_first_failure(tmp_path):
    http = bp.Http(tmp_path)
    for i in range(3):
        seed(http, f"{URL}{i}", {"i": i}, age_h=100)
    calls = []

    def down(url):
        calls.append(url)
        raise OSError("down")
    http._fetch = down
    for i in range(3):
        http.get_json(f"{URL}{i}")
    assert len(calls) == 1   # one retry cycle per outage, not per page
    assert http.stale["wiki.guildwars2.com"] == 3


def test_no_snapshot_raises(tmp_path):
    http = bp.Http(tmp_path, offline=True)
    with pytest.raises(bp.SourceDown):
        http.get_json(URL)
