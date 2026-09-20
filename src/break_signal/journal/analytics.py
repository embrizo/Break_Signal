"""Deterministic journal metrics.

This is the ONLY place numbers are produced. The AI coach receives these as
tool results and interprets them; it never computes. Every aggregate carries
``n`` so small samples are visible to whoever reads it.

Conventions
-----------
- R-multiple is signed by direction: ``(exit-entry)/(entry-sl)`` for LONG,
  ``(entry-exit)/(sl-entry)`` for SHORT. Requires entry, sl and exit.
- ``BE`` (break-even) when ``|R| < BE_THRESHOLD``; the user may override
  the outcome at close time (partial fills etc.) but R itself is never edited.
- ``win_rate = wins / n`` where ``n`` counts every closed trade (BE included).
- Profit factor is in R: ``sum(R > 0) / |sum(R < 0)|``.
- Drawdown is measured on the cumulative-R equity curve, trades ordered by
  ``closed_ts``.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from datetime import datetime, timezone

from .models import Trade

BE_THRESHOLD = 0.1

_PERIOD_RE = re.compile(r"^(\d+)([dwmy])$", re.IGNORECASE)
_PERIOD_DAYS = {"d": 1, "w": 7, "m": 30, "y": 365}


# ── per-trade arithmetic ─────────────────────────────────────────────────────
def r_multiple(direction: str, entry: float | None, sl: float | None, exit_: float | None) -> float | None:
    """Signed R. ``None`` when any leg is missing or the stop equals the entry."""
    if entry is None or sl is None or exit_ is None:
        return None
    risk = entry - sl if direction == "LONG" else sl - entry
    if risk == 0:
        return None
    gain = exit_ - entry if direction == "LONG" else entry - exit_
    return gain / risk


def planned_rr(direction: str, entry: float | None, sl: float | None, tp: float | None) -> float | None:
    """Reward:risk implied by the plan (TP vs SL). Same sign convention as R."""
    return r_multiple(direction, entry, sl, tp)


def pnl_amount(
    direction: str,
    entry: float | None,
    exit_: float | None,
    position_size: float | None,
    risk_amount: float | None,
    r: float | None,
    fees: float | None,
) -> float | None:
    """Account-currency PnL. Prefers ``position_size × price move``; falls back
    to ``R × risk_amount``. ``None`` if neither is known."""
    gross: float | None = None
    if position_size is not None and entry is not None and exit_ is not None:
        move = exit_ - entry if direction == "LONG" else entry - exit_
        gross = move * position_size
    elif risk_amount is not None and r is not None:
        gross = r * risk_amount
    if gross is None:
        return None
    return gross - (fees or 0.0)


def derive_outcome(r: float | None) -> str | None:
    if r is None:
        return None
    if r > BE_THRESHOLD:
        return "WIN"
    if r < -BE_THRESHOLD:
        return "LOSS"
    return "BE"


def session_of(ts_ms: int | None) -> str | None:
    """Rough UTC session bucket: ASIA 00–08, LONDON 08–13, NY 13–22, ASIA 22–24."""
    if ts_ms is None:
        return None
    hour = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).hour
    if 8 <= hour < 13:
        return "LONDON"
    if 13 <= hour < 22:
        return "NY"
    return "ASIA"


def rsi_band(rsi: float | None) -> str | None:
    if rsi is None:
        return None
    if rsi < 30:
        return "<30"
    if rsi < 50:
        return "30-50"
    if rsi < 70:
        return "50-70"
    return ">70"


def atr_dist_band(d: float | None) -> str | None:
    if d is None:
        return None
    if d < 0.5:
        return "<0.5"
    if d < 1.0:
        return "0.5-1"
    return ">1"


# ── period helpers ───────────────────────────────────────────────────────────
def period_to_since(period: str | None, now_ms: int | None = None) -> int | None:
    """``"30d" | "12w" | "6m" | "1y" | "all" | None`` → epoch-ms lower bound."""
    if not period or period.lower() == "all":
        return None
    m = _PERIOD_RE.match(period.strip())
    if not m:
        raise ValueError(f"Bad period '{period}' (use e.g. 30d, 12w, 6m, 1y, all)")
    days = int(m.group(1)) * _PERIOD_DAYS[m.group(2).lower()]
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    return now - days * 86_400_000


# ── aggregates ───────────────────────────────────────────────────────────────
def closed(trades: list[Trade]) -> list[Trade]:
    """Closed trades that have an R value, oldest close first."""
    out = [t for t in trades if t.status == "CLOSED" and t.r_multiple is not None]
    return sorted(out, key=lambda t: (t.closed_ts or 0, t.id))


def _round(x: float | None, nd: int = 3) -> float | None:
    return None if x is None else round(x, nd)


def summarize(trades: list[Trade]) -> dict:
    """Core numbers for a set of trades. Always includes ``n``."""
    ts = closed(trades)
    n = len(ts)
    rs = [t.r_multiple for t in ts]  # type: ignore[misc]
    wins = [r for r in rs if r > BE_THRESHOLD]
    losses = [r for r in rs if r < -BE_THRESHOLD]
    be = n - len(wins) - len(losses)
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    pf: float | None
    if gross_loss > 0:
        pf = gross_win / gross_loss
    else:
        pf = None if gross_win == 0 else float("inf")
    pnl = [t.pnl_amount for t in ts if t.pnl_amount is not None]
    curve = equity_curve(ts)
    return {
        "n": n,
        "wins": len(wins),
        "losses": len(losses),
        "be": be,
        "win_rate": _round(len(wins) / n) if n else None,
        "avg_r": _round(sum(rs) / n) if n else None,          # expectancy in R
        "expectancy_r": _round(sum(rs) / n) if n else None,
        "total_r": _round(sum(rs)) if n else None,
        "avg_win_r": _round(sum(wins) / len(wins)) if wins else None,
        "avg_loss_r": _round(sum(losses) / len(losses)) if losses else None,
        "profit_factor": _round(pf) if pf not in (None, float("inf")) else pf,
        "max_drawdown_r": _round(max_drawdown(curve)) if n else None,
        "streaks": streaks(ts),
        "total_pnl": _round(sum(pnl), 2) if pnl else None,
        "pnl_n": len(pnl),
    }


def equity_curve(trades: list[Trade]) -> list[dict]:
    """Cumulative R after each closed trade: ``[{ts, trade_id, r, cum_r}]``."""
    cum = 0.0
    out = []
    for t in closed(trades):
        cum += t.r_multiple  # type: ignore[operator]
        out.append({"ts": t.closed_ts, "trade_id": t.id, "r": round(t.r_multiple, 3),  # type: ignore[arg-type]
                    "cum_r": round(cum, 3)})
    return out


def max_drawdown(curve: list[dict]) -> float:
    """Largest peak-to-trough fall in cumulative R (0 if never below a prior peak)."""
    peak = 0.0
    dd = 0.0
    for p in curve:
        peak = max(peak, p["cum_r"])
        dd = max(dd, peak - p["cum_r"])
    return dd


def streaks(trades: list[Trade]) -> dict:
    """Longest win / loss runs and the run currently in progress (BE breaks a run)."""
    best_w = best_l = cur = 0
    cur_kind: str | None = None
    for t in closed(trades):
        kind = derive_outcome(t.r_multiple)
        if kind == cur_kind and kind in ("WIN", "LOSS"):
            cur += 1
        elif kind in ("WIN", "LOSS"):
            cur_kind, cur = kind, 1
        else:
            cur_kind, cur = None, 0
        if cur_kind == "WIN":
            best_w = max(best_w, cur)
        elif cur_kind == "LOSS":
            best_l = max(best_l, cur)
    return {"max_win": best_w, "max_loss": best_l,
            "current": cur, "current_kind": cur_kind}


def group_stats(trades: list[Trade], key) -> dict[str, dict]:
    """``summarize()`` per bucket, where ``key(trade)`` yields one or more
    bucket labels (a str, or an iterable of str). ``None`` labels are skipped.
    Sorted by n desc, then label."""
    buckets: dict[str, list[Trade]] = defaultdict(list)
    for t in closed(trades):
        labels = key(t)
        if labels is None:
            continue
        if isinstance(labels, str):
            labels = [labels]
        for lab in labels:
            if lab is not None:
                buckets[lab].append(t)
    out = {lab: summarize(ts) for lab, ts in buckets.items()}
    return dict(sorted(out.items(), key=lambda kv: (-kv[1]["n"], kv[0])))


def tag_stats(trades: list[Trade], phase: str | None = None) -> dict[str, dict]:
    """Per-tag performance. ``phase`` = ENTRY | EXIT | None (both)."""
    def key(t: Trade):
        if phase == "ENTRY":
            return t.entry_tags
        if phase == "EXIT":
            return t.exit_tags
        return t.tags
    return group_stats(trades, key)


def feature_stats(trades: list[Trade]) -> dict[str, dict[str, dict]]:
    """Performance by timeframe, direction, session, RSI band, ATR-distance band,
    and — for signal-linked trades — signal side / event."""
    return {
        "tf": group_stats(trades, lambda t: t.tf),
        "direction": group_stats(trades, lambda t: t.direction),
        "session": group_stats(trades, lambda t: t.ctx_session),
        "rsi_band": group_stats(trades, lambda t: rsi_band(t.ctx_rsi)),
        "atr_dist_band": group_stats(trades, lambda t: atr_dist_band(t.ctx_atr_dist)),
        "signal_side": group_stats(trades, lambda t: t.signal.side if t.signal else None),
        "signal_event": group_stats(trades, lambda t: t.signal.event if t.signal else None),
        "signal_linked": group_stats(trades, lambda t: "linked" if t.signal_id else "discretionary"),
    }
