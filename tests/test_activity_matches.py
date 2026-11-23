import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import activity_matches as am  # noqa: E402

SYNTRI = "evt:jw-js:of_mists_and_monsters"
SYNTRI_FAST = "fast:open-world.meta:janthir-syntri-of-mists-and-monsters"


def _by_wiki(wiki):
    return next(m for m in am.CONFIG["matches"] if m["wiki"] == wiki)


def test_janthir_syntri_joins_its_fast_meta():
    final = {SYNTRI: {"id": SYNTRI, "max_runs_per_day": 1}}
    stats, warn = am.apply(final)
    a = final[SYNTRI]
    assert a["same_as"] == SYNTRI_FAST and a["group"] == SYNTRI_FAST
    assert "cap_groups" not in a
    assert stats["linked"] == 1 and all("not in the pack" in w for w in warn)


def test_raid_copy_takes_the_fast_group_and_weekly_chest():
    m = _by_wiki("loc:aetherblade_hideout")
    final = {m["wiki"]: {"id": m["wiki"], "max_runs_per_week": 1}}
    am.apply(final)
    am.apply(final)   # idempotent
    a = final[m["wiki"]]
    assert a["group"] == "fast:instances.raids:aetherblade-hideout"
    assert a["cap_groups"] == [{"id": "weekly-chest:bouncy-chest-event-aetherblade-hideout",
                                "name": am.WEEKLY_CAP_NAME, "max_runs_per_week": 1}]


def test_config_is_one_entry_per_wiki_record_and_ids_only():
    ws = [m["wiki"] for m in am.CONFIG["matches"]]
    assert len(ws) == len(set(ws))
    for m in am.CONFIG["matches"]:
        assert set(m) <= {"wiki", "fast", "group", "weekly_cap"}
        assert m["fast"].startswith(("fast:", "owner:")) and not m["wiki"].startswith("fast:")


def test_castora_groups_agree():
    import castora
    for wid, meta in castora.CONFIG["metas"].items():
        if "group" in meta:
            m = _by_wiki(wid)
            assert m.get("group", m["fast"]) == meta["group"]
