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

**What's not built yet:**
- Python service (`src/break_signal/`) — Phase 2
- `config.yaml` template (`config.example.yaml`) — needed before Phase 2
- Docker setup — `Dockerfile` + `docker-compose.yml` for Pi 5
- Tests — parity tests against Pine output on same OKX candles

**Milestone status:**

| # | Milestone | Status |
|---|---|---|
| M1 | Pine indicator draws lines | Done |
| M2 | Pine break alert fires (user verifies on TradingView) | Waiting — user needs to load it |
| M3 | Rules tuned (≤1 false signal per 20 bars over 3 months SOL) | Blocked on M2 |
| M4 | Python core + tests reproduce Pine lines | Not started |
| M5 | Telegram alerts live from Pi 5 | Not started |
| M6 | Backtest report (12 months, hit-rate summary) | Not started |
| M7 | Multi-symbol + 24/7 Docker deploy on Pi 5 | Not started |

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

1. **User action: load Pine indicator on TradingView** — paste `pine/break_signal.pine` into Pine Editor on `OKX:SOLUSDT.P` 1D. Check if auto-drawn lines match the hand-drawn ones from the reference screenshot. Report back what needs tuning (M2/M3).
2. **Build Python core** — `src/break_signal/core/` with `pivots.py`, `trendline.py`, `breakout.py`. Port the exact algorithm from Pine. Write parity tests against known OKX candle fixtures (M4).
3. **OKX data module** — `data/okx_rest.py` (backfill) + `data/okx_ws.py` (live stream). Async, no keys (M4).
4. **Notification module** — `notify/telegram.py` + `notify/discord.py` behind a common `Notifier` protocol. `config.example.yaml` as a template (M5).
5. **Chart rendering** — `render/chart.py` using mplfinance, attach PNG to alerts (M5).
6. **Docker + Pi 5 deploy** — `Dockerfile` (ARM64 `python:3.11-slim-bookworm`), `docker-compose.yml`, WAL-mode SQLite (M7).

## How to resume work in a new session

1. Read this file.
2. Check the project root for other `*_HANDOFF.md` files (other AI tools may have worked on this project too) and read them if present.
3. Skim recent commits for anything that landed after this file was last updated.
4. Pick up at the top of "Next steps" unless the user says otherwise.
5. Update this file's session log and current-state section before ending the session.
