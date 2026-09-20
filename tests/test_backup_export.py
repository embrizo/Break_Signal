"""Phase J5: nightly backup snapshots, markdown/CSV/JSON export."""
import csv
import io
import json
from datetime import datetime, timezone

import pytest

from break_signal.journal import backup, export
from break_signal.journal.cli import main
from break_signal.journal.db import JournalDB
from break_signal.journal.tools import Tools


@pytest.fixture
def db(tmp_path):
    d = JournalDB(tmp_path / "j.db")
    t = Tools(d)
    a = t.add_trade("SOL", "LONG", tf="4H", entry_price=100, sl_price=90, tp_price=120,
                    tags=["Breakout"], entry_reason="clean", auto_link=False)["trade"]["id"]
    t.close_trade(a, 120, exit_reason="hit tp", tags=["Hit TP"])
    t.add_event(a, "note", {"text": "held"})
    t.add_trade("BTC", "SHORT", entry_price=100, sl_price=110, auto_link=False)
    d.insert_signal(dict(symbol="SOL-USDT-SWAP", exchange="OKX", tf="4H", event="break_up", side="resistance",
                         price=1, line=1, atr_dist=0.4, touches=3, age_bars=1, vol_ratio=1.5, rsi=60.0,
                         time="2026-01-01T00:00:00Z", line_id="x"), "live")
    yield d
    d.close()


# ── export ───────────────────────────────────────────────────────────────────
def test_markdown_oldest_first_with_details(db):
    md = export.to_markdown(db.list_trades())
    assert md.startswith("# Trading journal export")
    assert md.index("## #1 ") < md.index("## #2 ")
    assert "CLOSED WIN 2.00R" in md and "- entry reason: clean" in md and "- exit reason: hit tp" in md
    assert "- event " in md and "note {'text': 'held'}" in md
    assert "- entry tags: Breakout" in md and "- exit tags: Hit TP" in md


def test_csv_and_json(db):
    rows = list(csv.DictReader(io.StringIO(export.to_csv(db.list_trades()))))
    assert len(rows) == 2 and rows[0]["id"] == "2" and rows[1]["entry_tags"] == "Breakout"
    assert set(rows[0]) == set(export.CSV_COLUMNS)
    js = json.loads(export.to_json(db.list_trades()))
    assert js[1]["id"] == 1 and js[1]["tags"] == ["Breakout", "Hit TP"]


# ── backup ───────────────────────────────────────────────────────────────────
def test_backup_snapshot_is_a_valid_db_and_md(db, tmp_path):
    dest = tmp_path / "backups"
    out = backup.backup(db, dest, keep=14, now=datetime(2026, 9, 21, 0, 5, tzinfo=timezone.utc))
    assert out["db"].endswith("journal-20260921-0005.db") and out["md"].endswith("journal-20260921-0005.md")
    assert out["bytes"] > 0 and out["pruned"] == []
    v = backup.verify(out["db"])
    assert v["integrity"] == "ok" and v["trades"] == 2 and v["signals"] == 1
    assert sorted(p.name for p in dest.iterdir()) == ["journal-20260921-0005.db", "journal-20260921-0005.md"]
    import sqlite3
    assert sqlite3.connect(out["db"]).execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    md = (dest / "journal-20260921-0005.md").read_text(encoding="utf-8")
    assert md.startswith("# Trading journal backup") and "## #1 " in md
    # the snapshot is independent: writing to the live db doesn't change it
    Tools(db).add_trade("SOL", "LONG", auto_link=False)
    assert backup.verify(out["db"])["trades"] == 2


def test_backup_prunes_oldest_pairs(db, tmp_path):
    dest = tmp_path / "backups"
    for day in range(1, 6):
        backup.backup(db, dest, keep=3, now=datetime(2026, 9, day, 0, 5, tzinfo=timezone.utc))
    dbs = sorted(p.name for p in dest.glob("*.db"))
    mds = sorted(p.name for p in dest.glob("*.md"))
    assert dbs == ["journal-20260903-0005.db", "journal-20260904-0005.db", "journal-20260905-0005.db"]
    assert [m.replace(".md", ".db") for m in mds] == dbs
    assert backup.prune(dest, 0) == []          # keep=0 means never prune


def test_next_run():
    now = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
    assert backup.next_run("00:05", now) == datetime(2026, 9, 22, 0, 5, tzinfo=timezone.utc)
    assert backup.next_run("12:01", now) == datetime(2026, 9, 21, 12, 1, tzinfo=timezone.utc)
    assert backup.next_run("12:00", now) == datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)   # strictly after
    with pytest.raises(ValueError):
        backup.next_run("24:00", now)


def test_cli_backup_and_export(db, tmp_path, capsys):
    dbp = str(db.path)
    rc = main(["--db", dbp, "--config", "nope.yaml", "backup", "--dir", str(tmp_path / "b"), "--keep", "2"])
    out = capsys.readouterr().out
    assert rc == 0 and "wrote" in out and "verified: integrity=ok trades=2 signals=1" in out
    snap = next((tmp_path / "b").glob("*.db"))
    rc = main(["--db", dbp, "--config", "nope.yaml", "backup", "--verify", str(snap)])
    assert rc == 0 and json.loads(capsys.readouterr().out)["integrity"] == "ok"
    rc = main(["--db", dbp, "--config", "nope.yaml", "export", "--format", "csv"])
    assert rc == 0 and capsys.readouterr().out.startswith("id,status,signal_id")
    rc = main(["--db", dbp, "--config", "nope.yaml", "export", "--out", str(tmp_path / "j.md")])
    assert rc == 0 and (tmp_path / "j.md").read_text(encoding="utf-8").startswith("# Trading journal export")
