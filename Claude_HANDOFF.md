# Claude handoff — Break Signal

Read this first in any new session on this project. Update it at the end of every session
that does real work.

## What this project is

Automated trendline detection and breakout alert system for `OKX:SOLUSDT.P` (perpetual futures) on 1D and 4H timeframes. The system auto-draws valid support/resistance trendlines using fractal pivots, then pushes high-conviction breakout alerts to Telegram and Discord when price closes through a line.

Three phases: Pine Script indicator on TradingView (Phase 1, done) → Python watcher service on Raspberry Pi 5 (Phase 2, next) → optional web dashboard (Phase 3). No trading — read-only public market data only.

## Locked decisions

- **Exchange:** OKX V5 public API, `instId=SOL-USDT-SWAP`
- **Timeframes:** 1D and 4H
- **Quality mode:** strict — `atrBreak=0.30`, `minTouches=3`, `maxViolations=0`, volume + body filters ON
- **Notifications:** Telegram Bot API + Discord webhook, failures isolated
- **Hosting:** Raspberry Pi 5, ARM64 Docker, SQLite with WAL mode
- **No API keys** for market data (public endpoints). Telegram bot token and Discord webhook URL go in `config.yaml` which is gitignored.

## Current state

**Repo:** https://github.com/embrizo/Break_Signal — `main` branch, 2 commits.

**What's built:**
- `.gitignore` — excludes `config.yaml`, `.env`, `state.db`, Python/Docker artifacts
- `IMPLEMENTATION_PLAN.md` — full spec: algorithm (§2), Pine details (§3), Python service design (§4), milestones (§6), risks (§7), locked decisions (§8)
- `pine/break_signal.pine` — Pine v6 indicator, complete and ready to load

**What's built (Phase 2 — added session 2):**
- Full Python service under `src/break_signal/`:
  - `core/` — `indicators.py` (Pine-matching RMA/ATR/RSI), `pivots.py`, `trendline.py`, `breakout.py`, `engine.py`, `state.py` (SQLite/WAL), `params.py`, `types.py`
  - `data/` — `okx_rest.py` (paged backfill), `okx_ws.py` (live stream, heartbeat, reconnect)
  - `notify/` — `base.py` + `telegram.py` + `discord.py` (independent failures)
  - `render/chart.py` — mplfinance snapshot with lines drawn
  - `backtest/replay.py` — growing-window replay → CSV
  - `watcher.py`, `__main__.py`
- `config.example.yaml`, `pyproject.toml`, `requirements.txt`, `Dockerfile` (arm64 + piwheels), `docker-compose.yml`
- `tests/` — 20 tests, ALL PASSING (indicators, pivots, trendline, breakout)
- README rewritten with full usage

**Key implementation decision (session 2):** the Python validity walk in
`trendline._build_side` runs through `last_bar - 1`, NOT `last_bar` like the literal
Pine loop. Reason: the Python engine rebuilds lines every bar in a single pass, so
including the current bar would let a breaking close invalidate the line before the
break block could see it. Walking to `last_bar-1` reproduces Pine's *effective*
behaviour, where lines persist between rebuilds and the current close is tested as a
break against them. Documented inline. **If Pine and Python signals ever disagree on
a break bar, this is the first place to look.**

**Line id** is keyed on pivot open-time (ms), `f"{side}:{ts_a}:{ts_b}"`, not bar
index — stable across restarts (Pine uses bar_index; intentional divergence).

**What's not built yet:**
- Live smoke test against real OKX (never run — needs network + real candles)
- `config.yaml` with real Telegram/Discord secrets (user must create)
- Deploy to the actual Pi 5
- M3 tuning still pending user's visual check of the Pine lines
- M6 backtest report (the `replay.py` tool exists; the *report* hasn't been produced)

**Milestone status:**

| # | Milestone | Status |
|---|---|---|
| M1 | Pine indicator draws lines | Done |
| M2 | Pine break alert fires (user verifies on TradingView) | Waiting — user needs to load it |
| M3 | Rules tuned (≤1 false signal per 20 bars over 3 months SOL) | Blocked on M2/user |
| M4 | Python core + tests reproduce Pine lines | Done — 20 tests pass on synthetic data |
| M5 | Telegram+Discord alerts live from Pi 5 | Code done; not yet run against live OKX / deployed |
| M6 | Backtest report | Tool built (`replay.py`); report not produced |
| M7 | Multi-symbol + 24/7 Docker deploy on Pi 5 | Docker built; not deployed |

## Algorithm summary

Documented in detail in `IMPLEMENTATION_PLAN.md` §2. The short version:

1. **Fractal pivots** — lookback `L=5` (1D) / `L=8` (4H), rolling buffer of last 10–20 per side.
2. **Pairwise candidate lines** — every pair of pivot highs (resistance) or lows (support) separated by ≥10 bars.
3. **Validity filter** — walk all bars; any close through `line ± 0.10×ATR` kills the line (zero violations allowed in strict mode).
4. **Score** — `3×touches + 2×span + 1.5×recency`, require ≥3 touches, keep top 3 per side, dedupe near-identical lines.
5. **Break trigger** — confirmed close only, must clear line by 0.30×ATR, volume > 1.5× SMA20, body ≥ 50% of range.

## OKX API notes (for Phase 2)

- REST candles: `GET /api/v5/market/candles?instId=SOL-USDT-SWAP&bar=1D` — returns newest-first, reverse before processing.
- History backfill: `/api/v5/market/history-candles` for >100 bars.
- WebSocket: `wss://ws.okx.com:8443/ws/v5/business`, subscribe to `candle1D` / `candle4H`.
- Candle close flag: last array element is `"1"` when the bar is confirmed — only act on confirmed bars.
- No API key needed for public market data.

## Session log

### Session 1 — 2026-09-06

- User provided a TradingView screenshot of SOLUSDT 1D with hand-drawn trendlines (descending resistance from May highs, July–Aug support) and asked for an auto-trendline breakout alert system.
- Decided approach: TradingView Pine first (prove the rules visually), then Python service on Pi 5 for multi-symbol + Telegram/Discord.
- Wrote `IMPLEMENTATION_PLAN.md` with full algorithm spec, three-phase architecture, and milestone table.
- Wrote `pine/break_signal.pine` — Pine v6 indicator implementing the complete algorithm with auto-tuning pivot length by timeframe, JSON alert payload, info table, and ghost lines for broken trendlines.
- User locked decisions: OKX futures, 1D+4H, Telegram+Discord, strict quality, Pi 5 hosting.
- Created `.gitignore` to protect future credentials (`config.yaml`, `.env`, `state.db`).
- Pushed everything to GitHub: https://github.com/embrizo/Break_Signal
- Pine script has NOT been tested on TradingView yet — user needs to load it and verify lines match the reference screenshot.

## Next steps

1. **Live smoke test** — on a machine with network + full deps
   (`pip install -r requirements.txt`), run
   `python -m break_signal.backtest.replay --symbol SOL-USDT-SWAP --tf 1D --limit 500 --out signals.csv`
   to confirm the OKX REST fetch works and see real signals. This is the fastest
   real-data check and needs no secrets.
2. **User action: load Pine indicator on TradingView** — verify the auto lines match
   the reference screenshot; tune `pivotLen`/`atrBreak` (M2/M3). Whatever tuning wins
   must be copied into `config.example.yaml` `params:` so Python stays in parity.
3. **Create `config.yaml`** from the example with real Telegram bot token + chat_id and
   Discord webhook. Run `python -m break_signal -c config.yaml` and wait for a live break.
4. **Deploy to the Pi 5** — `docker compose up -d --build`. Point `./data` at an SSD/USB.
5. **M6 backtest report** — extend `replay.py` output into a hit-rate summary over ~12
   months across SOL + BTC + ETH to validate the strict defaults don't overfit SOL.
6. **Optional Phase 3** — FastAPI + TradingView Lightweight Charts dashboard.

## Environment notes (session 2)

- Dev machine has multiple Python installs; the active one is a hermes-agent venv
  (`C:\Users\embri\AppData\Local\hermes\hermes-agent\venv`). Only `numpy` + `pytest`
  were installed there for the core tests. `aiohttp`, `websockets`, `pandas`,
  `mplfinance`, `matplotlib` are NOT installed locally — the service's data/render/
  notify layers have been compile-checked but not run here. Run them on the Pi or a
  full venv.
- Core tests pass with just numpy + pytest: `python -m pytest tests/ -q` (but the
  `render`/`data`/`notify` modules import heavy deps, so run only the core test files
  if those aren't installed, or install the full requirements first).

## How to resume work in a new session

1. Read this file.
2. Check the project root for other `*_HANDOFF.md` files (other AI tools may have worked on this project too) and read them if present.
3. Skim recent commits for anything that landed after this file was last updated.
4. Pick up at the top of "Next steps" unless the user says otherwise.
5. Update this file's session log and current-state section before ending the session.
