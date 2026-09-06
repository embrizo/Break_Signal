"""Entry point: start one watcher per configured (symbol, timeframe)."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from .config import load_config
from .core.state import State
from .notify.base import Notifier
from .notify.discord import DiscordNotifier
from .notify.telegram import TelegramNotifier
from .watcher import Watcher


def build_notifiers(cfg) -> list[Notifier]:
    out: list[Notifier] = []
    tg = cfg.channels.telegram
    if tg.enabled and tg.bot_token and tg.chat_id:
        out.append(TelegramNotifier(tg.bot_token, tg.chat_id))
    dc = cfg.channels.discord
    if dc.enabled and dc.webhook_url:
        out.append(DiscordNotifier(dc.webhook_url))
    return out


async def run(config_path: str) -> None:
    cfg = load_config(config_path)
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("break_signal")

    notifiers = build_notifiers(cfg)
    if not notifiers:
        log.warning("No notifiers configured — breaks will be logged only.")
    else:
        log.info("Notifiers: %s", ", ".join(n.name for n in notifiers))

    state = State(cfg.state_db)
    watchers = [Watcher(cfg, w, state, notifiers) for w in cfg.watches]
    log.info("Starting %d watcher(s)", len(watchers))
    try:
        await asyncio.gather(*(w.run() for w in watchers))
    finally:
        state.close()


def main() -> None:
    ap = argparse.ArgumentParser(prog="break_signal", description="OKX trendline breakout alerts")
    ap.add_argument(
        "-c", "--config",
        default=os.environ.get("BREAK_SIGNAL_CONFIG", "config.yaml"),
        help="path to config.yaml (default: ./config.yaml or $BREAK_SIGNAL_CONFIG)",
    )
    args = ap.parse_args()
    try:
        asyncio.run(run(args.config))
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)


if __name__ == "__main__":
    main()
