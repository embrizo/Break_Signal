"""Deterministic similar-trade retrieval (no embeddings).

Given a proposed setup, rank the trader's CLOSED trades by feature overlap:

    score = tag Jaccard × 3
          + same signal side/event × 2     (via the linked signal, or inferred)
          + same symbol × 2
          + same timeframe × 1
          + RSI within ±8 × 1
          + same ATR-distance band × 1

Direction is a hard filter (a LONG setup is only compared to LONG trades).
Ties break by recency. Transparent by design: every result carries the
per-feature match breakdown so the coach can cite *why* a trade is similar.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import analytics
from .models import Trade

RSI_WINDOW = 8.0


@dataclass
class Proposed:
    symbol: str
    direction: str
    tf: str | None = None
    side: str | None = None          # resistance | support
    event: str | None = None         # break_up | break_down
    tags: list[str] | None = None
    rsi: float | None = None
    atr_dist: float | None = None

    def __post_init__(self):
        self.direction = self.direction.upper()
        # A long on a resistance break is break_up; fill in whichever is missing.
        if self.event is None and self.side is not None:
            self.event = "break_up" if self.direction == "LONG" else "break_down"
        if self.side is None and self.event is not None:
            self.side = "resistance" if self.event == "break_up" else "support"


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def score(p: Proposed, t: Trade) -> tuple[float, dict]:
    """Score one trade against the proposal; returns (score, breakdown)."""
    ptags = {x.lower() for x in (p.tags or [])}
    ttags = {x.lower() for x in t.entry_tags}
    jac = _jaccard(ptags, ttags)
    t_side = t.signal.side if t.signal else None
    t_event = t.signal.event if t.signal else None
    same_side = bool(p.side and t_side and p.side == t_side) or \
        bool(p.event and t_event and p.event == t_event)
    same_symbol = t.symbol == p.symbol
    same_tf = bool(p.tf and t.tf and p.tf.upper() == t.tf.upper())
    rsi_close = p.rsi is not None and t.ctx_rsi is not None and abs(p.rsi - t.ctx_rsi) <= RSI_WINDOW
    same_band = (p.atr_dist is not None and t.ctx_atr_dist is not None and
                 analytics.atr_dist_band(p.atr_dist) == analytics.atr_dist_band(t.ctx_atr_dist))
    s = jac * 3 + same_side * 2 + same_symbol * 2 + same_tf * 1 + rsi_close * 1 + same_band * 1
    return round(s, 3), {
        "tag_jaccard": round(jac, 3), "shared_tags": sorted(ptags & ttags),
        "same_side_event": same_side, "same_symbol": same_symbol, "same_tf": same_tf,
        "rsi_within_8": rsi_close, "same_atr_band": same_band,
    }


def rank(p: Proposed, trades: list[Trade], k: int = 8) -> list[tuple[Trade, float, dict]]:
    cands = [t for t in analytics.closed(trades) if t.direction == p.direction]
    scored = [(t, *score(p, t)) for t in cands]
    scored.sort(key=lambda x: (-x[1], -(x[0].closed_ts or 0), -x[0].id))
    return scored[:k]


def similar_trades(p: Proposed, trades: list[Trade], k: int = 8) -> dict:
    """Tool-shaped result: ranked matches + aggregate of the matched set."""
    ranked = rank(p, trades, k)
    matches = [
        {
            "id": t.id, "symbol": t.symbol, "tf": t.tf, "direction": t.direction,
            "outcome": t.outcome, "r_multiple": round(t.r_multiple, 3) if t.r_multiple is not None else None,
            "entry_tags": t.entry_tags, "exit_tags": t.exit_tags,
            "entry_reason": t.entry_reason, "exit_reason": t.exit_reason,
            "ctx_rsi": t.ctx_rsi, "ctx_atr_dist": t.ctx_atr_dist,
            "signal": {"side": t.signal.side, "event": t.signal.event} if t.signal else None,
            "closed_ts": t.closed_ts, "score": s, "match": breakdown,
        }
        for t, s, breakdown in ranked
    ]
    return {
        "proposed": p.__dict__,
        "n_candidates": len([t for t in analytics.closed(trades) if t.direction == p.direction]),
        "matches": matches,
        "aggregate": analytics.summarize([t for t, _, _ in ranked]),
        "note": "deterministic feature match; scores are relative, not probabilities",
    }
