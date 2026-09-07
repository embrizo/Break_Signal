# Claude handoff — Break Signal

**Last Updated:** 2026-09-07
**Workspace:** `Break_Signal`
**Primary Language/Runtime:** Python 3.11+ (asyncio); Pine Script v6 (Phase 1)

Read this first in any new session on this project. Update it at the end of every session
that does real work.

---

## Status: the `data/` layer is written and verified live (2026-09-07)

The previously-missing `src/break_signal/data/` package has been written and
smoke-tested against real OKX data. The service and backtest now import and run.
Verified this session:

- `python -m pytest tests/ -q` → 20 passed.
- Live REST backfill → 500 SOL-USDT-SWAP 1D candles, oldest-first, no NaNs.
- `python -m break_signal.backtest.replay ... --limit 500` → 12 signals
  (4 up / 8 down) written to CSV. **Full data→core→signal path works.**

Still **not** exercised live: the WebSocket stream (`okx_ws.stream_closed_candles`)
and the notify layer — no live break has fired to Telegram/Discord yet, and the
service has not run against the WS endpoint. That is now the frontier (M5).

---

## What this project is

Automated trendline detection and breakout alert system for `OKX:SOLUSDT.P` (perpetual futures) on 1D and 4H timeframes. It auto-draws valid support/resistance trendlines from fractal pivots, then pushes high-conviction breakout alerts to Telegram and Discord when price closes through a line. No trading — read-only public market data only.

Three phases: Pine Script indicator on TradingView (Phase 1) → Python watcher service on Raspberry Pi 5 (Phase 2) → optional web dashboard (Phase 3, not started).

Full spec: [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) — algorithm (§2), Pine details (§3), Python service design (§4), milestones (§6), risks (§7), locked decisions (§8).

## Architecture summary

- **Pine indicator** — [pine/break_signal.pine](pine/break_signal.pine): whole algorithm in one Pine v6 file; draws lines, fires JSON `alert()`. Complete.
- **`core/`** — pure algorithm, no I/O, fully tested:
  - [indicators.py](src/break_signal/core/indicators.py) — Pine-matching RMA / ATR / RSI / SMA
  - [pivots.py](src/break_signal/core/pivots.py) — fractal pivots
  - [trendline.py](src/break_signal/core/trendline.py) — build / validate / score lines
  - [breakout.py](src/break_signal/core/breakout.py) — confirmed-close break test + filters
  - [engine.py](src/break_signal/core/engine.py) — ties pivots→lines→breaks per bar
  - [state.py](src/break_signal/core/state.py) — SQLite/WAL dedupe of sent alerts
  - `params.py`, `types.py` — config dataclasses + `Candle`/`Trendline` models
- **`data/`** — written 2026-09-07: [okx_rest.py](src/break_signal/data/okx_rest.py) (paged backfill: `/candles` then `/history-candles` with `after`, confirmed bars only, reversed to oldest-first) + [okx_ws.py](src/break_signal/data/okx_ws.py) (live `wss://ws.okx.com:8443/ws/v5/business` stream, text `ping`/`pong` heartbeat after 20s idle, reconnect w/ backoff, yields only `confirm=="1"` candles). REST verified live; WS not yet run live. Override hosts via `OKX_REST_URL` / `OKX_WS_URL` env for geo-block fallback.
- **`notify/`** — [base.py](src/break_signal/notify/base.py) protocol + [telegram.py](src/break_signal/notify/telegram.py) + [discord.py](src/break_signal/notify/discord.py); failures isolated per channel.
- **`render/`** — [chart.py](src/break_signal/render/chart.py): mplfinance snapshot with lines drawn.
- **`backtest/`** — [replay.py](src/break_signal/backtest/replay.py): growing-window replay → CSV (imports `data/`, so currently broken).
- **[watcher.py](src/break_signal/watcher.py)** — one (symbol, timeframe) async worker; **[__main__.py](src/break_signal/__main__.py)** — entrypoint / asyncio runner.

## Locked decisions

- **Exchange:** OKX V5 public API, `instId=SOL-USDT-SWAP` (NOT Binance — plan §4 predates this and still says Binance in places; OKX is correct per §8).
- **Timeframes:** 1D and 4H.
- **Quality mode:** strict — `atrBreak=0.30`, `minTouches=3`, `maxViolations=0`, volume + body filters ON.
- **Notifications:** Telegram Bot API + Discord webhook, failures isolated.
- **Hosting:** Raspberry Pi 5, ARM64 Docker, SQLite WAL mode.
- **No API keys** for market data (public endpoints). Telegram token + Discord webhook go in `config.yaml` (gitignored).

## Key implementation notes (don't re-litigate)

- **Validity walk stops at `last_bar - 1`.** In [trendline.py](src/break_signal/core/trendline.py) `_build_side` walks through `last_bar - 1`, NOT `last_bar` like the literal Pine loop. The Python engine rebuilds lines every bar in one pass; including the current bar would let a breaking close invalidate the line before the break block sees it. Walking to `last_bar-1` reproduces Pine's *effective* behaviour. **If Pine and Python ever disagree on a break bar, look here first.**
- **Line id** is keyed on pivot open-time (ms): `f"{side}:{ts_a}:{ts_b}"`, not bar index — stable across restarts. (Pine uses `bar_index`; intentional divergence.)
- **OKX candles are newest-first**; reverse before processing. Last array element is `"1"` when the bar is confirmed — only act on confirmed bars. History beyond ~100 bars: `/api/v5/market/history-candles`.

## Quick run commands

```bash
# Core algorithm tests — pass today, need only numpy + pytest
python -m pytest tests/ -q

# Service (BROKEN until data/ exists)
pip install -r requirements.txt
cp config.example.yaml config.yaml    # fill in Telegram/Discord secrets
python -m break_signal -c config.yaml

# Backtest replay (BROKEN until data/ exists)
python -m break_signal.backtest.replay --symbol SOL-USDT-SWAP --tf 1D --limit 500 --out signals.csv

# Deploy (Pi 5)
docker compose up -d --build
```

## Current state

**Repo:** https://github.com/embrizo/Break_Signal — `main`, 4 commits, working tree clean.

**Milestone status (corrected):**

| # | Milestone | Status |
|---|---|---|
| M1 | Pine indicator draws lines | Done (not visually verified on TradingView by user) |
| M2 | Pine break alert fires (user verifies) | Waiting — user must load it |
| M3 | Rules tuned | Blocked on M2 |
| M4 | Python core + tests reproduce Pine lines | Done — 20 tests pass on synthetic data |
| M5 | Telegram+Discord alerts live from Pi 5 | In progress — data layer done, REST verified; WS + notify not yet run live |
| M6 | Backtest report | Replay runs on live data (12 signals/500 bars); multi-symbol hit-rate *report* not yet produced |
| M7 | Multi-symbol + 24/7 Docker deploy | Docker files exist; not deployed |

**What's real vs. claimed:** `core/`, `data/`, `notify/`, `render/`, `backtest/replay.py`, tests, Docker files, config example all exist. `data/` (written 2026-09-07) is committed-pending — **check `git status`; it may be uncommitted.** REST path verified live; WS + notify paths not yet exercised.

## Environment notes

- **This machine is new to the project** (`C:\Users\Pattapon\...`, Windows 11, PowerShell). Dependency state here is unverified. The old handoff referenced a different machine (`C:\Users\embri\...`, hermes-agent venv) with only numpy+pytest installed.
- Core tests run with just numpy + pytest. `aiohttp`, `websockets`, `pandas`, `mplfinance`, `matplotlib` are needed for the full service — confirm they're installed before running anything beyond `tests/`.

## Next steps

1. **Commit the `data/` package** if `git status` shows it untracked (it was written 2026-09-07 but may not be committed yet).
2. **Live WS smoke test** — run `python -m break_signal.data.okx_ws`-style check or start the watcher and confirm `stream_closed_candles` actually receives a confirmed candle from `wss://ws.okx.com:8443/ws/v5/business`. This is the one code path not yet exercised live.
3. **Create `config.yaml`** with real Telegram token + chat_id + Discord webhook; run `python -m break_signal -c config.yaml` and confirm a real break fires to both channels (validates notify + render end to end). Completes M5.
4. **User action: load Pine indicator on TradingView** — verify auto lines match the reference screenshot; tune `pivotLen`/`atrBreak` (M2/M3). Copy winning tuning into `config.example.yaml` `params:` for parity.
5. **Deploy to Pi 5** — `docker compose up -d --build`; point `./data` (state dir) at an SSD/USB.
6. **M6 backtest report** — extend `replay.py` output into a hit-rate summary over ~12 months across SOL + BTC + ETH to check the strict defaults don't overfit SOL.
7. **Optional Phase 3** — FastAPI + TradingView Lightweight Charts dashboard.

## Session log

### Session 3 — 2026-09-07
- Ran `/handoff` on a fresh machine. **Found the `data/` package was missing** — never on disk, never committed, yet imported by `watcher.py` and `backtest/replay.py`, so both entry points crashed at import. Wrote `ANTIGRAVITY_HANDOFF.md` and rewrote this file.
- **Wrote the `data/` package** (`__init__.py`, `okx_rest.py`, `okx_ws.py`) matching the existing call-site signatures. Verified live: 20 tests pass, REST backfill returns 500 clean SOL 1D candles, and `replay.py` produces 12 signals end to end on real OKX data. WS + notify paths still un-run live. `data/` may still be uncommitted — see Next steps #1.

### Session 2 — 2026-09-06
- Built Phase 2 Python service: `core/`, `notify/`, `render/`, `backtest/replay.py`, `watcher.py`, `__main__.py`, Docker files, config example, 20 passing core tests. Rewrote README.
- Documented the `last_bar-1` validity-walk decision and the pivot-timestamp line id.
- (In hindsight: the `data/` layer described in the commit was not actually written.)

### Session 1 — 2026-09-06
- User provided a TradingView SOLUSDT 1D screenshot with hand-drawn trendlines and asked for an auto-trendline breakout alert system.
- Decided: TradingView Pine first (prove rules visually), then Python service on Pi 5 for multi-symbol + Telegram/Discord.
- Wrote `IMPLEMENTATION_PLAN.md` and `pine/break_signal.pine` (complete Pine v6 indicator).
- User locked decisions: OKX futures, 1D+4H, Telegram+Discord, strict quality, Pi 5 hosting.
- `.gitignore` protects `config.yaml`, `.env`, `state.db`. Pushed to GitHub.

## How to resume in a new session

1. Read this file and the ⚠️ section at the top.
2. Also read `ANTIGRAVITY_HANDOFF.md` if another tool worked here.
3. Skim recent commits for anything landed after this file was last updated.
4. Pick up at Next steps #1 (write `data/`) unless the user says otherwise.
5. Update this file's session log and state before ending the session.
