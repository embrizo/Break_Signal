# Break Signal

Automated trendline detection & breakout alerts for **OKX perpetual futures**.
It finds valid support/resistance trendlines with no manual drawing, watches
every candle *close*, and pushes a high-conviction breakout alert (with a chart)
to **Telegram** and **Discord**.

Two implementations of the same algorithm:

- **Phase 1 — TradingView Pine indicator** (`pine/break_signal.pine`): draws the
  lines and fires alerts inside TradingView. Best for eyeballing/tuning the rules.
- **Phase 2 — Python watcher service** (`src/break_signal/`): runs 24/7 (built for
  a Raspberry Pi 5), scans multiple symbols/timeframes with no alert cap, renders a
  chart image, and fans out to Telegram + Discord.

See [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) for the full algorithm spec,
architecture, and milestones.

## How it works (strict / high-conviction mode)

1. **Fractal pivots** (lookback auto-tuned: 5 on 1D, 8 on 4H).
2. **Pairwise candidate lines** between pivots ≥10 bars apart.
3. **Validity filter** — any candle that *closed* through a line kills it (0 tolerated).
4. **Score** `3×touches + 2×span + 1.5×recency`, require ≥3 touches, keep top 3 per side, dedupe.
5. **Break** on a confirmed close clearing the line by 0.30×ATR, with volume > 1.5× SMA20 and body ≥ 50% of range.

The Python core (`src/break_signal/core/`) is a faithful port of the Pine script,
so both produce the same signals on the same candles.

## Quick start (local)

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml   # then fill in Telegram/Discord secrets
python -m break_signal -c config.yaml
```

`config.yaml` is gitignored — your bot token and webhook URL never get committed.
No exchange API keys are needed; the service only reads public OKX market data and
never places orders.

## Run on a Raspberry Pi 5 (Docker)

```bash
cp config.example.yaml config.yaml   # fill in secrets
docker compose up -d --build
docker compose logs -f
```

The image is `python:3.11-slim-bookworm` (multi-arch) and pulls prebuilt ARM
wheels from piwheels, so the build is minutes, not an hour. State persists in
`./data` (back it with an SSD/USB, not the SD card).

## Backtest the rules

```bash
python -m break_signal.backtest.replay --symbol SOL-USDT-SWAP --tf 1D --limit 500 --out signals.csv
```

Walks a growing window so each bar sees only past data — no look-ahead, same
pivot-confirmation lag as live.

## Tests

```bash
pip install pytest
python -m pytest tests/ -q
```

The core tests (`tests/test_indicators.py`, `test_pivots.py`, `test_trendline.py`,
`test_breakout.py`) need only numpy + pytest and verify the algorithm on synthetic
data with a known trendline.

## Notifications

- **Telegram:** create a bot with [@BotFather](https://t.me/BotFather), grab the
  token, and get your `chat_id` (e.g. via [@userinfobot](https://t.me/userinfobot)).
- **Discord:** channel → *Integrations → Webhooks → New Webhook* → copy the URL.

Both go in `config.yaml`. Channels fail independently — Discord being down never
blocks Telegram.

## Project layout

```
pine/break_signal.pine        TradingView Pine v6 indicator (Phase 1)
src/break_signal/
  core/        pivots, trendline, breakout, engine, indicators, state
  data/        okx_rest (backfill), okx_ws (live stream)
  notify/      telegram, discord
  render/      mplfinance chart snapshot
  backtest/    offline replay -> CSV
  watcher.py   one (symbol, timeframe) worker
  __main__.py  entrypoint
tests/         algorithm tests on synthetic data
```
