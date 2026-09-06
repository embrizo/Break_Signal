"""Engine: run the full pipeline for one candle series.

Given a Candles series and Params, compute the active trendlines at the last
bar and any breakout signals there. This is what both the live watcher and the
backtester call, guaranteeing identical logic.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import indicators
from .breakout import check_breaks
from .params import Params
from .pivots import pivot_highs, pivot_lows
from .trendline import build_lines
from .types import Candles, Signal, Trendline


@dataclass
class EngineResult:
    lines: list[Trendline]
    signals: list[Signal]
    atr_last: float


class Engine:
    def __init__(self, params: Params, tf_seconds: int, symbol: str, exchange: str, tf_label: str):
        self.params = params
        self.tf_seconds = tf_seconds
        self.symbol = symbol
        self.exchange = exchange
        self.tf_label = tf_label
        self.pivot_len = params.resolved_pivot_len(tf_seconds)

    def _confirmed_pivots(self, idxs: list[int], last_bar: int) -> list[int]:
        """Keep only confirmed pivots, then trim by age and to max_pivots (newest)."""
        L = self.pivot_len
        p = [i for i in idxs if i <= last_bar - L]
        p = [i for i in p if (last_bar - i) <= self.params.max_age]
        if len(p) > self.params.max_pivots:
            p = p[-self.params.max_pivots :]
        return p

    def evaluate(self, candles: Candles, broken_ids: set[str] | None = None) -> EngineResult:
        """Compute lines + signals at the last bar of ``candles``."""
        broken_ids = broken_ids or set()
        n = len(candles)
        last_bar = n - 1
        atr = indicators.atr(candles.high, candles.low, candles.close, 14)
        vol_sma = indicators.sma(candles.volume, 20)
        rsi = indicators.rsi(candles.close, 14)
        atr_last = float(atr[last_bar]) if np.isfinite(atr[last_bar]) else float("nan")

        ph = self._confirmed_pivots(pivot_highs(candles.high, self.pivot_len), last_bar)
        pl = self._confirmed_pivots(pivot_lows(candles.low, self.pivot_len), last_bar)

        lines = build_lines(
            candles, atr_last, ph, pl, self.params, last_bar, self.pivot_len, broken_ids
        )
        signals = check_breaks(
            candles,
            atr_last,
            float(vol_sma[last_bar]) if np.isfinite(vol_sma[last_bar]) else float("nan"),
            float(rsi[last_bar]) if np.isfinite(rsi[last_bar]) else float("nan"),
            lines,
            self.params,
            last_bar,
            self.symbol,
            self.exchange,
            self.tf_label,
        )
        return EngineResult(lines=lines, signals=signals, atr_last=atr_last)
