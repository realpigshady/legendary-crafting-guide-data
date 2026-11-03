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
