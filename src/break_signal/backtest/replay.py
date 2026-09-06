"""Replay the engine bar-by-bar over history and emit a CSV of every signal.

Usage:
    python -m break_signal.backtest.replay --symbol SOL-USDT-SWAP --tf 1D \
        --limit 500 --out signals.csv

This walks a growing window so each bar sees only the data available at the
time — the same information the live watcher has — which faithfully reproduces
the pivot-confirmation lag (no look-ahead / repainting).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys

import aiohttp

from ..config import bar_seconds
from ..core.engine import Engine
from ..core.params import Params
from ..core.types import Candles
from ..data import okx_rest


def replay(candles: Candles, params: Params, tf: str, symbol: str, warmup: int = 60):
    engine = Engine(params, bar_seconds(tf), symbol, "OKX", tf)
    broken: set[str] = set()
    rows = []
    n = len(candles)
    for end in range(warmup, n + 1):
        window = candles.slice(0)  # copy view
        window = Candles(
            ts=window.ts[:end], open=window.open[:end], high=window.high[:end],
            low=window.low[:end], close=window.close[:end], volume=window.volume[:end],
        )
        res = engine.evaluate(window, broken)
        for sig in res.signals:
            if sig.line_id in broken:
                continue
            broken.add(sig.line_id)
            rows.append(sig.to_dict())
    return rows


async def _main(args) -> int:
    async with aiohttp.ClientSession() as session:
        candles = await okx_rest.fetch_candles(session, args.symbol, args.tf, args.limit)
    if len(candles) == 0:
        print("No candles fetched.", file=sys.stderr)
        return 1
    rows = replay(candles, Params(), args.tf, args.symbol)
    if not rows:
        print(f"No signals over {len(candles)} candles of {args.symbol} {args.tf}.")
        return 0
    fields = list(rows[0].keys())
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    ups = sum(1 for r in rows if r["event"] == "break_up")
    dns = len(rows) - ups
    print(
        f"{len(rows)} signals over {len(candles)} candles "
        f"({ups} up / {dns} down) -> {args.out}"
    )
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Backtest the break-signal rules on OKX history")
    ap.add_argument("--symbol", default="SOL-USDT-SWAP")
    ap.add_argument("--tf", default="1D")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--out", default="signals.csv")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
