import numpy as np

from break_signal.core import indicators
from break_signal.core.engine import Engine
from break_signal.core.params import Params
from break_signal.core.pivots import pivot_highs

from .helpers import descending_resistance


def _engine(params=None):
    # tf 1D -> pivot_len 5 under auto-tune
    return Engine(params or Params(), tf_seconds=86_400, symbol="SOL-USDT-SWAP",
                  exchange="OKX", tf_label="1D")


def test_pivots_land_on_expected_bars():
    c = descending_resistance()
    assert pivot_highs(c.high, 5) == [5, 20, 35]


def test_detects_descending_resistance():
    c = descending_resistance()
    res = _engine().evaluate(c)
    r_lines = [t for t in res.lines if t.side == "R"]
    assert len(r_lines) == 1, "collinear pivots should collapse to one line"
    line = r_lines[0]
    assert line.touches >= 3
    # slope recovered from the two anchor highs
    assert abs(line.slope - (-0.2)) < 1e-6
    # anchors are among the seeded pivots
    assert line.ax in (5, 20) and line.bx in (20, 35)


def test_no_support_line_on_flat_lows():
    c = descending_resistance()
    res = _engine().evaluate(c)
    assert [t for t in res.lines if t.side == "S"] == []


def test_line_value_matches_true_line_at_current_bar():
    c = descending_resistance()
    res = _engine().evaluate(c)
    line = [t for t in res.lines if t.side == "R"][0]
    last = len(c) - 1
    true_val = 100.0 + (-0.2) * last
    assert abs(line.value_at(last) - true_val) < 1e-6


def test_line_id_is_timestamp_stable():
    c = descending_resistance()
    line = [t for t in _engine().evaluate(c).lines if t.side == "R"][0]
    # id keyed on open-time ms, not bar index -> stable across a re-slice/restart
    assert line.id.startswith("R:")
    assert str(int(c.ts[line.ax])) in line.id
