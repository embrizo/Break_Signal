"""Breakout detection on the confirmed candle. Port of the Pine break block."""
from __future__ import annotations

import math

import numpy as np

from .params import Params
from .types import Candles, Signal, Trendline, ms_to_iso


def check_breaks(
    candles: Candles,
    atr_last: float,
    vol_sma_last: float,
    rsi_last: float,
    lines: list[Trendline],
    params: Params,
    last_bar: int,
    symbol: str,
    exchange: str,
    tf: str,
) -> list[Signal]:
    """Return a Signal for every active line the current close broke through.

    Only ever call this for a *confirmed* (closed) candle at ``last_bar``.
    """
    signals: list[Signal] = []
    if not lines or not np.isfinite(atr_last) or atr_last <= 0:
        return signals

    buf = params.atr_break * atr_last
    c = float(candles.close[last_bar])
    o = float(candles.open[last_bar])
    h = float(candles.high[last_bar])
    lo = float(candles.low[last_bar])
    c_prev = float(candles.close[last_bar - 1])
    vol = float(candles.volume[last_bar])

    vol_ok = (not params.use_volume) or (
        np.isfinite(vol_sma_last) and vol_sma_last > 0 and vol > vol_sma_last * params.vol_mult
    )
    body_ok = (not params.use_body) or (
        (h - lo) > 0 and abs(c - o) >= (h - lo) * params.body_min
    )
    vol_ratio = vol / vol_sma_last if (np.isfinite(vol_sma_last) and vol_sma_last > 0) else 0.0

    for t in lines:
        lv = t.value_at(last_bar)
        lv_prev = t.value_at(last_bar - 1)
        age = last_bar - t.ax

        up = c > lv + buf
        up_prev = c_prev > lv_prev + buf
        dn = c < lv - buf
        dn_prev = c_prev < lv_prev - buf

        hit_r = t.side == "R" and up and (not params.two_bar or up_prev)
        hit_s = t.side == "S" and dn and (not params.two_bar or dn_prev)

        if (hit_r or hit_s) and vol_ok and body_ok and age >= params.min_bars:
            dist = abs(c - lv) / atr_last
            signals.append(
                Signal(
                    symbol=symbol,
                    exchange=exchange,
                    tf=tf,
                    event="break_up" if hit_r else "break_down",
                    side="resistance" if hit_r else "support",
                    price=c,
                    line=lv,
                    atr_dist=dist,
                    touches=t.touches,
                    age_bars=age,
                    vol_ratio=vol_ratio,
                    rsi=rsi_last if math.isfinite(rsi_last) else 0.0,
                    time=ms_to_iso(int(candles.ts[last_bar])),
                    line_id=t.id,
                    _line=t,
                )
            )
    return signals
