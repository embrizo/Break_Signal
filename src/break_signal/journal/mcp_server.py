"""MCP server (stdio) exposing the journal tools to Claude Code.

Registered by ``.mcp.json`` at the repo root. Never print to stdout — that is
the MCP transport; logs go to stderr.

Resolution order for the journal path: ``$JOURNAL_DB`` → ``journal.db`` in
``$BREAK_SIGNAL_CONFIG`` / ``./config.yaml`` → ``data/journal.db``.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from ..config import Config, load_config
from .db import JournalDB
from .tools import Tools

logging.basicConfig(stream=sys.stderr, level=os.environ.get("JOURNAL_LOG", "WARNING"))
log = logging.getLogger("journal.mcp")


def _build_tools() -> Tools:
    cfg: Config | None = None
    cfg_path = Path(os.environ.get("BREAK_SIGNAL_CONFIG", "config.yaml"))
    if cfg_path.exists():
        try:
            cfg = load_config(cfg_path)
        except Exception as e:  # noqa: BLE001
            log.warning("config %s not loaded: %s", cfg_path, e)
    db_path = os.environ.get("JOURNAL_DB") or (cfg.journal.db if cfg else "data/journal.db")
    account = cfg.journal.account_size if cfg else None
    log.info("journal db: %s", db_path)
    return Tools(JournalDB(db_path, account_size=account), cfg)


mcp = FastMCP(
    "journal",
    instructions=(
        "Trading journal + market snapshot for one trader. FACT tools return stored or live "
        "data; CALC tools return deterministic numbers from analytics.py — quote them verbatim "
        "and always show n. WRITE tools change the journal; confirm with the user first. "
        "Never compute metrics yourself; never tell the user to buy or sell."
    ),
)
T = _build_tools()


# ── FACT ────────────────────────────────────────────────────────────────────
@mcp.tool()
def journal_recent_signals(symbol: str | None = None, tf: str | None = None,
                           source: str | None = None, limit: int = 20) -> list[dict]:
    """FACT: breakout alerts stored by the watcher / backtest, newest first.
    source = live | backtest | pine."""
    return T.recent_signals(symbol, tf, source, limit)


@mcp.tool()
def journal_search_trades(symbol: str | None = None, tf: str | None = None,
                          direction: str | None = None, status: str | None = None,
                          outcome: str | None = None, tags: list[str] | None = None,
                          period: str | None = None, signal_linked: bool | None = None,
                          limit: int = 50) -> dict:
    """FACT: trades matching the filters (newest first). status OPEN|CLOSED|SKIPPED;
    outcome WIN|LOSS|BE; tags = all required; period like 30d/6m/1y/all."""
    return T.search_trades(symbol, tf, direction, status, outcome, tags, period, signal_linked, limit)


@mcp.tool()
def journal_get_trade(trade_id: int) -> dict:
    """FACT: one trade in full — plan, outcome, reasons, tags, events, screenshots,
    linked signal, stored rule violations."""
    return T.get_trade(trade_id)


@mcp.tool()
def journal_list_tags() -> list[dict]:
    """FACT: the tag word bank (name + category)."""
    return T.list_tags()


@mcp.tool()
def journal_list_rules() -> list[dict]:
    """FACT: the trader's structured rules and whether each is enabled."""
    return T.list_rules()


# ── CALC ────────────────────────────────────────────────────────────────────
@mcp.tool()
def journal_stats(period: str = "all", symbol: str | None = None, tf: str | None = None,
                  direction: str | None = None, tags: list[str] | None = None) -> dict:
    """CALC: win_rate, profit_factor, avg_r (expectancy), max_drawdown_r, streaks —
    over CLOSED trades matching the filters. Every figure comes with n; n < 5 is a small sample."""
    return T.stats(period, symbol, tf, direction, tags)


@mcp.tool()
def journal_tag_stats(period: str = "all", phase: str | None = None) -> dict:
    """CALC: per-tag n / wins / losses / win_rate / avg_r / profit_factor.
    phase ENTRY (setup & psychology at entry) | EXIT | null for both."""
    return T.tag_stats(period, phase)


@mcp.tool()
def journal_feature_stats(period: str = "all") -> dict:
    """CALC: performance bucketed by tf, direction, session, RSI band, ATR-distance band,
    signal side/event and linked-vs-discretionary."""
    return T.feature_stats(period)


@mcp.tool()
def journal_equity_curve(period: str = "all") -> list[dict]:
    """CALC: cumulative R after each closed trade, oldest first."""
    return T.equity_curve(period)


@mcp.tool()
def journal_similar_trades(symbol: str, direction: str, tf: str | None = None,
                           side: str | None = None, event: str | None = None,
                           tags: list[str] | None = None, rsi: float | None = None,
                           atr_dist: float | None = None, k: int = 8) -> dict:
    """CALC: the k past trades most similar to a proposed setup (deterministic feature
    match: tags, signal side/event, symbol, tf, RSI ±8, ATR-distance band) with their
    outcomes, reasons and an aggregate. side resistance|support; event break_up|break_down."""
    return T.similar_trades(symbol, direction, tf, side, event, tags, rsi, atr_dist, k)


@mcp.tool()
def journal_rule_check(proposed: dict) -> dict:
    """CALC: which enabled rules a proposed trade would violate. proposed keys:
    symbol, direction, tf, entry_price, sl_price, tp_price, risk_pct, tags, ctx_rsi."""
    return T.rule_check(proposed)


@mcp.tool()
def journal_review_context(trade_id: int) -> dict:
    """FACT+CALC: everything for a post-trade review — the trade, rule check, stats on the
    same setup, its most similar trades, and entry-tag stats."""
    return T.review_context(trade_id)


@mcp.tool()
async def market_snapshot(symbol: str, tf: str, bars: int = 300) -> dict:
    """FACT (live OKX candles) + CALC (Break Signal engine): price, ATR, RSI, volume ratio,
    active support/resistance lines with distance in ATR and %, nearest levels, and whether
    a breakout fired on the last confirmed bar. tf = 1D | 4H | 1H ..."""
    return await T.market_snapshot(symbol, tf, bars)


# ── WRITE (Claude Code's permission prompt guards these) ────────────────────
@mcp.tool()
def journal_add_trade(symbol: str, direction: str, entry_price: float | None = None,
                      sl_price: float | None = None, tp_price: float | None = None,
                      tf: str | None = None, tags: list[str] | None = None,
                      entry_reason: str | None = None, position_size: float | None = None,
                      risk_amount: float | None = None, risk_pct: float | None = None,
                      leverage: float | None = None, fees: float | None = None,
                      confidence: int | None = None, emotion_before: str | None = None,
                      notes: str | None = None, signal_id: int | None = None,
                      auto_link: bool = True) -> dict:
    """WRITE: log an OPEN trade. Only pass what the user actually said — everything else
    stays null. Auto-links to a matching alert within the last 3 bars unless signal_id is given."""
    return T.add_trade(symbol, direction, entry_price, sl_price, tp_price, tf, tags, entry_reason,
                       position_size, risk_amount, risk_pct, leverage, fees, confidence,
                       emotion_before, notes, signal_id, auto_link)


@mcp.tool()
def journal_close_trade(trade_id: int, exit_price: float, outcome: str | None = None,
                        exit_reason: str | None = None, tags: list[str] | None = None,
                        fees: float | None = None, emotion_after: str | None = None) -> dict:
    """WRITE: close a trade with the exit price, the user's reason/lesson and exit tags.
    R and PnL are computed; outcome defaults to the sign of R unless the user states it."""
    return T.close_trade(trade_id, exit_price, outcome, exit_reason, tags, fees, emotion_after)


@mcp.tool()
def journal_skip_signal(signal_id: int, reason: str | None = None,
                        tags: list[str] | None = None) -> dict:
    """WRITE: record that the user deliberately passed on an alert, and why."""
    return T.skip_signal(signal_id, reason, tags)


@mcp.tool()
def journal_add_event(trade_id: int, type: str, data: dict | None = None) -> dict:
    """WRITE: log a mid-trade event: sl_moved | tp_moved | partial_close | added | note.
    data e.g. {"from": 225, "to": 222}."""
    return T.add_event(trade_id, type, data)


@mcp.tool()
def journal_add_tag(name: str, category: str = "OTHER") -> dict:
    """WRITE: add a tag to the word bank. category SETUP | PSYCH | EXIT | MISTAKE | OTHER."""
    return T.add_tag(name, category)


@mcp.tool()
def journal_tag_trade(trade_id: int, tags: list[str], phase: str = "ENTRY") -> dict:
    """WRITE: attach tags to an existing trade. phase ENTRY | EXIT."""
    return T.tag_trade(trade_id, tags, phase)


@mcp.tool()
def journal_add_screenshot(trade_id: int, phase: str, path: str) -> dict:
    """WRITE: register a chart screenshot file for a trade. phase PRE | POST."""
    return T.add_screenshot(trade_id, phase, path)


@mcp.tool()
def journal_update_trade(trade_id: int, notes: str | None = None, confidence: int | None = None,
                         emotion_before: str | None = None, emotion_after: str | None = None,
                         sl_price: float | None = None, tp_price: float | None = None,
                         entry_reason: str | None = None) -> dict:
    """WRITE: edit fields on an existing trade (only the ones given)."""
    fields = {k: v for k, v in dict(notes=notes, confidence=confidence, emotion_before=emotion_before,
                                    emotion_after=emotion_after, sl_price=sl_price, tp_price=tp_price,
                                    entry_reason=entry_reason).items() if v is not None}
    return T.update_trade(trade_id, **fields)


@mcp.tool()
def journal_add_rule(name: str, condition: dict, severity: str = "high") -> dict:
    """WRITE: add a structured rule. condition = {"field": ..., "op": ..., "value": ...};
    ops <= < >= > == != in not_in has_tag not_has_tag is_true is_false."""
    return T.add_rule(name, condition, severity)


@mcp.tool()
def journal_set_rule_enabled(rule_id: int, enabled: bool) -> dict:
    """WRITE: enable or disable a rule."""
    return T.set_rule_enabled(rule_id, enabled)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
