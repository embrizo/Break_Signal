"""J6: the trendline segments the dashboard draws.

dashboard.html draws each engine line as a two-point series
``[(anchor_ts/1000, anchor_value), (last_ts/1000, value)]`` (see loadChart),
so the segment must reproduce the engine's line at every bar between the
anchor pivot and the latest candle. The fixture line is ``y = 100 - 0.2·x``
with pivot touches at bars 5, 20, 35, one candle per day.
"""
import asyncio

import numpy as np
import pytest
from aiohttp.test_utils import TestClient, TestServer

from break_signal.config import Config, Watch
from break_signal.core.params import Params
from break_signal.core.types import Candles
from break_signal.journal import web as WEB
from break_signal.journal.db import JournalDB
from break_signal.journal.tools import Tools, snapshot_from_candles

from .helpers import MS_PER_DAY, append_bar, descending_resistance

SYM, TF = "SOL-USDT-SWAP", "1D"


def _lines(candles: Candles) -> list[dict]:
    return snapshot_from_candles(SYM, TF, candles, Params())["lines"]


def _segment_value_at(ln: dict, ts_ms: int) -> float:
    """Linear interpolation of the drawn segment at a candle time — what the chart shows."""
    frac = (ts_ms - ln["anchor_ts"]) / (ln["last_ts"] - ln["anchor_ts"])
    return ln["anchor_value"] + frac * (ln["value"] - ln["anchor_value"])


def _mirror(c: Candles, axis: float = 200.0) -> Candles:
    """Flip the series around a price so the resistance line becomes an ascending support."""
    return Candles(ts=c.ts, open=axis - c.open, high=axis - c.low, low=axis - c.high,
                   close=axis - c.close, volume=c.volume)


def _truncate(c: Candles, n: int) -> Candles:
    return Candles(ts=c.ts[:n], open=c.open[:n], high=c.high[:n], low=c.low[:n], close=c.close[:n], volume=c.volume[:n])


def test_segment_endpoints_sit_on_the_fixture_line():
    c = descending_resistance()
    lines = _lines(c)
    assert len(lines) == 1
    ln = lines[0]
    assert ln["side"] == "resistance"
    assert ln["anchor_ts"] == int(c.ts[5])                     # oldest pivot is anchor A
    assert ln["anchor_value"] == pytest.approx(100.0 - 0.2 * 5)
    assert ln["last_ts"] == int(c.ts[-1])                      # segment always extends to the newest candle
    assert ln["value"] == pytest.approx(100.0 - 0.2 * (len(c) - 1))
    assert ln["slope_per_bar"] == pytest.approx(-0.2)


def test_segment_is_collinear_with_the_engine_line():
    c = descending_resistance()
    ln = _lines(c)[0]
    # the two drawn points must be exactly slope·bars apart …
    bars = ln["age_bars"] + ln["span_bars"]                    # anchor A → last bar
    assert ln["value"] == pytest.approx(ln["anchor_value"] + ln["slope_per_bar"] * bars)
    assert bars == (ln["last_ts"] - ln["anchor_ts"]) // MS_PER_DAY
    # … so interpolating the segment at the other pivots lands on their highs (exact touches)
    for pivot in (20, 35):
        assert _segment_value_at(ln, int(c.ts[pivot])) == pytest.approx(float(c.high[pivot]))
    # and every non-pivot high stays strictly below the drawn line (the line was never violated)
    for i in range(5, len(c)):
        assert float(c.high[i]) <= _segment_value_at(ln, int(c.ts[i])) + 1e-9


def test_anchor_is_an_existing_candle_time():
    c = descending_resistance()
    ln = _lines(c)[0]
    times = {int(t) for t in c.ts}
    assert ln["anchor_ts"] in times and ln["last_ts"] in times
    assert ln["anchor_ts"] < ln["last_ts"]
    assert ln["anchor_ts"] % 1000 == 0 and ln["last_ts"] % 1000 == 0     # /1000 in the page gives whole seconds


def test_segment_extends_by_one_bar_after_a_new_candle():
    c0 = descending_resistance()
    last = len(c0)
    lv = 100.0 - 0.2 * last
    c1 = append_bar(c0, open_=lv + 0.2, high=lv + 5.5, low=lv - 0.5, close=lv + 5.0, volume=300.0)
    before, after = _lines(c0)[0], _lines(c1)[0]
    # same line (same anchors), only the drawn end moves
    assert after["line_id"] == before["line_id"]
    assert (after["anchor_ts"], after["anchor_value"]) == (before["anchor_ts"], before["anchor_value"])
    assert after["last_ts"] == before["last_ts"] + MS_PER_DAY
    assert after["value"] == pytest.approx(before["value"] + before["slope_per_bar"])
    assert after["age_bars"] == before["age_bars"] + 1 and after["span_bars"] == before["span_bars"]
    # the breakout bar closed above it: the segment now sits below price
    assert after["dist_pct"] < 0 and before["dist_pct"] > 0


def test_support_line_is_the_mirror_image():
    c = descending_resistance()
    r, s = _lines(c)[0], _lines(_mirror(c))[0]
    assert s["side"] == "support" and s["line_id"].startswith("S:")
    assert (s["anchor_ts"], s["last_ts"]) == (r["anchor_ts"], r["last_ts"])
    assert s["anchor_value"] == pytest.approx(200.0 - r["anchor_value"])
    assert s["value"] == pytest.approx(200.0 - r["value"])
    assert s["slope_per_bar"] == pytest.approx(-r["slope_per_bar"])


def test_lines_sorted_nearest_to_price_first():
    c = descending_resistance()
    # resistance above (from the fixture) + support below (mirror glued under the same closes)
    m = _mirror(c, axis=2 * float(c.close[-1]) - 40)   # puts the mirrored support ~20 below the last close
    both = Candles(ts=c.ts, open=c.open, high=c.high, low=m.low, close=c.close, volume=c.volume)
    lines = _lines(both)
    assert {ln["side"] for ln in lines} == {"resistance", "support"}
    dists = [abs(ln["dist_pct"]) for ln in lines]
    assert dists == sorted(dists)
    for ln in lines:
        assert ln["last_ts"] == int(c.ts[-1])


def test_too_few_candles_gives_no_lines():
    snap = snapshot_from_candles(SYM, TF, _truncate(descending_resistance(), 10), Params())
    assert "lines" not in snap and snap["error"] == "only 10 candles"


def test_chart_endpoint_lines_align_with_served_candles(monkeypatch):
    """What the page receives: line times (ms) must map onto the candles' `time` (s)."""
    cfg = Config(watches=[Watch(symbol=SYM, timeframe=TF)], web={"enabled": True})
    db = JournalDB(":memory:")
    tools = Tools(db, cfg)
    series = descending_resistance()

    async def fake_fetch(session, symbol, tf, limit):
        return series

    from break_signal.data import okx_rest
    monkeypatch.setattr(okx_rest, "fetch_candles", fake_fetch)
    app = WEB.build_app(cfg, db, [], tools)

    async def go():
        async with TestClient(TestServer(app)) as c:
            d = await (await c.get(f"/api/chart?symbol={SYM}&tf={TF}")).json()
            times = [k["time"] for k in d["candles"]]
            assert times == sorted(times) and len(set(times)) == len(times)   # Lightweight Charts requires this
            for ln in d["snapshot"]["lines"]:
                assert ln["anchor_ts"] // 1000 in times and ln["last_ts"] // 1000 == times[-1]
                assert isinstance(ln["anchor_value"], float) and isinstance(ln["value"], float)
            ln = d["snapshot"]["lines"][0]
            assert ln["anchor_ts"] // 1000 == times[5] and ln["anchor_value"] == pytest.approx(99.0)
            assert ln["value"] == pytest.approx(87.0)
            # a bar whose high touched the line must not have its high above the drawn segment
            hi = {k["time"]: k["high"] for k in d["candles"]}
            assert hi[times[20]] == pytest.approx(_segment_value_at(ln, int(series.ts[20])))
    asyncio.run(go())
    db.close()


def test_chart_endpoint_with_short_history_has_no_lines(monkeypatch):
    cfg = Config(watches=[Watch(symbol=SYM, timeframe=TF)], web={"enabled": True})
    db = JournalDB(":memory:")
    tools = Tools(db, cfg)
    short = _truncate(descending_resistance(), 12)

    async def fake_fetch(session, symbol, tf, limit):
        return short

    from break_signal.data import okx_rest
    monkeypatch.setattr(okx_rest, "fetch_candles", fake_fetch)
    app = WEB.build_app(cfg, db, [], tools)

    async def go():
        async with TestClient(TestServer(app)) as c:
            r = await c.get(f"/api/chart?symbol={SYM}&tf={TF}")
            assert r.status == 200                              # candles still render; only the lines are absent
            d = await r.json()
            assert len(d["candles"]) == 12
            assert "lines" not in d["snapshot"] and "error" in d["snapshot"]   # page does `snap.lines || []`
    asyncio.run(go())
    db.close()
