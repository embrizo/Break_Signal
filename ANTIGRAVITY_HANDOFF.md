# ANTIGRAVITY_HANDOFF.md — Break Signal

**Last Updated:** 2026-09-07
**Workspace:** `Break_Signal`
**Tech Stack:** Python 3.11+ (asyncio, numpy, pandas, aiohttp, websockets, pydantic, mplfinance) · Pine Script v6 · SQLite (WAL) · Docker (ARM64 / Raspberry Pi 5)

---

## Status — the `data/` layer is written and verified (2026-09-07)

The `src/break_signal/data/` package (previously missing, which broke every
runtime path) has been written and smoke-tested against live OKX data:

- 20 core tests pass.
- REST backfill returns 500 clean SOL-USDT-SWAP 1D candles (oldest-first, no NaNs).
- `backtest/replay.py` runs end to end → 12 signals over 500 real candles.

**Not yet exercised live:** the WebSocket stream (`okx_ws.stream_closed_candles`)
and the notify layer — no live break has fired to Telegram/Discord. That is the
current frontier (M5). The `data/` files may still be uncommitted — check
`git status` (see Section 6, Task 1).

---

## 1. Project Goal & Overview

Automated support/resistance **trendline detection and breakout alerting** for
`OKX:SOLUSDT.P` perpetual futures on the 1D and 4H timeframes. No manual line
drawing: fractal pivots feed pairwise candidate lines, a close-through validity
filter discards broken ones, a touch/span/recency score keeps the best few per
side, and a confirmed close through a line (by an ATR buffer, with volume + body
filters) fires an alert to Telegram and Discord with a rendered chart image.

**It never trades** — public OKX market data only, no API keys, no orders. Built
to run 24/7 on a Raspberry Pi 5. Full specification lives in
[`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md).

Three phases: **Phase 1** TradingView Pine indicator (algorithm proven visually) →
**Phase 2** Python watcher service (this repo's focus) → **Phase 3** optional web
dashboard (not started).

---

## 2. Architecture & Key Components

Two implementations of one algorithm, kept in parity:

- **Phase 1 — Pine indicator** (`pine/break_signal.pine`, Pine v6): the whole
  algorithm in one file — pivots, candidate lines, validity filter, scoring,
  strict break test, JSON `alert()` payload, info table, ghost lines for broken
  trendlines. Complete; not yet visually verified on TradingView by the user.

- **Phase 2 — Python service** (`src/break_signal/`):
  - **`core/`** (pure algorithm, no I/O, fully tested): `indicators.py`
    (Pine-matching RMA/ATR/RSI/SMA), `pivots.py`, `trendline.py`
    (build/validate/score), `breakout.py` (break test + volume/body filters),
    `engine.py` (per-bar orchestration), `state.py` (SQLite/WAL alert dedupe),
    `params.py` + `types.py` (config dataclasses, `Candle`/`Trendline` models).
  - **`data/`** — written 2026-09-07: `okx_rest.py` (paged candle backfill via
    `/candles` + `/history-candles`, confirmed bars only, reversed oldest-first),
    `okx_ws.py` (live WebSocket stream on the business endpoint, text `ping`/`pong`
    heartbeat, reconnect with backoff, yields only confirmed candles). Hosts
    overridable via `OKX_REST_URL` / `OKX_WS_URL`. REST verified live; WS not yet.
  - **`notify/`**: `base.py` (Notifier protocol), `telegram.py` (sendPhoto +
    caption), `discord.py` (multipart webhook). Channel failures are isolated.
  - **`render/chart.py`**: mplfinance snapshot with the active lines drawn.
  - **`backtest/replay.py`**: growing-window replay (no look-ahead) → CSV of signals.
  - **`watcher.py`**: one async worker per (symbol, timeframe) — backfill, then
    stream closed candles, re-run the engine, notify, persist state.
  - **`__main__.py`**: entrypoint; loads `config.yaml`, launches watchers.
  - **`config.py`**: pydantic settings model.

**Data flow:** REST backfill (~500 candles) → compute pivots → build/validate/score
lines → persist. Then on each **confirmed** WebSocket candle close: append, rebuild
lines cheaply, test each active line for a break, and on a new break render a PNG
and fan out to Telegram + Discord, marking the line broken in SQLite so it can't
re-alert (survives restarts).

---

## 3. Directory Layout & Key Files

```
Break_Signal/
├── Claude_HANDOFF.md            # terser, Claude-focused handoff (sibling to this)
├── ANTIGRAVITY_HANDOFF.md       # this file
├── IMPLEMENTATION_PLAN.md       # full spec: algorithm, Pine, service, milestones
├── README.md
├── pyproject.toml               # break-signal v0.2.0, deps, pytest config
├── requirements.txt
├── config.example.yaml          # copy to config.yaml (gitignored) and fill secrets
├── Dockerfile                   # python:3.11-slim-bookworm, piwheels for ARM
├── docker-compose.yml
├── .gitignore                   # excludes config.yaml, .env, state.db, artifacts
├── pine/
│   └── break_signal.pine        # Phase 1 indicator (Pine v6)
├── src/break_signal/
│   ├── __main__.py              # entrypoint / asyncio runner
│   ├── config.py                # pydantic settings
│   ├── watcher.py               # per-(symbol,tf) worker  [imports missing .data]
│   ├── core/                    # pivots, trendline, breakout, engine, indicators,
│   │                            #   state, params, types  (tested, working)
│   ├── data/                    # okx_rest.py, okx_ws.py  (written 2026-09-07)
│   ├── notify/                  # base, telegram, discord
│   ├── render/                  # chart.py (mplfinance)
│   └── backtest/
│       └── replay.py            # offline replay -> CSV  [imports missing .data]
└── tests/                       # conftest, helpers, test_indicators/pivots/
                                 #   trendline/breakout  (20 tests, all pass)
```

---

## 4. Key Execution & Verification Commands

```bash
# --- Verify (works today) ---
python -m pytest tests/ -q          # 20 core tests; needs only numpy + pytest

# --- Install full service deps ---
pip install -r requirements.txt     # aiohttp, websockets, numpy, pandas,
                                     # mplfinance, matplotlib, pydantic, PyYAML

# --- Backtest (works — no secrets needed) ---
python -m break_signal.backtest.replay --symbol SOL-USDT-SWAP --tf 1D --limit 500 --out signals.csv

# --- Run service (imports OK; needs config.yaml with real secrets, WS not yet live-tested) ---
cp config.example.yaml config.yaml  # then add Telegram token + chat_id + Discord webhook
python -m break_signal -c config.yaml

# --- Deploy on Raspberry Pi 5 ---
docker compose up -d --build
docker compose logs -f
```

---

## 5. Current State & Known Invariants

- **Completed / working:** Pine indicator (`pine/break_signal.pine`); Python
  `core/` algorithm with 20 passing tests; `data/` REST + WS layer (REST verified
  live); `backtest/replay.py` runs end to end on real OKX data; `notify/`, `render/`,
  Docker + config scaffolding. Repo: https://github.com/embrizo/Break_Signal, `main`.
- **Remaining:** the live WS + notify path (M5) has not fired a real alert yet;
  the M6 multi-symbol hit-rate *report* is not produced; deploy (M7) not done. The
  `data/` files may still be uncommitted — check `git status`.
- **Invariants (a change can silently break these):**
  - **Pine ↔ Python parity.** Both must produce the same signals on the same
    candles. The subtle rule: the Python validity walk in `trendline._build_side`
    runs to `last_bar - 1`, not `last_bar`, so the current close is tested as a
    *break* against persisted lines rather than invalidating them first. If the
    two implementations ever disagree on a break bar, check this first.
  - **Only act on confirmed candles.** OKX returns candles newest-first and flags
    the last array element `"1"` when the bar is closed. Reverse the array; never
    alert intrabar (the #1 source of false signals).
  - **Line id = `f"{side}:{ts_a}:{ts_b}"`** keyed on pivot open-time in ms (not bar
    index) — stable across restarts so SQLite dedupe holds.
  - **Alert dedupe** keyed by `(symbol, timeframe, line_id)`; a broken line never
    re-alerts, even after a service restart.
  - **No exchange API keys, ever.** Public market data only; the service must never
    place an order.
  - **Channel isolation.** Discord failing must not block Telegram, and vice versa.
- **Known quirks / blockers:**
  - `IMPLEMENTATION_PLAN.md` §4 still describes **Binance** in places; the locked
    decision (§8) is **OKX** — OKX is authoritative.
  - Pine indicator not yet loaded/tuned on TradingView by the user (M2/M3 pending);
    whatever tuning wins must be copied into `config.example.yaml` `params:`.
  - This dev machine (`C:\Users\Pattapon\...`) is new to the project; full-service
    dependency install is unverified here.

---

## 6. Next Steps

- [ ] **Task 1 — commit `data/`** if `git status` shows it untracked (written
      2026-09-07: `__init__.py`, `okx_rest.py`, `okx_ws.py`).
- [ ] **Task 2 — live WS smoke test:** confirm `okx_ws.stream_closed_candles`
      actually receives a confirmed candle from `wss://ws.okx.com:8443/ws/v5/business`
      (the one code path not yet exercised live).
- [ ] **Task 3 — go live (M5):** create `config.yaml` with real Telegram + Discord
      secrets; run the watcher and confirm a real break alerts on both channels
      (validates notify + render end to end).
- [ ] **Task 4 — TradingView verification (user):** load `pine/break_signal.pine`,
      confirm auto lines match the reference screenshot, tune `pivotLen`/`atrBreak`,
      then mirror the tuning into `config.example.yaml`.
- [ ] **Task 5 — deploy to Pi 5:** `docker compose up -d --build`; put the state dir
      on an SSD/USB, not the SD card.
- [ ] **Task 6 — backtest report (M6):** extend `replay.py` into a hit-rate summary
      over ~12 months across SOL + BTC + ETH to check the strict defaults aren't
      overfit to SOL.
- [ ] **Task 7 (optional) — Phase 3 dashboard:** FastAPI + TradingView Lightweight
      Charts.
