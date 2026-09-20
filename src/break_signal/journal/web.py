"""One aiohttp server for the journal's HTTP surface.

- ``/``                 read-only dashboard (Lightweight Charts): candles, live
                        trendlines, alert and trade markers, stats, memories
- ``/api/...``          JSON the dashboard reads (also handy for scripts)
- ``/pine/<secret>``    TradingView webhook (see ``webhook.py``)
- ``/health``

The dashboard has NO auth — it is meant for the LAN. Do not port-forward it;
only the webhook path is safe to expose, and only because of its secret.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import analytics, memory
from .db import JournalDB
from .tools import Tools, json_safe, trade_dict

if TYPE_CHECKING:
    from ..config import Config

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


def build_app(cfg: "Config", db: JournalDB, notifiers: list[Any], tools: Tools | None = None):
    from aiohttp import web

    tools = tools or Tools(db, cfg)
    app = web.Application()

    async def health(_req):
        return web.json_response({"ok": True,
                                  "signals": db.conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0],
                                  "trades": db.conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]})

    app.router.add_get("/health", health)

    if cfg.webhook.enabled and cfg.webhook.secret:
        from .webhook import make_pine_handler
        app.router.add_post("/pine/{secret}", make_pine_handler(cfg, db, notifiers))

    if cfg.web.enabled and cfg.web.dashboard:
        _add_dashboard(app, cfg, db, tools)
    return app


def _add_dashboard(app, cfg: "Config", db: JournalDB, tools: Tools) -> None:
    from aiohttp import web

    def _int(req, name, default):
        try:
            return int(req.query.get(name, default))
        except ValueError:
            return default

    def respond(data, status: int = 200):
        # Every payload goes through json_safe: Python's json module would happily
        # emit `Infinity`/`NaN`, which browsers refuse to parse.
        return web.json_response(json_safe(data), status=status)

    async def index(_req):
        return web.FileResponse(STATIC / "dashboard.html")

    async def api_config(_req):
        return respond({
            "watches": [{"symbol": w.symbol, "tf": w.timeframe} for w in cfg.watches],
            "aliases": cfg.journal.symbol_aliases,
        })

    async def api_summary(_req):
        trades = db.list_trades()
        since30 = analytics.period_to_since("30d")
        return respond({
            "all_time": analytics.summarize(trades),
            "last_30d": analytics.summarize([t for t in trades if (t.opened_ts or 0) >= (since30 or 0)]),
            "tag_stats_entry": analytics.tag_stats(trades, "ENTRY"),
            "by_tf": analytics.feature_stats(trades)["tf"],
            "open_trades": [trade_dict(t) for t in trades if t.status == "OPEN"],
            "equity_curve": analytics.equity_curve(trades),
            "memories": memory.list_memories(db),
        })

    async def api_trades(req):
        return respond(tools.search_trades(
            symbol=req.query.get("symbol") or None, tf=req.query.get("tf") or None,
            status=req.query.get("status") or None, limit=_int(req, "limit", 50)))

    async def api_signals(req):
        return respond(tools.recent_signals(
            req.query.get("symbol") or None, req.query.get("tf") or None,
            req.query.get("source") or None, _int(req, "limit", 50)))

    async def api_chart(req):
        """Candles + engine lines + markers for one (symbol, tf). Needs OKX."""
        symbol = tools._sym(req.query.get("symbol", "SOL"))
        tf = req.query.get("tf", "1D")
        bars = min(_int(req, "bars", 300), 1000)
        try:
            import aiohttp
            from ..data import okx_rest
            async with aiohttp.ClientSession() as session:
                candles = await okx_rest.fetch_candles(session, symbol, tf, bars)
        except Exception as e:  # noqa: BLE001
            return respond({"symbol": symbol, "tf": tf,
                            "error": f"OKX fetch failed: {e.__class__.__name__}: {e}"}, status=502)
        from .tools import snapshot_from_candles
        snap = snapshot_from_candles(symbol, tf, candles, tools.params)
        ohlc = [{"time": int(candles.ts[i]) // 1000, "open": float(candles.open[i]), "high": float(candles.high[i]),
                 "low": float(candles.low[i]), "close": float(candles.close[i]), "volume": float(candles.volume[i])}
                for i in range(len(candles))]
        first_ts = int(candles.ts[0])
        signals = [s.to_dict() for s in db.list_signals(symbol=symbol, tf=tf, since=first_ts, limit=500)]
        trades = [trade_dict(t) for t in db.list_trades(symbol=symbol, tf=tf, since=first_ts)]
        return respond({"symbol": symbol, "tf": tf, "candles": ohlc, "snapshot": snap,
                        "signals": signals, "trades": trades})

    app.router.add_get("/", index)
    app.router.add_get("/api/config", api_config)
    app.router.add_get("/api/summary", api_summary)
    app.router.add_get("/api/trades", api_trades)
    app.router.add_get("/api/signals", api_signals)
    app.router.add_get("/api/chart", api_chart)


async def serve(cfg: "Config", db: JournalDB, notifiers: list[Any], tools: Tools | None = None) -> None:
    """Background task: run the HTTP server until cancelled."""
    from aiohttp import web

    if cfg.webhook.enabled and not cfg.webhook.secret:
        log.error("webhook.enabled but webhook.secret is empty — webhook route NOT registered")
    runner = web.AppRunner(build_app(cfg, db, notifiers, tools))
    await runner.setup()
    site = web.TCPSite(runner, cfg.web.host, cfg.web.port)
    await site.start()
    log.info("web: http://%s:%d/  (dashboard %s, webhook %s)", cfg.web.host, cfg.web.port,
             "on" if cfg.web.enabled and cfg.web.dashboard else "off",
             "on" if cfg.webhook.enabled and cfg.webhook.secret else "off")
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()
