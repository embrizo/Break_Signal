import pytest

from break_signal.journal import similar
from break_signal.journal.db import JournalDB

SIG = dict(symbol="SOL-USDT-SWAP", exchange="OKX", tf="4H", event="break_up", side="resistance",
           price=100.0, line=99.0, atr_dist=0.4, touches=3, age_bars=20, vol_ratio=1.6, rsi=60.0,
           time="2026-09-01T00:00:00Z", line_id="R:1:2")


@pytest.fixture
def db():
    d = JournalDB(":memory:")
    sid = d.insert_signal(SIG, "live")
    # id 1: perfect match — same symbol/tf, linked to resistance break, tags overlap, rsi 60
    a = d.add_trade("SOL-USDT-SWAP", "LONG", tf="4H", entry_price=100, sl_price=95, signal_id=sid,
                    entry_tags=["Breakout", "Retest"], ctx_rsi=60, ctx_atr_dist=0.4, opened_ts=1_000)
    d.close_trade(a.id, 110, closed_ts=1_500, exit_reason="clean")
    # id 2: same symbol/tf, no tags in common, no signal, rsi far away
    b = d.add_trade("SOL-USDT-SWAP", "LONG", tf="4H", entry_price=100, sl_price=95,
                    entry_tags=["FOMO"], ctx_rsi=30, ctx_atr_dist=1.5, opened_ts=2_000)
    d.close_trade(b.id, 95, closed_ts=2_500)
    # id 3: other symbol, other tf, but tags overlap fully and rsi close
    c = d.add_trade("BTC-USDT-SWAP", "LONG", tf="1D", entry_price=100, sl_price=95,
                    entry_tags=["Breakout", "Retest"], ctx_rsi=64, opened_ts=3_000)
    d.close_trade(c.id, 105, closed_ts=3_500)
    # id 4: SHORT — must be excluded by the direction filter
    s = d.add_trade("SOL-USDT-SWAP", "SHORT", tf="4H", entry_price=100, sl_price=105,
                    entry_tags=["Breakout", "Retest"], ctx_rsi=60, opened_ts=4_000)
    d.close_trade(s.id, 90, closed_ts=4_500)
    # id 5: OPEN — excluded (no outcome yet)
    d.add_trade("SOL-USDT-SWAP", "LONG", tf="4H", entry_tags=["Breakout", "Retest"], opened_ts=5_000)
    yield d
    d.close()


def test_proposed_fills_side_event():
    p = similar.Proposed("SOL-USDT-SWAP", "long", side="resistance")
    assert p.direction == "LONG" and p.event == "break_up"
    q = similar.Proposed("SOL-USDT-SWAP", "SHORT", event="break_down")
    assert q.side == "support"


def test_score_breakdown(db):
    p = similar.Proposed("SOL-USDT-SWAP", "LONG", tf="4H", side="resistance",
                         tags=["breakout", "retest"], rsi=62, atr_dist=0.3)
    t = db.get_trade(1)
    s, br = similar.score(p, t)
    assert br == {"tag_jaccard": 1.0, "shared_tags": ["breakout", "retest"], "same_side_event": True,
                  "same_symbol": True, "same_tf": True, "rsi_within_8": True, "same_atr_band": True}
    assert s == pytest.approx(3 + 2 + 2 + 1 + 1 + 1)


def test_ranking_and_filters(db):
    p = similar.Proposed("SOL-USDT-SWAP", "LONG", tf="4H", side="resistance",
                         tags=["Breakout", "Retest"], rsi=62, atr_dist=0.3)
    res = similar.similar_trades(p, db.list_trades(), k=8)
    ids = [m["id"] for m in res["matches"]]
    assert ids == [1, 3, 2]                 # perfect > tag/rsi overlap > symbol/tf only
    assert 4 not in ids and 5 not in ids    # SHORT and OPEN excluded
    assert res["n_candidates"] == 3
    assert res["matches"][0]["score"] > res["matches"][1]["score"] > res["matches"][2]["score"]
    assert res["matches"][2]["match"]["tag_jaccard"] == 0.0
    agg = res["aggregate"]
    assert agg["n"] == 3 and agg["wins"] == 2 and agg["losses"] == 1


def test_k_limits_and_aggregate_follows(db):
    p = similar.Proposed("SOL-USDT-SWAP", "LONG", tags=["Breakout"])
    res = similar.similar_trades(p, db.list_trades(), k=1)
    assert len(res["matches"]) == 1 and res["aggregate"]["n"] == 1


def test_ties_break_by_recency(db):
    # No features given → every candidate scores only on symbol; ids 1 and 2 tie, newest first.
    p = similar.Proposed("SOL-USDT-SWAP", "LONG")
    ids = [m["id"] for m in similar.similar_trades(p, db.list_trades())["matches"]]
    assert ids[:2] == [2, 1] and ids[2] == 3


def test_empty_journal():
    d = JournalDB(":memory:")
    res = similar.similar_trades(similar.Proposed("SOL-USDT-SWAP", "LONG"), d.list_trades())
    assert res["matches"] == [] and res["aggregate"]["n"] == 0
    d.close()
