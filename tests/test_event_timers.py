import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import event_timers as et  # noqa: E402

EV = et.parse((Path(__file__).resolve().parent / "fixtures" / "event_timer_data.json").read_text())


def test_run_length_cycle_and_waypoint():
    t = et.timer_for(EV, "hot-td")
    assert t["minutes"] == 20 and t["every"] == 120 and t["waypoint"].startswith("[&")
    assert len(t["starts"]) == 12 and all(s % 120 == t["starts"][0] % 120 for s in t["starts"])


def test_back_to_back_runs_each_start():   # Dragon's Stand: one phase fills the cycle
    assert len(et.timer_for(EV, "hot-ds")["starts"]) == 12


def test_apply_by_id_and_by_map_name():
    acts = [{"id": "auric_basin", "name": "Auric Basin Meta"}, {"id": "wiki:dry_top", "name": "Dry Top"},
            {"id": "strikes", "name": "Strike Missions", "est_minutes": 30}]
    assert et.apply(acts, EV) == 2
    assert acts[0]["est_minutes"] == 20 and "every 120 min" in acts[0]["guidance"]["text"]
    assert acts[1]["est_minutes"] == 20 and "every 60 min" in acts[1]["guidance"]["text"]
    assert acts[2]["est_minutes"] == 30 and "guidance" not in acts[2]       # not a timed meta: untouched


def test_catalog_has_every_phase():
    import activity_catalog as ac
    acts = ac.timed(EV)
    ids = {a["id"] for a in acts}
    assert not any("dn:" in i for i in ids)                                  # day/night bars skipped
    named = {(k, s.get("name")) for k, m in EV.items() if not ac.SKIP_MAPS.search(m.get("name", "")) and m.get("name")
             for s in m["segments"].values() if s.get("name")}
    assert len(acts) >= len(named) - 5                                      # every named phase (phases only in unused slots aside)
    wb = [a for a in acts if a["id"].startswith("evt:core-wb:")]
    assert len(wb) >= 10 and all(a["est_minutes"] > 0 and a["starts_utc"] for a in wb)
    oct_ = next(a for a in acts if a["id"] == "evt:hot-ab:battle_in_tarir_octovine")
    assert oct_["est_minutes"] == 20 and "every 120 min" in oct_["guidance"]["text"]
