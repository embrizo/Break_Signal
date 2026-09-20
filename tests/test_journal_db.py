import sqlite3

import pytest

from break_signal.core.types import Signal
from break_signal.journal.db import SEED_TAGS, JournalDB, iso_to_ms


@pytest.fixture
def db():
    d = JournalDB(":memory:")
    yield d
    d.close()


def _signal(**over) -> Signal:
    base = dict(symbol="SOL-USDT-SWAP", exchange="OKX", tf="4H", event="break_up",
                side="resistance", price=231.5, line=229.8, atr_dist=0.35, touches=4,
                age_bars=51, vol_ratio=1.9, rsi=61.3, time="2026-09-20T04:00:00Z",
                line_id="R:1000:2000")
    base.update(over)
    return Signal(**base)


# ── schema ───────────────────────────────────────────────────────────────────
def test_schema_init_is_idempotent(tmp_path):
    path = tmp_path / "j.db"
    a = JournalDB(path)
    a.add_trade("SOL-USDT-SWAP", "LONG", entry_price=1.0)
    a.close()
    b = JournalDB(path)  # re-open: no errors, data intact, seed not duplicated
    assert len(b.list_trades()) == 1
    assert len(b.list_tags()) == len(SEED_TAGS)
    versions = b.conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
    assert versions == 1
    b.close()


def test_seed_tags_present_and_bilingual(db):
    names = {t.name for t in db.list_tags()}
    assert {"Breakout", "FOMO", "ตามวินัย", "แหกกฎเลื่อน SL"} <= names
    assert db.get_tag("fomo").category == "PSYCH"


# ── signals ──────────────────────────────────────────────────────────────────
def test_insert_signal_from_signal_object_and_unique(db):
    sid = db.insert_signal(_signal(), source="live")
    again = db.insert_signal(_signal(), source="live")
    assert sid == again
    row = db.get_signal(sid)
    assert row.line_price == 229.8 and row.rsi == 61.3
    assert row.candle_ts == iso_to_ms("2026-09-20T04:00:00Z")
    # different candle → new row
    other = db.insert_signal(_signal(time="2026-09-21T04:00:00Z"), source="live")
    assert other != sid


def test_latest_signal_and_list(db):
    db.insert_signal(_signal(time="2026-09-18T00:00:00Z", line_id="a"), "backtest")
    newest = db.insert_signal(_signal(time="2026-09-20T00:00:00Z", line_id="b"), "live")
    assert db.latest_signal("SOL-USDT-SWAP", tf="4H").id == newest
    assert db.latest_signal("SOL-USDT-SWAP", event="break_down") is None
    assert [s.id for s in db.list_signals(source="live")] == [newest]
    assert len(db.list_signals()) == 2


# ── trades ───────────────────────────────────────────────────────────────────
def test_add_trade_defaults_and_null_fields(db):
    t = db.add_trade("SOL-USDT-SWAP", "long", entry_price=100, sl_price=90,
                     entry_tags=["Breakout", "newtag"])
    assert t.status == "OPEN" and t.direction == "LONG"
    assert t.tp_price is None and t.exit_price is None and t.r_multiple is None
    assert t.entry_tags == ["Breakout", "newtag"]
    assert db.get_tag("newtag").category == "OTHER"
    assert t.ctx_session in ("ASIA", "LONDON", "NY")


def test_add_trade_rejects_bad_direction_and_unknown_field(db):
    with pytest.raises(ValueError):
        db.add_trade("SOL-USDT-SWAP", "sideways")
    with pytest.raises(ValueError):
        db.add_trade("SOL-USDT-SWAP", "LONG", bogus=1)


def test_risk_pct_from_account_size():
    d = JournalDB(":memory:", account_size=10_000)
    t = d.add_trade("SOL-USDT-SWAP", "LONG", risk_amount=150)
    assert t.risk_pct == pytest.approx(1.5)
    d.close()


def test_close_trade_computes_r_and_outcome(db):
    t = db.add_trade("SOL-USDT-SWAP", "LONG", entry_price=100, sl_price=90, tp_price=120)
    c = db.close_trade(t.id, 115, exit_reason="hit target", exit_tags=["Hit TP"])
    assert c.status == "CLOSED"
    assert c.r_multiple == pytest.approx(1.5)
    assert c.outcome == "WIN"
    assert c.exit_reason == "hit target"
    assert c.exit_tags == ["Hit TP"] and c.entry_tags == []
    assert c.pnl_amount is None  # no size, no risk amount


def test_close_trade_short_and_user_outcome_override(db):
    t = db.add_trade("SOL-USDT-SWAP", "SHORT", entry_price=100, sl_price=110,
                     position_size=3, fees=0.5)
    c = db.close_trade(t.id, 95, outcome="be")  # user says BE despite +0.5R
    assert c.r_multiple == pytest.approx(0.5)
    assert c.outcome == "BE"
    assert c.pnl_amount == pytest.approx(5 * 3 - 0.5)


def test_close_twice_rejected(db):
    t = db.add_trade("SOL-USDT-SWAP", "LONG", entry_price=100, sl_price=90)
    db.close_trade(t.id, 110)
    with pytest.raises(ValueError):
        db.close_trade(t.id, 111)
    with pytest.raises(KeyError):
        db.close_trade(999, 1)


def test_close_without_sl_leaves_r_none_but_derives_nothing(db):
    t = db.add_trade("SOL-USDT-SWAP", "LONG", entry_price=100)
    c = db.close_trade(t.id, 110)
    assert c.r_multiple is None and c.outcome is None


def test_skip_signal(db):
    sid = db.insert_signal(_signal(event="break_down", side="support"), "live")
    t = db.skip_signal(sid, reason="RSI too low", tags=["Hesitation"])
    assert t.status == "SKIPPED" and t.direction == "SHORT" and t.signal_id == sid
    assert t.ctx_rsi == 61.3 and t.tf == "4H"
    assert t.signal is not None and t.signal.event == "break_down"


def test_events_and_screenshots_cascade_on_delete(db):
    t = db.add_trade("SOL-USDT-SWAP", "LONG", entry_price=100, sl_price=90,
                     entry_tags=["Breakout"])
    db.add_event(t.id, "sl_moved", {"from": 90, "to": 88})
    db.add_screenshot(t.id, "pre", "shots/1.png")
    full = db.get_trade(t.id)
    assert full.events[0].type == "sl_moved" and full.events[0].data == {"from": 90, "to": 88}
    assert full.screenshots[0].phase == "PRE"
    db.delete_trade(t.id)
    assert db.get_trade(t.id) is None
    for table in ("trade_events", "screenshots", "trade_tags"):
        assert db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_list_trades_filters(db):
    a = db.add_trade("SOL-USDT-SWAP", "LONG", tf="4H", entry_price=100, sl_price=90,
                     entry_tags=["Breakout", "FOMO"], opened_ts=1_000)
    b = db.add_trade("BTC-USDT-SWAP", "SHORT", tf="1D", entry_price=100, sl_price=110,
                     entry_tags=["Breakout"], opened_ts=2_000)
    db.close_trade(a.id, 120)
    assert [t.id for t in db.list_trades()] == [b.id, a.id]  # newest first
    assert [t.id for t in db.list_trades(symbol="SOL-USDT-SWAP")] == [a.id]
    assert [t.id for t in db.list_trades(status="OPEN")] == [b.id]
    assert [t.id for t in db.list_trades(outcome="WIN")] == [a.id]
    assert [t.id for t in db.list_trades(tags=["breakout"])] == [b.id, a.id]
    assert [t.id for t in db.list_trades(tags=["Breakout", "fomo"])] == [a.id]
    assert [t.id for t in db.list_trades(since=1_500)] == [b.id]
    assert [t.id for t in db.list_trades(until=1_500)] == [a.id]
    assert [t.id for t in db.list_trades(direction="SHORT")] == [b.id]
    assert db.list_trades(signal_linked=True) == []
    assert len(db.list_trades(limit=1)) == 1


def test_tag_ops(db):
    t = db.add_trade("SOL-USDT-SWAP", "LONG", entry_tags=["Breakout"])
    db.attach_tags(t.id, ["breakout"], "ENTRY")  # NOCASE duplicate ignored
    assert db.get_trade(t.id).entry_tags == ["Breakout"]
    db.attach_tags(t.id, ["Breakout"], "EXIT")  # same tag, different phase is allowed
    assert db.get_trade(t.id).exit_tags == ["Breakout"]
    db.detach_tag(t.id, "Breakout", "EXIT")
    assert db.get_trade(t.id).exit_tags == []
    renamed = db.rename_tag("Breakout", "BO")
    assert renamed.name == "BO" and db.get_trade(t.id).entry_tags == ["BO"]
    assert db.set_tag_category("BO", "OTHER").category == "OTHER"
    with pytest.raises(ValueError):
        db.get_or_create_tag("x", "NOPE")
    with pytest.raises(ValueError):
        db.attach_tags(t.id, ["x"], "MIDDLE")


def test_tag_names_unique_nocase(db):
    db.get_or_create_tag("Alpha")
    assert db.get_or_create_tag("alpha").name == "Alpha"
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute("INSERT INTO tags(name, category) VALUES ('ALPHA','OTHER')")


def test_update_trade(db):
    t = db.add_trade("SOL-USDT-SWAP", "LONG", entry_price=100)
    u = db.update_trade(t.id, notes="hello", confidence=3)
    assert u.notes == "hello" and u.confidence == 3
    with pytest.raises(ValueError):
        db.update_trade(t.id, nope=1)


def test_outcome_check_constraint(db):
    t = db.add_trade("SOL-USDT-SWAP", "LONG")
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute("UPDATE trades SET outcome='MAYBE' WHERE id=?", (t.id,))
