"""Technical indicators matching TradingView Pine's implementations.

Pine's ``ta.atr`` / ``ta.rsi`` use Wilder's RMA (running moving average),
seeded with a simple moving average of the first ``length`` samples. These
functions reproduce that exactly so Python and Pine agree bar for bar.
Undefined leading values are NaN, as in Pine.
"""
from __future__ import annotations

import numpy as np


def sma(src: np.ndarray, length: int) -> np.ndarray:
    """Simple moving average; NaN for the first ``length-1`` bars."""
    src = np.asarray(src, dtype=float)
    n = src.shape[0]
    out = np.full(n, np.nan)
    if n < length:
        return out
    csum = np.cumsum(np.insert(src, 0, 0.0))
    out[length - 1 :] = (csum[length:] - csum[:-length]) / length
    return out


def rma(src: np.ndarray, length: int) -> np.ndarray:
    """Wilder's RMA, seeded with the SMA of the first ``length`` values.

    Mirrors Pine's ``ta.rma``: the first defined value sits at index
    ``length-1`` and equals the mean of ``src[0:length]``; thereafter
    ``rma[i] = alpha*src[i] + (1-alpha)*rma[i-1]`` with ``alpha = 1/length``.
    """
    src = np.asarray(src, dtype=float)
    n = src.shape[0]
    out = np.full(n, np.nan)
    if n < length:
        return out
    alpha = 1.0 / length
    seed = float(np.mean(src[:length]))
    out[length - 1] = seed
    prev = seed
    for i in range(length, n):
        prev = alpha * src[i] + (1.0 - alpha) * prev
        out[i] = prev
    return out


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    n = high.shape[0]
    tr = np.empty(n)
    tr[0] = high[0] - low[0]
    if n > 1:
        hl = high[1:] - low[1:]
        hc = np.abs(high[1:] - close[:-1])
        lc = np.abs(low[1:] - close[:-1])
        tr[1:] = np.maximum.reduce([hl, hc, lc])
    return tr


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int = 14) -> np.ndarray:
    return rma(true_range(high, low, close), length)


def rsi(close: np.ndarray, length: int = 14) -> np.ndarray:
    close = np.asarray(close, dtype=float)
    n = close.shape[0]
    out = np.full(n, np.nan)
    if n < 2:
        return out
    change = np.diff(close, prepend=close[0])
    change[0] = 0.0
    gain = np.where(change > 0, change, 0.0)
    loss = np.where(change < 0, -change, 0.0)
    avg_gain = rma(gain, length)
    avg_loss = rma(loss, length)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        out = 100.0 - 100.0 / (1.0 + rs)
    # avg_loss == 0 -> RSI 100; both zero -> undefined, keep NaN handling sane
    out = np.where(avg_loss == 0, 100.0, out)
    out = np.where(np.isnan(avg_gain), np.nan, out)
    return out
