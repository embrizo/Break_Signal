"""Synthetic candle construction for deterministic algorithm tests."""
from __future__ import annotations

import numpy as np

from break_signal.core.types import Candles

MS_PER_DAY = 86_400_000


def descending_resistance(
    n: int = 66,
    intercept: float = 100.0,
    slope: float = -0.2,
    pivots: tuple[int, ...] = (5, 20, 35),
    base_gap: float = 3.0,
    volume: float = 100.0,
) -> Candles:
    """A series where pivot highs sit exactly on a descending line.

    Non-pivot highs sit ``base_gap`` below the line (so only the pivots touch
    it), candle ranges are tight (realistic ATR), lows descend monotonically
    (no support pivots), and every close stays below the line so the line is
    never violated. The caller can then append a breakout bar.
    """
    x = np.arange(n)
    line = intercept + slope * x
    high = line - base_gap
    for p in pivots:
        high[p] = line[p]  # exact touch, and a strict local max vs neighbours
    close = high - 0.3          # just under each bar's high, below the line
    open_ = close - 0.2
    lo = np.minimum(open_, close) - 0.3
    vol = np.full(n, volume)
    ts = np.arange(n, dtype=np.int64) * MS_PER_DAY
    return Candles(ts=ts, open=open_, high=high, low=lo, close=close, volume=vol)


def stepped_descending_resistance(
    n: int = 40,
    intercept: float = 100.0,
    slope: float = -0.2,
    first: int = 6,
    step: int = 4,
    count: int = 6,
    base_gap: float = 3.0,
    volume: float = 100.0,
) -> Candles:
    """A descending line whose touches are pivots at L=3 but NOT at L=5.

    Bumps sit exactly on the line every ``step`` bars. Because they are only
    ``step`` (=4) bars apart, each is a strict local high over a ±3 window (fine
    scale) but not over a ±5 window (coarse scale) — a nearer, higher bump falls
    inside the coarse window. So the coarse detector finds too few anchors to
    build the line, and only the fine scale recovers it. Mirrors the real
    consolidation trendline a human draws but the single-scale detector misses.
    """
    x = np.arange(n)
    line = intercept + slope * x
    high = line - base_gap
    for i in range(count):
        p = first + step * i
        high[p] = line[p]           # exact touch on the descending line
    close = high - 0.3
    open_ = close - 0.2
    lo = np.minimum(open_, close) - 0.3
    vol = np.full(n, volume)
    ts = np.arange(n, dtype=np.int64) * MS_PER_DAY
    return Candles(ts=ts, open=open_, high=high, low=lo, close=close, volume=vol)


def append_bar(c: Candles, open_, high, low, close, volume, dt_ms: int = MS_PER_DAY) -> Candles:
    return Candles(
        ts=np.append(c.ts, c.ts[-1] + dt_ms),
        open=np.append(c.open, open_),
        high=np.append(c.high, high),
        low=np.append(c.low, low),
        close=np.append(c.close, close),
        volume=np.append(c.volume, volume),
    )
