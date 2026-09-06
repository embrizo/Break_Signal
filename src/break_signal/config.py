"""Load and validate config.yaml into typed models."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .core.params import Params


class Watch(BaseModel):
    symbol: str
    timeframe: str


class TelegramCfg(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""


class DiscordCfg(BaseModel):
    enabled: bool = False
    webhook_url: str = ""


class Channels(BaseModel):
    telegram: TelegramCfg = Field(default_factory=TelegramCfg)
    discord: DiscordCfg = Field(default_factory=DiscordCfg)


class Config(BaseModel):
    exchange: str = "okx"
    watches: list[Watch]
    params: dict = Field(default_factory=dict)
    backfill: int = 500
    channels: Channels = Field(default_factory=Channels)
    render_chart: bool = True
    chart_bars: int = 120
    state_db: str = "state.db"
    log_level: str = "INFO"

    def to_params(self) -> Params:
        # Only pass keys Params knows about, so extra yaml keys don't crash startup.
        known = {f for f in Params.__dataclass_fields__}  # type: ignore[attr-defined]
        return Params(**{k: v for k, v in self.params.items() if k in known})


def load_config(path: str | Path) -> Config:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Config(**data)


# OKX bar string -> seconds, for pivot auto-tuning.
_BAR_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1H": 3600, "2H": 7200, "4H": 14400, "6H": 21600, "12H": 43200,
    "1D": 86400, "2D": 172800, "3D": 259200, "1W": 604800,
}


def bar_seconds(bar: str) -> int:
    if bar not in _BAR_SECONDS:
        raise ValueError(f"Unknown OKX bar '{bar}'")
    return _BAR_SECONDS[bar]
