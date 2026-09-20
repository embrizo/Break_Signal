"""The CLI must behave exactly like the MCP tools: same rule checks, seeding, linking."""
import json

import pytest

from break_signal.journal import rules
from break_signal.journal.cli import main
from break_signal.journal.db import JournalDB


@pytest.fixture
def dbp(tmp_path):
    return str(tmp_path / "j.db")


def run(dbp, *argv, capsys):
    rc = main(["--db", dbp, "--config", "nope.yaml", *argv])
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_add_close_records_rules_and_seeds(dbp, capsys):
    rc, out, _ = run(dbp, "add", "SOL 1D long 100 sl 90 tp 101 risk 3% #fomo", capsys=capsys)
    assert rc == 0 and "added trade #1" in out and "SOL-USDT-SWAP" in out
    assert "rule 'Max risk 1%'" in out and "Planned R:R >= 1.5" in out and "No FOMO entries" in out
    rc, out, _ = run(dbp, "close", "1 95 stopped out #hit_sl", capsys=capsys)
    assert rc == 0 and "LOSS" in out
    db = JournalDB(dbp)
    assert len(rules.list_rules(db)) == len(rules.SEED_RULES)            # seeded by the CLI too
    assert {v["name"] for v in rules.stored_violations(db, 1)} >= {"Max risk 1%"}
    db.close()


def test_add_with_signal_copies_context(dbp, capsys):
    db = JournalDB(dbp)
    sid = db.insert_signal(dict(symbol="SOL-USDT-SWAP", exchange="OKX", tf="4H", event="break_up",
                                side="resistance", price=1, line=1, atr_dist=0.4, touches=3, age_bars=1,
                                vol_ratio=1.5, rsi=58.0, time="2026-01-01T00:00:00Z", line_id="x"), "live")
    db.close()
    rc, out, _ = run(dbp, "add", "SOL 4H long 100 sl 95", "--signal", str(sid), capsys=capsys)
    assert rc == 0
    rc, out, _ = run(dbp, "show", "1", "--json", capsys=capsys)
    d = json.loads(out)
    assert d["signal_id"] == sid and d["ctx_rsi"] == 58.0


def test_event_reports_violation_and_errors_are_clean(dbp, capsys):
    run(dbp, "add", "SOL 4H long 100 sl 95 tp 110", capsys=capsys)
    rc, out, _ = run(dbp, "event", "1", "sl_moved", "from=95", "to=90", capsys=capsys)
    assert rc == 0 and "Never widen the stop" in out
    rc, _, err = run(dbp, "close", "99 100", capsys=capsys)
    assert rc == 2 and "no trade #99" in err
    rc, _, err = run(dbp, "tag", "rename", "Calm", "FOMO", capsys=capsys)
    assert rc == 2 and "already exists" in err
    rc, _, err = run(dbp, "add", "SOL 4H 100", capsys=capsys)
    assert rc == 2 and "direction" in err


def test_stats_and_list_run(dbp, capsys):
    run(dbp, "add", "SOL 4H long 100 sl 90", capsys=capsys)
    run(dbp, "close", "1 120", capsys=capsys)
    rc, out, _ = run(dbp, "stats", "--json", capsys=capsys)
    assert rc == 0 and json.loads(out)["summary"]["wins"] == 1
    rc, out, _ = run(dbp, "list", capsys=capsys)
    assert rc == 0 and "#1" in out and "WIN" in out
