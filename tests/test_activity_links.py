import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import activity_links as al  # noqa: E402


def _evt(i, name):
    return {"id": i, "name": name, "mode": "pve", "expansion": "core", "est_minutes": 20, "max_runs_per_day": 1,
            "guidance": {"text": "Core Tyria: every 360 min (UTC 04:20, 10:20, 16:20, 22:20; wiki event timer)",
                         "wiki_url": "https://wiki.guildwars2.com/wiki/X"}}


def test_ley_line_anomalies_share_one_daily_reward():
    ids = ["evt:core-la:gendarran_fields", "evt:core-la:iron_marches", "evt:core-la:timberline_falls"]
    final = {i: _evt(i, "Ley-Line Anomaly: " + i.split(":")[-1]) for i in ids}
    n, warn = al.apply_caps(final)
    assert n == 3 and warn == []
    for i in ids:
        assert final[i]["cap_groups"] == [{"id": "ley-line-anomaly", "name": al.CONFIG["groups"][0]["name"], "max_runs_per_day": 1}]
    assert al.apply_caps(final)[0] == 0          # idempotent
    al.describe(final)
    d = final[ids[0]]["description"]
    assert d.startswith("Timed event (Core Tyria), about 20 min: every 360 min (UTC 04:20, 10:20, 16:20, 22:20).")
    assert "Reward shared with Ley-Line Anomaly: iron_marches, Ley-Line Anomaly: timberline_falls (once a day in all)." in d


def test_missing_member_warns_and_lone_member_gets_no_group():
    final = {"evt:core-la:iron_marches": _evt("evt:core-la:iron_marches", "LLA")}
    n, warn = al.apply_caps(final)
    assert n == 0 and len(warn) == 2 and "cap_groups" not in final["evt:core-la:iron_marches"]


def test_place_and_track_descriptions():
    final = {"loc:cairn": {"id": "loc:cairn", "name": "Cairn", "mode": "pve", "expansion": "hot",
                           "guidance": {"text": "Raid in Bastion of the Penitent (no run length on the wiki: DRF times it)"}},
             "rt:x": {"id": "rt:x", "name": "X track", "mode": "wvw", "expansion": "core", "repeatable": False, "guidance": {"text": ""}},
             "ls3:gather:ember_bay": {"id": "ls3:gather:ember_bay", "name": "Gather", "mode": "pve", "expansion": "hot",
                                      "guidance": {"text": "Gather Petrified Wood in Ember Bay: 15 per character per day"}}}
    assert al.describe(final) == 3
    assert final["loc:cairn"]["description"] == "Raid in Bastion of the Penitent. No run length on the wiki: your own runs (DRF) time it."
    assert final["rt:x"]["description"] == "WVW reward track (once per account)."
    assert final["ls3:gather:ember_bay"]["description"] == "Gather Petrified Wood in Ember Bay: 15 per character per day."
