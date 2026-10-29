import json
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
    http.index[http._key(p)] = time.time() - age_h * 3600


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


def test_checkout_mtime_does_not_make_old_page_fresh(tmp_path):
    http = bp.Http(tmp_path)
    seed(http, URL, {"v": "old"}, age_h=24 * 8)   # index says 8 days old; file mtime = now
    http._fetch = lambda url: {"v": "new"}
    assert http.get_json(URL) == {"v": "new"}


def test_unchanged_page_not_rewritten(tmp_path):
    http = bp.Http(tmp_path)
    http._fetch = lambda url: {"v": 1}
    http.get_json(URL)
    p = http._path(URL)
    before = p.stat().st_mtime_ns
    http.index[http._key(p)] = 0            # force refetch
    time.sleep(0.01)
    http.get_json(URL)
    assert p.stat().st_mtime_ns == before   # same content -> no write -> no git churn


def test_prune_removes_unused_but_not_during_outage(tmp_path):
    http = bp.Http(tmp_path)
    seed(http, URL + "a", {"a": 1}, age_h=1)
    seed(http, URL + "b", {"b": 1}, age_h=1)
    http.get_json(URL + "a")
    http._down_hosts.add("x")
    assert http.prune() == 0
    http._down_hosts.clear()
    assert http.prune() == 1
    assert not http._path(URL + "b").exists()


def test_index_roundtrip(tmp_path):
    http = bp.Http(tmp_path)
    http._fetch = lambda url: {"v": 1}
    http.get_json(URL)
    http.save_index()
    again = bp.Http(tmp_path)
    assert list(again.index) == list(http.index)
