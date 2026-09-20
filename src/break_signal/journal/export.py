"""Journal exports: markdown (readable without any tooling), CSV, JSON.

Used by the CLI ``export`` command and by the nightly backup, which writes a
markdown copy next to the ``.db`` snapshot so the history survives even if
nothing can open SQLite.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

from . import analytics
from .db import now_ms
from .models import Trade

CSV_COLUMNS = [
    "id", "status", "signal_id", "symbol", "tf", "direction", "entry_price", "sl_price",
    "tp_price", "exit_price", "position_size", "risk_amount", "risk_pct", "fees",
    "outcome", "r_multiple", "pnl_amount", "opened_ts", "closed_ts", "ctx_session",
    "confidence", "entry_tags", "exit_tags", "entry_reason", "exit_reason", "notes",
]


def _ts(ms: int | None) -> str:
    if ms is None:
        return "-"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _f(x, nd: int = 2) -> str:
    if x is None:
        return "-"
    if isinstance(x, str):
        return x
    if x == float("inf"):
        return "inf"
    return f"{x:.{nd}f}"


def _pct(x) -> str:
    return "-" if x is None else f"{x * 100:.0f}%"


def summary_block(title: str, s: dict) -> str:
    st = s["streaks"]
    return (
        f"{title}\n"
        f"  n={s['n']}  W/L/BE={s['wins']}/{s['losses']}/{s['be']}  win_rate={_pct(s['win_rate'])}\n"
        f"  avg_R={_f(s['avg_r'])}  total_R={_f(s['total_r'])}  PF={_f(s['profit_factor'])}"
        f"  avg_win={_f(s['avg_win_r'])}R  avg_loss={_f(s['avg_loss_r'])}R  max_DD={_f(s['max_drawdown_r'])}R\n"
        f"  streaks: max_win={st['max_win']} max_loss={st['max_loss']} "
        f"current={st['current']} {st['current_kind'] or ''}"
        + (f"\n  PnL={_f(s['total_pnl'])} (n={s['pnl_n']})" if s["total_pnl"] is not None else "")
    )


def to_json(trades: list[Trade]) -> str:
    return json.dumps([t.to_dict() for t in trades], ensure_ascii=False, indent=2)


def to_csv(trades: list[Trade]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n")
    w.writeheader()
    for t in trades:
        d = t.to_dict()
        d["entry_tags"] = "|".join(t.entry_tags)
        d["exit_tags"] = "|".join(t.exit_tags)
        w.writerow({c: d.get(c) for c in CSV_COLUMNS})
    return buf.getvalue()


def to_markdown(trades: list[Trade], title: str = "Trading journal export") -> str:
    """Oldest first; one section per trade with plan, outcome, tags, reasons, events."""
    trades = sorted(trades, key=lambda t: (t.opened_ts or 0, t.id))
    lines = [f"# {title}", "", f"_{len(trades)} trades, exported {_ts(now_ms())} UTC_", ""]
    lines.append(summary_block("## Summary", analytics.summarize(trades)))
    lines.append("")
    for t in trades:
        lines.append(f"## #{t.id} {t.symbol} {t.tf or ''} {t.direction} — {t.status}"
                     + (f" {t.outcome} {_f(t.r_multiple)}R" if t.outcome else ""))
        lines.append(f"- opened {_ts(t.opened_ts)} · closed {_ts(t.closed_ts)}")
        lines.append(f"- entry {_f(t.entry_price, 4)} · sl {_f(t.sl_price, 4)} · "
                     f"tp {_f(t.tp_price, 4)} · exit {_f(t.exit_price, 4)}")
        if t.signal_id is not None:
            lines.append(f"- signal #{t.signal_id}" + (f" ({t.signal.event} {t.signal.side} @ {_ts(t.signal.candle_ts)})"
                                                      if t.signal else ""))
        if t.entry_tags:
            lines.append(f"- entry tags: {', '.join(t.entry_tags)}")
        if t.exit_tags:
            lines.append(f"- exit tags: {', '.join(t.exit_tags)}")
        if t.entry_reason:
            lines.append(f"- entry reason: {t.entry_reason}")
        if t.exit_reason:
            lines.append(f"- exit reason: {t.exit_reason}")
        if t.notes:
            lines.append(f"- notes: {t.notes}")
        for e in t.events:
            lines.append(f"- event {_ts(e.event_ts)}: {e.type} {e.data}")
        for s in t.screenshots:
            lines.append(f"- screenshot {s.phase}: {s.path}")
        lines.append("")
    return "\n".join(lines)
