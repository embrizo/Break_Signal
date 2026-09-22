"""TradingView → journal: receive the Pine indicator's alert() JSON as signals.

TradingView webhooks POST the alert message as the request body, can't set
headers, and may bundle several breaks from one bar as newline-separated JSON
objects (that is how ``pine/break_signal.pine`` builds ``alertMsg``). The
secret therefore lives in the URL path:

    POST http://<host>:<port>/pine/<secret>      body = alert message

Each object is normalised (Pine ticker ``SOLUSDT.P`` → ``SOL-USDT-SWAP``, period
``240`` → ``4H``, close time → bar open time) and stored with ``source='pine'``.
With ``webhook.notify`` the alert is also pushed through the Telegram/Discord
notifiers with the journal footer — useful when the Pi watcher is not running.
``ingest()`` is pure; the route is mounted on the shared server in ``web.py``.
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from ..config import bar_seconds
from ..core.types import Signal
from .db import JournalDB, iso_to_ms

if TYPE_CHECKING:
    from ..config import Config

log = logging.getLogger(__name__)

_QUOTES = ("USDT", "USDC", "USD", "BTC", "ETH")
_PINE_TF = {"D": "1D", "W": "1W", "M": "1M"}


def normalize_pine_symbol(ticker: str, aliases: dict[str, str] | None = None) -> str:
    """``SOLUSDT.P`` → ``SOL-USDT-SWAP``; ``SOLUSDT`` → ``SOL-USDT``; aliases win."""
    t = ticker.strip().upper()
    for k, v in (aliases or {}).items():
        if k.upper() == t:
            return v
    if "-" in t:
        return t
    perp = t.endswith(".P")
    if perp:
        t = t[:-2]
    for q in _QUOTES:
        if t.endswith(q) and len(t) > len(q):
            base = t[: -len(q)]
            for k, v in (aliases or {}).items():
                if k.upper() == base and perp:
                    return v
            return f"{base}-{q}-SWAP" if perp else f"{base}-{q}"
    return t


def normalize_pine_tf(period: str) -> str:
    """Pine ``timeframe.period`` → OKX bar: 240 → 4H, 60 → 1H, 15 → 15m, D → 1D, 2D → 2D."""
    p = period.strip().upper()
    if p in _PINE_TF:
        return _PINE_TF[p]
    m = re.fullmatch(r"(\d+)([DWM])", p)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    if p.isdigit():
        n = int(p)
        return f"{n // 60}H" if n >= 60 and n % 60 == 0 else f"{n}m"
    return p


def parse_alert_body(text: str) -> list[dict]:
    """One JSON object per line (Pine joins multiple breaks with '\\n'); a JSON
    array or a single object also work. Non-JSON lines are ignored."""
    text = text.strip()
    if not text:
        return []
    try:
        whole = json.loads(text)
        return whole if isinstance(whole, list) else [whole]
    except json.JSONDecodeError:
        pass
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            log.warning("webhook: ignoring non-JSON line: %.80s", line)
    return out


def to_signal(obj: dict, aliases: dict[str, str] | None = None) -> tuple[Signal, int]:
    """Pine payload → ``Signal`` + bar-open timestamp (Pine sends bar CLOSE time)."""
    tf = normalize_pine_tf(str(obj["tf"]))
    close_ms = iso_to_ms(obj["time"])
    try:
        open_ms = close_ms - bar_seconds(tf) * 1000
    except ValueError:
        open_ms = close_ms            # unknown bar (e.g. 1M): keep close time, still unique per bar
    sig = Signal(
        symbol=normalize_pine_symbol(str(obj["symbol"]), aliases),
        exchange=str(obj.get("exchange") or "OKX").upper(),
        tf=tf, event=obj["event"], side=obj["side"],
        price=float(obj["price"]), line=float(obj["line"]),
        atr_dist=float(obj.get("atr_dist") or 0), touches=int(obj.get("touches") or 0),
        age_bars=int(obj.get("age_bars") or 0), vol_ratio=float(obj.get("vol_ratio") or 0),
        rsi=float(obj.get("rsi") or 0),
        time=obj["time"],
        line_id=obj.get("line_id") or f"pine:{obj['side']}:{float(obj['line']):.6g}",
    )
    return sig, open_ms


def ingest(db: JournalDB, text: str, aliases: dict[str, str] | None = None) -> dict:
    """Store every alert in ``text``; idempotent. Returns ids, duplicates and rejects."""
    stored: list[dict] = []
    rejected: list[str] = []
    dup = 0
    for obj in parse_alert_body(text):
        try:
            sig, open_ms = to_signal(obj, aliases)
        except (KeyError, ValueError, TypeError) as e:
            rejected.append(f"{e.__class__.__name__}: {e}")
            continue
        before = db.conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        sid = db.insert_signal(sig, source="pine", candle_ts=open_ms)
        after = db.conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        if after == before:
            dup += 1
        stored.append({"id": sid, "symbol": sig.symbol, "tf": sig.tf, "event": sig.event,
                       "new": after > before, "signal": sig})
    return {"stored": stored, "duplicates": dup, "rejected": rejected}


# ── HTTP handler (mounted by web.py) ────────────────────────────────────────
def make_pine_handler(cfg: "Config", db: JournalDB, notifiers: list[Any]):
    from aiohttp import web

    from .footer import alert_footer
    from ..notify.base import format_message

    secret = cfg.webhook.secret
    aliases = cfg.journal.symbol_aliases

    async def pine(req):
        if not secret or req.match_info["secret"] != secret:
            log.warning("webhook: bad secret from %s", req.remote)
            return web.json_response({"error": "forbidden"}, status=403)
        body = await req.text()
        if len(body) > 64_000:
            return web.json_response({"error": "body too large"}, status=413)
        res = ingest(db, body, aliases)
        pushed = 0
        if cfg.webhook.notify and notifiers:
            for item in res["stored"]:
                if not item["new"]:
                    continue
                footer = alert_footer(db, item["signal"], item["id"]) if cfg.journal.history_footer else None
                text = format_message(item["signal"], footer)
                for n in notifiers:
                    try:
                        await n.send(text, None)
                        pushed += 1
                    except Exception:  # noqa: BLE001
                        log.exception("webhook: notifier %s failed", getattr(n, "name", n))
        out = {"stored": [{k: v for k, v in s.items() if k != "signal"} for s in res["stored"]],
               "duplicates": res["duplicates"], "rejected": res["rejected"], "pushed": pushed}
        if res["stored"]:
            log.info("webhook: %d stored (%d new), %d rejected", len(res["stored"]),
                     sum(s["new"] for s in res["stored"]), len(res["rejected"]))
            return web.json_response(out, status=200)
        # Nothing usable in the body. A 200 here would show up as a green tick in
        # TradingView's alert log while no signal ever reaches the journal, so say so.
        out["error"] = ("no alert stored — the body must be the Pine indicator's alert() JSON: "
                        "one object per line, or a JSON array. Check the alert's Message box.")
        log.warning("webhook: nothing stored from %d bytes (%d rejected) — %.120s",
                    len(body), len(res["rejected"]), body.replace("\n", " ") or "<empty body>")
        return web.json_response(out, status=400)

    return pine
