import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import snapshot_size as ss  # noqa: E402


def test_measure_groups_by_top_folder(tmp_path):
    (tmp_path / "wiki-pages").mkdir()
    (tmp_path / "wiki-pages" / "a.json").write_bytes(b"x" * 300)
    (tmp_path / "wiki-pages" / "b.json").write_bytes(b"x" * 200)
    (tmp_path / "index.json").write_bytes(b"x" * 10)
    sizes = ss.measure(tmp_path)
    assert sizes == {"wiki-pages": (2, 500), ".": (1, 10)}
    table, over = ss.report(sizes, budget_mb=200)
    assert not over and "| **total** | **3** |" in table
    assert ss.report(sizes, budget_mb=0.0004)[1]   # 510 B over a 400 B budget


def test_over_budget_appends_warning(tmp_path, monkeypatch, capsys):
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "big.json").write_bytes(b"x" * 1000)
    warn = tmp_path / "warnings.txt"
    warn.write_text("first\n")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(sys, "argv", ["x", "--snapshot", str(snap), "--budget-mb", "0.0005", "--warnings", str(warn)])
    assert ss.main() == 0
    assert "::warning::" in capsys.readouterr().out
    assert warn.read_text().splitlines()[1].startswith("SIZE: snapshot/ is")
