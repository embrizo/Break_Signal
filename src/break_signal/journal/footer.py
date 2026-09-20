"""Alert enrichment: the "your history on this setup" footer. No LLM involved.

Appended to every Telegram/Discord alert once the journal has enough closed
trades on the same kind of setup; below the threshold the footer is only the
logging hint, so the alert is never noisy with n=1 statistics.
"""
from __future__ import annotations

from typing import Any

from . import analytics
from .db import JournalDB

MIN_N = 3


def history_for_signal(db: JournalDB, sig: Any, symbol_only: bool = False) -> dict:
    """``analytics.signal_history`` for a ``core.types.Signal`` (or its dict)."""
    d = sig if isinstance(sig, dict) else sig.to_dict()
    return analytics.signal_history(
        db.list_trades(), tf=d["tf"], event=d["event"], side=d["side"],
        symbol=d["symbol"] if symbol_only else None,
    )


def _fmt_r(x: float | None) -> str:
    return "-" if x is None else f"{x:+.1f}R"


def alert_footer(db: JournalDB, sig: Any, signal_id: int | None = None,
                 min_n: int = MIN_N) -> str:
    """Text block to append to an alert. Always includes the logging hint;
    includes stats only when at least ``min_n`` matching trades are closed."""
    d = sig if isinstance(sig, dict) else sig.to_dict()
    hist = history_for_signal(db, sig)
    direction = hist["direction"].lower()
    lines: list[str] = []
    if hist["n"] >= min_n:
        wr = f"{hist['win_rate'] * 100:.0f}%" if hist["win_rate"] is not None else "-"
        lines.append(f"📒 Your history on {hist['label']}: "
                     f"{hist['n']} trades · {wr} win · {_fmt_r(hist['avg_r'])} avg")
        tag_bits = []
        if hist["best_tag"]:
            tag_bits.append(f"Best tag: {hist['best_tag']['tag']} ({_fmt_r(hist['best_tag']['avg_r'])}, n={hist['best_tag']['n']})")
        if hist["worst_tag"]:
            tag_bits.append(f"Worst tag: {hist['worst_tag']['tag']} ({_fmt_r(hist['worst_tag']['avg_r'])}, n={hist['worst_tag']['n']})")
        if tag_bits:
            lines.append("   " + "  ".join(tag_bits))
    else:
        lines.append(f"📒 Journal: {hist['n']} closed trade(s) on {hist['label']} — "
                     f"stats shown from {min_n}")
    ref = f" --signal {signal_id}" if signal_id is not None else ""
    skip = f", or skip {signal_id} <reason> to record a pass" if signal_id is not None else ""
    lines.append(f"   Log: journal add \"{d['symbol']} {d['tf']} {direction} <entry> sl <sl> tp <tp>\"{ref}{skip}")
    return "\n".join(lines)
