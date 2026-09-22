"""J6: TradingView Pine alert() webhook → journal signals."""
import asyncio
import json

import pytest

from break_signal.config import Config, Watch
from break_signal.journal import webhook as W
from break_signal.journal.db import JournalDB, iso_to_ms

PINE = ('{"symbol":"SOLUSDT.P","exchange":"OKX","tf":"240","event":"break_up","side":"resistance",'
        '"price":231.5,"line":229.8,"atr_dist":0.35,"touches":4,"age_bars":51,"vol_ratio":1.9,"rsi":61.3,'
        '"time":"2026-09-20T04:00:00Z"}')
PINE2 = PINE.replace('"side":"resistance"', '"side":"support"').replace('"event":"break_up"', '"event":"break_down"') \
            .replace('"line":229.8', '"line":220.1')


@pytest.mark.parametrize("ticker,expected", [
    ("SOLUSDT.P", "SOL-USDT-SWAP"), ("solusdt.p", "SOL-USDT-SWAP"), ("BTCUSD.P", "BTC-USD-SWAP"),
    ("ETHUSDC", "ETH-USDC"), ("SOL-USDT-SWAP", "SOL-USDT-SWAP"), ("XYZ", "XYZ"),
])
def test_symbol_normalisation(ticker, expected):
    assert W.normalize_pine_symbol(ticker) == expected


def test_symbol_aliases_win():
    assert W.normalize_pine_symbol("SOLUSDT.P", {"SOLUSDT.P": "SOL-USDT-SWAP-ALIAS"}) == "SOL-USDT-SWAP-ALIAS"
    assert W.normalize_pine_symbol("SOLUSDT.P", {"SOL": "SOL-USDT-SWAP"}) == "SOL-USDT-SWAP"


@pytest.mark.parametrize("period,expected", [
    ("240", "4H"), ("60", "1H"), ("720", "12H"), ("15", "15m"), ("1", "1m"), ("90", "90m"),
    ("D", "1D"), ("W", "1W"), ("M", "1M"), ("2D", "2D"), ("4H", "4H"),
])
def test_tf_normalisation(period, expected):
    assert W.normalize_pine_tf(period) == expected


def test_parse_body_forms():
    assert len(W.parse_alert_body(PINE)) == 1
    assert len(W.parse_alert_body(PINE + "\n" + PINE2)) == 2           # newline-joined, as Pine emits
    assert len(W.parse_alert_body("[" + PINE + "," + PINE2 + "]")) == 2
    assert W.parse_alert_body("hello\n" + PINE + "\n\n") and len(W.parse_alert_body("hello\n" + PINE)) == 1
    assert W.parse_alert_body("") == []


def test_to_signal_open_time_and_line_id():
    sig, open_ms = W.to_signal(json.loads(PINE))
    assert sig.symbol == "SOL-USDT-SWAP" and sig.tf == "4H" and sig.exchange == "OKX"
    assert open_ms == iso_to_ms("2026-09-20T00:00:00Z")                 # close 04:00 → open 00:00 for 4H
    assert sig.line_id == "pine:resistance:229.8"
    sig_m, open_m = W.to_signal({**json.loads(PINE), "tf": "M"})
    assert sig_m.tf == "1M" and open_m == iso_to_ms("2026-09-20T04:00:00Z")   # unknown bar: close time kept


def test_ingest_idempotent_and_rejects():
    db = JournalDB(":memory:")
    r = W.ingest(db, PINE + "\n" + PINE2)
    assert [s["new"] for s in r["stored"]] == [True, True] and r["duplicates"] == 0
    r2 = W.ingest(db, PINE)
    assert r2["stored"][0]["new"] is False and r2["duplicates"] == 1
    assert len(db.list_signals()) == 2
    s = db.get_signal(r["stored"][0]["id"])
    assert s.source == "pine" and s.rsi == 61.3 and s.candle_ts == iso_to_ms("2026-09-20T00:00:00Z")
    r3 = W.ingest(db, '{"symbol":"SOLUSDT.P","tf":"240"}')
    assert r3["stored"] == [] and r3["rejected"] and "KeyError" in r3["rejected"][0]
    db.close()


# ── HTTP ─────────────────────────────────────────────────────────────────────
class _Cap:
    name = "cap"

    def __init__(self):
        self.sent = []

    async def send(self, text, image=None):
        self.sent.append(text)


def _run(coro):
    return asyncio.run(coro)


def test_http_endpoint_secret_and_notify():
    from aiohttp.test_utils import TestClient, TestServer
    from break_signal.journal import web as WEB

    cfg = Config(watches=[Watch(symbol="SOL-USDT-SWAP", timeframe="4H")],
                 webhook={"enabled": True, "secret": "s3cret", "notify": True})
    db = JournalDB(":memory:")
    cap = _Cap()
    app = WEB.build_app(cfg, db, [cap])

    async def go():
        async with TestClient(TestServer(app)) as c:
            r = await c.get("/health")
            assert r.status == 200 and (await r.json()) == {"ok": True, "signals": 0, "trades": 0}
            assert (await c.get("/")).status == 404                          # web.enabled false → no dashboard
            r = await c.post("/pine/wrong", data=PINE)
            assert r.status == 403
            r = await c.post("/pine/s3cret", data=PINE + "\n" + PINE2)
            body = await r.json()
            assert r.status == 200 and len(body["stored"]) == 2 and body["pushed"] == 2
            assert "signal" not in body["stored"][0]
            assert len(cap.sent) == 2 and "RESISTANCE BREAK" in cap.sent[0] and "📒" in cap.sent[0]
            assert f"--signal {body['stored'][0]['id']}" in cap.sent[0]
            r = await c.post("/pine/s3cret", data=PINE)                  # duplicate: stored, not pushed again
            body = await r.json()
            assert body["duplicates"] == 1 and body["pushed"] == 0 and len(cap.sent) == 2
            # a body that isn't the alert JSON at all (default TradingView message,
            # plain text): nothing is stored, so it must NOT look like success
            r = await c.post("/pine/s3cret", data="not json at all")
            body = await r.json()
            assert r.status == 400 and body["stored"] == [] and "alert() JSON" in body["error"]
            assert (await c.post("/pine/s3cret", data="")).status == 400       # empty message
            r = await c.post("/pine/s3cret", data='{"symbol":"X"}')            # JSON, missing fields
            body = await r.json()
            assert r.status == 400 and "KeyError" in body["rejected"][0]
            r = await c.post("/pine/s3cret", data="x" * 70_000)
            assert r.status == 413
    _run(go())
    assert len(db.list_signals()) == 2
    db.close()


def test_empty_secret_mounts_no_webhook_route():
    from aiohttp.test_utils import TestClient, TestServer
    from break_signal.journal import web as WEB

    cfg = Config(watches=[Watch(symbol="SOL-USDT-SWAP", timeframe="4H")], webhook={"enabled": True})
    db = JournalDB(":memory:")
    app = WEB.build_app(cfg, db, [])

    async def go():
        async with TestClient(TestServer(app)) as c:
            assert (await c.post("/pine/", data=PINE)).status == 404
            assert (await c.post("/pine/anything", data=PINE)).status == 404
    _run(go())
    assert db.list_signals() == []
    db.close()
