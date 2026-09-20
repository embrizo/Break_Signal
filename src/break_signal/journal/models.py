"""Dataclasses mirroring the journal tables. Plain data, no behaviour beyond to_dict."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

# Closed enumerations used by the schema CHECK constraints.
DIRECTIONS = ("LONG", "SHORT")
STATUSES = ("OPEN", "CLOSED", "SKIPPED")
OUTCOMES = ("WIN", "LOSS", "BE")
TAG_CATEGORIES = ("SETUP", "PSYCH", "EXIT", "MISTAKE", "OTHER")
TAG_PHASES = ("ENTRY", "EXIT")
SESSIONS = ("ASIA", "LONDON", "NY")


@dataclass
class SignalRow:
    """One breakout the watcher (or a backtest replay) emitted."""

    id: int
    source: str            # live | backtest | pine
    symbol: str
    exchange: str
    tf: str
    event: str             # break_up | break_down
    side: str              # resistance | support
    line_id: str
    price: float
    line_price: float
    atr_dist: float | None
    touches: int | None
    age_bars: int | None
    vol_ratio: float | None
    rsi: float | None
    candle_ts: int
    created_ts: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Tag:
    id: int
    name: str
    category: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TradeEvent:
    id: int
    trade_id: int
    type: str              # sl_moved | tp_moved | partial_close | added | note
    data: dict
    event_ts: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Screenshot:
    id: int
    trade_id: int
    phase: str             # PRE | POST
    path: str
    created_ts: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Trade:
    """A trade row plus its tags, events, screenshots and linked signal.

    ``r_multiple`` / ``pnl_amount`` are cached values computed by
    ``analytics`` at close time; they are never typed in by the user.
    """

    id: int
    symbol: str
    direction: str
    status: str
    created_ts: int
    updated_ts: int
    signal_id: int | None = None
    tf: str | None = None
    entry_price: float | None = None
    sl_price: float | None = None
    tp_price: float | None = None
    exit_price: float | None = None
    position_size: float | None = None
    leverage: float | None = None
    fees: float | None = None
    risk_amount: float | None = None
    risk_pct: float | None = None
    opened_ts: int | None = None
    closed_ts: int | None = None
    outcome: str | None = None
    pnl_amount: float | None = None
    r_multiple: float | None = None
    entry_reason: str | None = None
    exit_reason: str | None = None
    confidence: int | None = None
    emotion_before: str | None = None
    emotion_after: str | None = None
    notes: str | None = None
    ctx_rsi: float | None = None
    ctx_atr: float | None = None
    ctx_atr_dist: float | None = None
    ctx_vol_ratio: float | None = None
    ctx_session: str | None = None
    # hydrated relations
    entry_tags: list[str] = field(default_factory=list)
    exit_tags: list[str] = field(default_factory=list)
    events: list[TradeEvent] = field(default_factory=list)
    screenshots: list[Screenshot] = field(default_factory=list)
    signal: SignalRow | None = None

    @property
    def tags(self) -> list[str]:
        return self.entry_tags + self.exit_tags

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tags"] = self.tags
        return d
