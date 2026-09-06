"""One watcher per (symbol, timeframe): backfill, stream, detect, notify."""
from __future__ import annotations

import asyncio
import logging

import aiohttp
import numpy as np

from .config import Config, Watch, bar_seconds
from .core.engine import Engine
from .core.state import State
from .core.types import Candles, Signal
from .data import okx_rest
from .data.okx_ws import ClosedCandle, stream_closed_candles
from .notify.base import Notifier, format_message

log = logging.getLogger(__name__)


class Watcher:
    def __init__(self, cfg: Config, watch: Watch, state: State, notifiers: list[Notifier]):
        self.cfg = cfg
        self.symbol = watch.symbol
        self.tf = watch.timeframe
        self.state = state
        self.notifiers = notifiers
        self.engine = Engine(
            params=cfg.to_params(),
            tf_seconds=bar_seconds(self.tf),
            symbol=self.symbol,
            exchange="OKX",
            tf_label=self.tf,
        )
        self.candles: Candles | None = None

    async def run(self) -> None:
        async with aiohttp.ClientSession() as session:
            self.candles = await okx_rest.fetch_candles(
                session, self.symbol, self.tf, self.cfg.backfill
            )
        log.info("%s %s: backfilled %d candles", self.symbol, self.tf, len(self.candles))

        # Prime state so historical breaks in the backfill don't fire on startup.
        self._prime_broken()

        async for candle in stream_closed_candles(self.symbol, self.tf):
            try:
                self._append(candle)
                await self._on_close()
            except Exception:  # noqa: BLE001 — never let one candle kill the stream
                log.exception("%s %s: error processing candle", self.symbol, self.tf)

    # ── internals ───────────────────────────────────────────────────────
    def _prime_broken(self) -> None:
        """Run the engine over the backfill and record any already-broken lines,
        so a fresh start does not alert on breaks that happened in the past."""
        assert self.candles is not None
        broken = self.state.broken_ids(self.symbol, self.tf)
        res = self.engine.evaluate(self.candles, broken)
        for sig in res.signals:
            self.state.mark_broken(self.symbol, self.tf, sig.line_id)
        if self.candles.ts.size:
            self.state.set_last_candle_ts(self.symbol, self.tf, int(self.candles.ts[-1]))

    def _append(self, c: ClosedCandle) -> None:
        assert self.candles is not None
        cd = self.candles
        if cd.ts.size and int(cd.ts[-1]) == c.ts:
            # same candle re-sent — replace the last row
            cd.open[-1], cd.high[-1], cd.low[-1] = c.open, c.high, c.low
            cd.close[-1], cd.volume[-1] = c.close, c.volume
            return
        self.candles = Candles(
            ts=np.append(cd.ts, np.int64(c.ts)),
            open=np.append(cd.open, c.open),
            high=np.append(cd.high, c.high),
            low=np.append(cd.low, c.low),
            close=np.append(cd.close, c.close),
            volume=np.append(cd.volume, c.volume),
        )
        # keep memory bounded
        cap = max(self.cfg.backfill, 600)
        if len(self.candles) > cap * 2:
            self.candles = self.candles.slice(len(self.candles) - cap)

    async def _on_close(self) -> None:
        assert self.candles is not None
        last_ts = int(self.candles.ts[-1])
        if self.state.last_candle_ts(self.symbol, self.tf) == last_ts:
            return  # already processed this candle
        broken = self.state.broken_ids(self.symbol, self.tf)
        res = self.engine.evaluate(self.candles, broken)
        for sig in res.signals:
            if self.state.is_broken(self.symbol, self.tf, sig.line_id):
                continue
            await self._dispatch(sig, res.lines)
            self.state.mark_broken(self.symbol, self.tf, sig.line_id)
            self.state.log_alert(
                self.symbol, self.tf, sig.line_id, sig.event, sig.price, int(self.candles.ts[-1])
            )
        self.state.set_last_candle_ts(self.symbol, self.tf, last_ts)

    async def _dispatch(self, sig: Signal, lines) -> None:
        log.info("BREAK %s %s %s @ %.4g", self.symbol, self.tf, sig.event, sig.price)
        text = format_message(sig)
        image = None
        if self.cfg.render_chart:
            try:
                from .render.chart import render

                image = render(
                    self.candles, lines, sig,
                    title=f"{self.symbol} {self.tf} — {sig.event}",
                    bars=self.cfg.chart_bars,
                )
            except Exception:  # noqa: BLE001 — a render failure must not drop the alert
                log.exception("chart render failed; sending text-only")

        # each channel independent: one failure never blocks the others
        results = await asyncio.gather(
            *(n.send(text, image) for n in self.notifiers), return_exceptions=True
        )
        for n, r in zip(self.notifiers, results):
            if isinstance(r, Exception):
                log.error("notifier %s failed: %s", n.name, r)
