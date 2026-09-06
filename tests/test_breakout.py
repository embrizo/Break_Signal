import numpy as np

from break_signal.core.engine import Engine
from break_signal.core.params import Params

from .helpers import append_bar, descending_resistance


def _engine():
    return Engine(Params(), tf_seconds=86_400, symbol="SOL-USDT-SWAP",
                  exchange="OKX", tf_label="1D")


def _break_bar(c):
    """A decisive bullish candle that closes above the descending line."""
    last = len(c)  # index the appended bar will have
    line_val = 100.0 + (-0.2) * last
    return append_bar(
        c,
        open_=line_val + 0.2,
        high=line_val + 5.5,
        low=line_val - 0.5,
        close=line_val + 5.0,   # well above line + ATR buffer
        volume=300.0,           # ~3x the 100 baseline
    )


def test_clean_break_fires_one_up_signal():
    c = _break_bar(descending_resistance())
    res = _engine().evaluate(c)
    ups = [s for s in res.signals if s.event == "break_up"]
    assert len(ups) == 1
    sig = ups[0]
    assert sig.side == "resistance"
    assert sig.symbol == "SOL-USDT-SWAP" and sig.tf == "1D"
    assert sig.touches >= 3
    assert sig.atr_dist > 0


def test_broken_line_does_not_refire():
    c = _break_bar(descending_resistance())
    eng = _engine()
    first = eng.evaluate(c)
    broken = {s.line_id for s in first.signals}
    assert broken
    # feed the broken id back in — the line must not be re-selected or re-fire
    again = eng.evaluate(c, broken_ids=broken)
    assert [s for s in again.signals if s.line_id in broken] == []


def test_volume_filter_blocks_low_volume_break():
    c = descending_resistance()
    last = len(c)
    line_val = 100.0 + (-0.2) * last
    c = append_bar(
        c,
        open_=line_val + 0.2, high=line_val + 5.5, low=line_val - 0.5,
        close=line_val + 5.0,
        volume=100.0,  # equal to baseline -> below 1.5x threshold
    )
    res = _engine().evaluate(c)
    assert [s for s in res.signals if s.event == "break_up"] == []


def test_doji_body_filter_blocks_indecisive_break():
    c = descending_resistance()
    last = len(c)
    line_val = 100.0 + (-0.2) * last
    # closes just above the line but with a tiny body inside a huge range
    c = append_bar(
        c,
        open_=line_val + 4.6, high=line_val + 10.0, low=line_val - 5.0,
        close=line_val + 5.0,   # body 0.4 vs range 15 -> < 50%
        volume=300.0,
    )
    res = _engine().evaluate(c)
    assert [s for s in res.signals if s.event == "break_up"] == []


def test_no_signal_when_close_stays_below_line():
    c = descending_resistance()
    last = len(c)
    line_val = 100.0 + (-0.2) * last
    c = append_bar(
        c,
        open_=line_val - 3.0, high=line_val - 0.1, low=line_val - 5.0,
        close=line_val - 1.0,   # still under the line
        volume=300.0,
    )
    res = _engine().evaluate(c)
    assert res.signals == []
