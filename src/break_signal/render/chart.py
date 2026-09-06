"""Render a candlestick snapshot with the active trendlines drawn on it.

Returns PNG bytes to attach to Telegram/Discord. Uses a non-interactive
matplotlib backend so it runs headless on a Pi.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone

import matplotlib

matplotlib.use("Agg")  # headless
import mplfinance as mpf  # noqa: E402
import pandas as pd  # noqa: E402

from ..core.types import Candles, Signal, Trendline  # noqa: E402


def render(
    candles: Candles,
    lines: list[Trendline],
    signal: Signal | None,
    title: str,
    bars: int = 120,
) -> bytes:
    n = len(candles)
    start = max(0, n - bars)
    view = candles.slice(start)
    idx = pd.to_datetime(view.ts, unit="ms", utc=True)
    df = pd.DataFrame(
        {
            "Open": view.open,
            "High": view.high,
            "Low": view.low,
            "Close": view.close,
            "Volume": view.volume,
        },
        index=idx,
    )

    # Build line segments in (datetime, price) space over the visible window.
    alines = []
    colors = []
    lines_to_draw = list(lines)
    if signal is not None and signal._line is not None and signal._line not in lines_to_draw:
        lines_to_draw.append(signal._line)
    for t in lines_to_draw:
        x0 = start
        x1 = n - 1
        seg = [
            (_dt(candles.ts[x0]), t.value_at(x0)),
            (_dt(candles.ts[x1]), t.value_at(x1)),
        ]
        alines.append(seg)
        broke = signal is not None and signal._line is not None and signal._line.id == t.id
        colors.append("#ff9800" if broke else ("#f23645" if t.side == "R" else "#089981"))

    kwargs = dict(
        type="candle",
        style="nightclouds",
        volume=True,
        title=title,
        figratio=(16, 9),
        figscale=1.1,
        tight_layout=True,
        returnfig=True,
    )
    if alines:
        kwargs["alines"] = dict(alines=alines, colors=colors, linewidths=1.4, alpha=0.9)

    fig, _ = mpf.plot(df, **kwargs)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    import matplotlib.pyplot as plt

    plt.close(fig)
    return buf.getvalue()


def _dt(ms) -> datetime:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
