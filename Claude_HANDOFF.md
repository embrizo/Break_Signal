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

The **WebSocket stream is also verified live** (2026-09-07): pointing
`okx_ws.stream_closed_candles` at `candle1m` yielded a confirmed candle in ~13s —
connect → subscribe → parse → confirm-filter → yield all work. The only M5 piece
**not** yet exercised is the **notify layer** (Telegram/Discord), which needs real
secrets — no live break alert has been sent.

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
- **Multi-scale pivots (Session 4).** Detection now merges a coarse (`pivot_len`) and a fine (`pivot_len_fine=3`) fractal scale so consolidation trendlines across minor swings are caught. Merged set is deduped + capped at `max_pivots` so loop bounds are unchanged. Pair building orders anchors explicitly (merged pivots aren't sorted). Toggle: `use_fine_pivots`. Keep Pine `mergeP`/`touchGap` in exact parity with `pivots.merge_pivots` + `engine.touch_gap`.

## Quick run commands

```bash
# Core algorithm tests — pass today, need only numpy + pytest
python -m pytest tests/ -q

# Backtest replay — works, no secrets needed (verified live 2026-09-07)
python -m break_signal.backtest.replay --symbol SOL-USDT-SWAP --tf 1D --limit 500 --out signals.csv

# Service — imports & runs; needs config.yaml with real secrets for notify
pip install -r requirements.txt
cp config.example.yaml config.yaml    # fill in Telegram/Discord secrets
python -m break_signal -c config.yaml

# Deploy (Pi 5)
docker compose up -d --build
```

## Current state

**Repo:** https://github.com/embrizo/Break_Signal — `main`. `data/` layer + `.gitignore` fix committed as `3590c43` (2026-09-07), not yet pushed.

**Milestone status (corrected):**

| # | Milestone | Status |
|---|---|---|
| M1 | Pine indicator draws lines | Done (not visually verified on TradingView by user) |
| M2 | Pine break alert fires (user verifies) | Waiting — user must load it |
| M3 | Rules tuned | Blocked on M2 |
| M4 | Python core + tests reproduce Pine lines | Done — 20 tests pass on synthetic data |
| M5 | Telegram+Discord alerts live from Pi 5 | In progress — data layer done, REST + WS both verified live; only notify (needs secrets) not yet run |
| M6 | Backtest report | Replay runs on live data (12 signals/500 bars); multi-symbol hit-rate *report* not yet produced |
| M7 | Multi-symbol + 24/7 Docker deploy | Docker files exist; not deployed |

**What's real vs. claimed:** `core/`, `data/`, `notify/`, `render/`, `backtest/replay.py`, tests, Docker files, config example all exist and are committed. REST + WS data paths verified live; only the notify path (needs real secrets) is unexercised.

## Environment notes

- **This machine is new to the project** (`C:\Users\Pattapon\...`, Windows 11, PowerShell). Dependency state here is unverified. The old handoff referenced a different machine (`C:\Users\embri\...`, hermes-agent venv) with only numpy+pytest installed.
- Core tests run with just numpy + pytest. `aiohttp`, `websockets`, `pandas`, `mplfinance`, `matplotlib` are needed for the full service — confirm they're installed before running anything beyond `tests/`.

## Next steps

1. **Create `config.yaml`** with real Telegram token + chat_id + Discord webhook; run `python -m break_signal -c config.yaml` and confirm a real break fires to both channels (validates the notify + render layer — the only M5 piece not yet exercised live). Completes M5. *(REST + WS data paths already verified live 2026-09-07.)*
2. **User action: load Pine indicator on TradingView** — verify auto lines match the reference screenshot; tune `pivotLen`/`atrBreak` (M2/M3). Copy winning tuning into `config.example.yaml` `params:` for parity.
3. **Deploy to Pi 5** — `docker compose up -d --build`; point `./data` (state dir) at an SSD/USB.
4. **M6 backtest report** — extend `replay.py` output into a hit-rate summary over ~12 months across SOL + BTC + ETH to check the strict defaults don't overfit SOL.
5. **Optional Phase 3** — FastAPI + TradingView Lightweight Charts dashboard.
6. **(Housekeeping) push `main`** to GitHub when ready — `3590c43` is local-only.

## Session log

### Session 4 — 2026-09-07
- **Added multi-scale pivot detection** (IMPLEMENTATION_PLAN.md §11). A user example (Gold 4h) showed a valid consolidation trendline the single-scale detector missed. Added a finer pivot pass (`use_fine_pivots`, `pivot_len_fine=3`) merged with the coarse scale; the touch-cluster gap shrinks to match. New inputs default ON. Mirrored in Pine (`useFine`/`pivotFine` + `mergeP` + swap-ordered pairing + a `debugCand` toggle that draws all candidates) and Python core (`params.py`, `pivots.merge_pivots`, `engine._pivot_bars`, `trendline` touch_gap).
- Verified: 22 tests pass (2 new — a synthetic line detectable at L=3 but not L=5). Live replay: fine OFF = 12 signals (unchanged), fine ON = 14 (2 extra valid breaks). **Pine not compiled here — load it on TradingView to confirm; use `debugCand` to see candidates.**

### Session 3 — 2026-09-07
- Ran `/handoff` on a fresh machine. **Found the `data/` package was missing** — never on disk, never committed, yet imported by `watcher.py` and `backtest/replay.py`, so both entry points crashed at import. Wrote `ANTIGRAVITY_HANDOFF.md` and rewrote this file.
- **Wrote the `data/` package** (`__init__.py`, `okx_rest.py`, `okx_ws.py`) matching the existing call-site signatures. Verified live: 20 tests pass, REST backfill returns 500 clean SOL 1D candles, `replay.py` produces 12 signals end to end, and the WS stream yielded a confirmed `candle1m` in ~13s. Only the notify layer (needs secrets) remains un-run.
- **Found & fixed the root cause:** `.gitignore` had an unanchored `data/` (meant for the Docker state volume) that also matched `src/break_signal/data/`, silently swallowing the source package in Session 2. Changed to `/data/`. Committed everything as `3590c43` (local, not pushed).

### Session 2 — 2026-09-06
- Built Phase 2 Python service: `core/`, `notify/`, `render/`, `backtest/replay.py`, `watcher.py`, `__main__.py`, Docker files, config example, 20 passing core tests. Rewrote README.
- Documented the `last_bar-1` validity-walk decision and the pivot-timestamp line id.
- (In hindsight: the `data/` layer *was* written this session but was silently gitignored, so it never entered the commit — root-caused and fixed in Session 3.)

### Session 1 — 2026-09-06
- User provided a TradingView SOLUSDT 1D screenshot with hand-drawn trendlines and asked for an auto-trendline breakout alert system.
- Decided: TradingView Pine first (prove rules visually), then Python service on Pi 5 for multi-symbol + Telegram/Discord.
- Wrote `IMPLEMENTATION_PLAN.md` and `pine/break_signal.pine` (complete Pine v6 indicator).
- User locked decisions: OKX futures, 1D+4H, Telegram+Discord, strict quality, Pi 5 hosting.
- `.gitignore` protects `config.yaml`, `.env`, `state.db`. Pushed to GitHub.

## How to resume in a new session

1. Read this file and the Status section at the top.
2. Also read `ANTIGRAVITY_HANDOFF.md` if another tool worked here.
3. Skim recent commits for anything landed after this file was last updated.
4. Pick up at Next steps #1 (write `data/`) unless the user says otherwise.
5. Update this file's session log and state before ending the session.
