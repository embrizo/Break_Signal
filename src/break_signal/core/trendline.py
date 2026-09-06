"""Build, validate, score and select trendlines.

Direct port of the Pine functions ``buildSide`` / ``isDup`` / ``selectTop``.
Everything is evaluated at ``last_bar`` (the current confirmed candle) using the
single ATR value at that bar, exactly like the indicator.
"""
from __future__ import annotations

import numpy as np

from .params import Params
from .types import Candles, Trendline


def _build_side(
    candles: Candles,
    atr_last: float,
    pivots: list[int],
    side: str,
    params: Params,
    last_bar: int,
    pivot_len: int,
) -> list[Trendline]:
    """Generate every valid candidate line for one side (R from highs, S from lows)."""
    out: list[Trendline] = []
    n = len(pivots)
    if n < 2 or not np.isfinite(atr_last) or atr_last <= 0:
        return out

    price = candles.high if side == "R" else candles.low
    close = candles.close

    for i in range(n - 1):
        ax = pivots[i]
        ay = float(price[ax])
        for j in range(i + 1, n):
            bx = pivots[j]
            by = float(price[bx])
            if bx - ax < params.min_bars:
                continue
            slope = (by - ay) / (bx - ax)
            lv_now = ay + slope * (last_bar - ax)
            # prune: the line must live near current price
            if abs(lv_now - float(close[last_bar])) > params.max_dist * atr_last:
                continue

            viol = 0
            touches = 0
            last_touch = -9999
            k_from = max(ax, last_bar - params.look_max)
            # Validity/touches are assessed through the PRIOR close. The current
            # bar's close is evaluated only as a potential break (in breakout.py).
            # This reproduces Pine's effective behaviour, where lines persist
            # between rebuilds and the breaking close is tested against them
            # rather than invalidating them first.
            for k in range(k_from, last_bar):
                lv = ay + slope * (k - ax)
                c = float(close[k])
                ext = float(price[k])
                # violation = a CLOSE through the line (wicks are forgiven)
                if side == "R":
                    if c > lv + params.atr_valid * atr_last:
                        viol += 1
                else:
                    if c < lv - params.atr_valid * atr_last:
                        viol += 1
                if viol > params.max_violations:
                    break
                # touch = extreme kisses the line, at most one per pivot-window cluster
                if abs(ext - lv) <= params.atr_touch * atr_last and (k - last_touch) >= pivot_len:
                    touches += 1
                    last_touch = k

            if viol <= params.max_violations and touches >= params.min_touches:
                span = min((last_bar - ax) / 200.0, 1.0)
                recency = 1.0 / (1.0 + (last_bar - bx))
                score = 3.0 * touches + 2.0 * span + 1.5 * recency
                out.append(
                    Trendline(
                        side=side,
                        ax=ax,
                        ay=ay,
                        bx=bx,
                        by=by,
                        slope=slope,
                        touches=touches,
                        score=score,
                        ts_a=int(candles.ts[ax]),
                        ts_b=int(candles.ts[bx]),
                    )
                )
    return out


def _is_dup(a: Trendline, b: Trendline, atr_last: float, last_bar: int) -> bool:
    """Two lines are the same if they overlap now and 50 bars ago (Pine ``isDup``)."""
    d_now = abs(a.value_at(last_bar) - b.value_at(last_bar))
    d_old = abs(a.value_at(last_bar - 50) - b.value_at(last_bar - 50))
    return d_now <= 0.5 * atr_last and d_old <= 1.0 * atr_last


def _select_top(
    cands: list[Trendline],
    params: Params,
    atr_last: float,
    last_bar: int,
    broken_ids: set[str],
) -> list[Trendline]:
    """Greedy pick highest-scoring, skipping duplicates and already-broken lines."""
    sel: list[Trendline] = []
    pool = sorted(cands, key=lambda t: t.score, reverse=True)
    for best in pool:
        if len(sel) >= params.max_lines:
            break
        if best.id in broken_ids:
            continue
        if any(_is_dup(best, s, atr_last, last_bar) for s in sel):
            continue
        sel.append(best)
    return sel


def build_lines(
    candles: Candles,
    atr_last: float,
    pivot_highs: list[int],
    pivot_lows: list[int],
    params: Params,
    last_bar: int,
    pivot_len: int,
    broken_ids: set[str] | None = None,
) -> list[Trendline]:
    """Return the active resistance + support lines at ``last_bar``."""
    broken_ids = broken_ids or set()
    res = _build_side(candles, atr_last, pivot_highs, "R", params, last_bar, pivot_len)
    sup = _build_side(candles, atr_last, pivot_lows, "S", params, last_bar, pivot_len)
    return (
        _select_top(res, params, atr_last, last_bar, broken_ids)
        + _select_top(sup, params, atr_last, last_bar, broken_ids)
    )
