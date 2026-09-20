# Claude handoff — Break Signal

**Last Updated:** 2026-09-20
**Workspace:** `G:\7Days\Trading_Journal` (moved from `Break_Signal` on 2026-09-20; same git repo + remote)
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
- **`backtest/`** — [replay.py](src/break_signal/backtest/replay.py): growing-window replay → CSV.
- **`journal/`** — added 2026-09-20 (plan phases J0 + J1): [db.py](src/break_signal/journal/db.py) (`JournalDB`, SQLite WAL, separate `data/journal.db`, seed word bank), [models.py](src/break_signal/journal/models.py), [parser.py](src/break_signal/journal/parser.py) (one-line trade/close syntax), [analytics.py](src/break_signal/journal/analytics.py) (**the only place metrics are computed**), [rules.py](src/break_signal/journal/rules.py) (structured rule engine + seed rules), [similar.py](src/break_signal/journal/similar.py) (deterministic similar-trade ranking), [tools.py](src/break_signal/journal/tools.py) (`Tools` — the one shared JSON-safe tool surface), [mcp_server.py](src/break_signal/journal/mcp_server.py) (FastMCP stdio → Claude Code via [.mcp.json](.mcp.json)), [cli.py](src/break_signal/journal/cli.py) (`python -m break_signal.journal …`). Coach behaviour rules: [CLAUDE.md](CLAUDE.md). Spec: [`JOURNAL_AI_IMPLEMENTATION_PLAN.md`](JOURNAL_AI_IMPLEMENTATION_PLAN.md).
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
- **Journal rules (Session 5).** `journal/analytics.py` is the only place R / PnL / stats are computed; `db.close_trade` calls it and caches `r_multiple`/`pnl_amount` on the row. Missing user input stays NULL — never inferred. `journal` imports `core` (for `Signal`); `core` and `notify/base.py` (+ the alert notifiers) must never import `journal`. `notify/telegram_bot.py` is the deliberate exception — it is a journal front-end that happens to live under `notify/`. `watcher` imports `journal.footer` lazily; `__main__` at the top. Test fixture in `tests/test_analytics.py` has hand-computed golden numbers — if it fails, analytics changed, not the data.
- **Stats count by stored `outcome`, not by R sign** (code-review fix, 2026-09-20). `analytics.closed()` = CLOSED with an outcome; `with_r()` = those with an R. wins/losses/BE/streaks/PF use `outcome`; avg R / drawdown / curve use R. A user override at close (`outcome="BE"` on a +0.5R trade) is therefore honoured everywhere. Over MCP, PF=∞ is serialised as the string `"inf"`.
- **Schema changes go through `db._MIGRATIONS`** — append `(version, fn)`, bump `SCHEMA_VERSION`, keep the CREATE block current for fresh DBs, and never put a new column's index in `_SCHEMA` (the column doesn't exist yet on old files; create it in the migration fn). Fresh DBs also run every step, so steps must be idempotent (check `_has_column`).
- **Memories are derived, never written by the LLM.** `memory.derive()` is the only source; the coach reads them. Thresholds: n ≥ 5, win ≥ 70 % / ≤ 35 %, 90 d; rules ≥ 3 breaks.
- **All writes go through `journal/tools.py`** — CLI, MCP and (J3) Telegram. Never call `JournalDB.add_trade/close_trade` directly from a front-end; you'd skip rule checks, seeding, auto-link and ctx copy.
- **Signal ids in alerts (J2).** The watcher inserts the `signals` row *before* dispatching so the alert footer can say `--signal <id>` / `skip <id>`. `signals` is UNIQUE on (symbol, tf, line_id, candle_ts); CSV imports synthesise `line_id = csv:<side>:<line>` because the CSV has no line id.

## Quick run commands

```bash
# All tests (core + journal) — need only numpy + pytest; 81 pass
python -m pytest tests/ -q

# Journal (no deps beyond stdlib + pydantic/yaml for config)
python -m break_signal.journal add "SOL 4H long 231.5 sl 225 tp 245 #breakout -- clean retest"
python -m break_signal.journal close "1 244 win hit TP #hit_tp"
python -m break_signal.journal stats

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

- Two dev machines have touched this repo: `C:\Users\Pattapon\...` (sessions 3–4, OKX reachable) and `C:\Users\embri\...` (sessions 1–2, 5; `python` on PATH = hermes-agent venv, Python 3.11.15, has numpy, pytest, aiohttp, mcp 1.26, pydantic, yaml — NOT anthropic, pandas, mplfinance).
- **On the `embri` machine OKX is DNS-blocked** (`www.okx.com`, `aws.okx.com`, etc. resolve to nothing — ISP block). `market_snapshot`, `replay.py`, and the watcher cannot fetch there without a VPN; the MCP tool returns a clear `error` in that case. Everything journal-side works offline.
- Core + journal tests run with just numpy + pytest. `aiohttp`, `websockets`, `pandas`, `mplfinance`, `matplotlib` are needed for the full service; `mcp` for the MCP server (`pip install -e .[ai]`).

## Next steps

0. **Journal + AI coach.** J0–J4 code done (2026-09-20); next is **J5** (Pi deploy: compose mounts for `journal.db` + screenshots, nightly `.backup`, `[ai]` extra behind `AI_ENABLED`). **Live checks still owed:** (a) `.mcp.json` loads in a fresh Claude Code session (confirmed 2026-09-20 — the `journal` MCP tools appeared in-session); (b) `ANTHROPIC_API_KEY=… python -m pytest tests/evals -q` (3 live coach evals) and `python -m break_signal.journal ask "how are my 4H breaks?"`; (c) Telegram bot: `config.yaml` with `telegram_bot.enabled: true`, `allowed_chat_ids: ["<your id>"]`, real `channels.telegram.bot_token`, then `python -m break_signal -c config.yaml` and send `/help`. Then **J4** per the plan: `journal/report.py` (weekly/monthly, WEEKLY_V1 prompt already in `prompts.py`), `journal/memory.py`, equity-curve PNG, violations in the alert footer.
1. **Create `config.yaml`** with real Telegram token + chat_id + Discord webhook; run `python -m break_signal -c config.yaml` and confirm a real break fires to both channels (validates the notify + render layer — the only M5 piece not yet exercised live). Completes M5. *(REST + WS data paths already verified live 2026-09-07.)*
2. **User action: load Pine indicator on TradingView** — verify auto lines match the reference screenshot; tune `pivotLen`/`atrBreak` (M2/M3). Copy winning tuning into `config.example.yaml` `params:` for parity.
3. **Deploy to Pi 5** — `docker compose up -d --build`; point `./data` (state dir) at an SSD/USB.
4. **M6 backtest report** — extend `replay.py` output into a hit-rate summary over ~12 months across SOL + BTC + ETH to check the strict defaults don't overfit SOL.
5. **Optional Phase 3** — FastAPI + TradingView Lightweight Charts dashboard.
6. **(Housekeeping) push `main`** to GitHub when ready — `3590c43` is local-only.

## Session log

### Session 5 — 2026-09-20
- User supplied `trading_journal_implementation_plan_AI_extended.md` (journal + AI copilot concept, Next.js/Supabase/LangGraph stack — file not kept in repo) and asked to integrate it with Break Signal plus an "AI suggestion" feature: log trades with win/loss + reason, then ask Claude in chat for price-action suggestions grounded in that history.
- Wrote `JOURNAL_AI_IMPLEMENTATION_PLAN.md`. Key decisions: keep Python/SQLite/Pi stack (drop Next.js, Supabase, LangGraph, pgvector); separate `data/journal.db`; one shared `journal/tools.py` exposed three ways — MCP server for Claude Code (the "talk in this chat" path, phase J1), Telegram `/ask` via Anthropic SDK tool runner (`claude-opus-5`, read-only tools), and CLI. Deterministic `analytics.py` is the only source of numbers; LLM interprets only. Every watcher alert becomes a `signals` row that trades can link to.
- **Repo relocated** to `G:\7Days\Trading_Journal`. The working copy there had been a partial copy at `a7ac237` (4 commits behind origin); copied `.git` + missing files over, fast-forwarded to `d926166` (multi-scale pivots, data layer, Pine fix). 22 tests pass. The old `G:\7Days\Break_Signal` folder is now a stale duplicate — delete it.
- `signals_sol_1d_binance.csv` (10 rows, replay output) committed as the first backtest batch for journal phase J2.
- **Built phase J0** with defaults for the plan's §10 questions (R-multiples primary; bilingual seed tags from the source doc): `journal/{models,db,analytics,parser,cli}.py` + `__main__.py`, `journal:` config block, 59 new tests (81 total, all pass), README section. CLI smoke-tested end to end (add → close → event → list/show/stats/export). Gotcha: in PowerShell pass the trade line as ONE quoted string — bare `--` is stripped and `104,200` is split on the comma before Python sees it.
- **Built phase J1**: `journal/{tools,similar,rules,mcp_server}.py`, `.mcp.json`, `CLAUDE.md`, `pyproject` extra `[ai]`; 40 new tests (121 total). `rules.py` was pulled forward from J4 because `journal_rule_check` needed it; violations are evaluated and stored on every add/close/event. Drove the MCP server over stdio with the `mcp` Python client — all tools round-trip. `market_snapshot` could not be run live: OKX is DNS-blocked on this machine; the pure `snapshot_from_candles()` is tested on the synthetic breakout fixture. Not yet done: the in-chat smoke test (needs a fresh Claude Code session to load `.mcp.json`).
- **Code review of J0–J2** (`/code-review`, 6 findings, all fixed): tag counted twice when used at ENTRY+EXIT; `add_trade` crashed on a tf outside the OKX bar table; CLI bypassed `Tools` (no rule checks / seed / ctx copy); stats classified by R sign and could contradict `search_trades(outcome=)` → now count by `outcome`; PF=∞ became `null` over MCP → now `"inf"`; `rename_tag` clash was a raw IntegrityError. Added `tests/test_cli.py`. 137 tests pass.
- **Built phase J4**: `journal/memory.py` (deterministic evidence-backed memories: tag/tf/direction/RSI-band patterns with n ≥ 5 and lopsided win rate over 90 d, rules broken ≥ 3×; upsert by key, prune unconfirmed, keep confirmed + trader notes), `journal/report.py` (weekly/monthly metrics block → markdown, optional narrative via `Coach.narrative()`, optional PNG, in-process scheduler wired in `__main__`), footer rule hint, schema **v2** (`memories.key`) via the migrations table — verified on the real `data/journal.db`. Seed rules now only on DB creation. Bot `/report /memories /confirm /forget`; CLI `report`, `memories`; MCP `journal_report`, `journal_memories`, `journal_confirm_memory`, `journal_forget_memory`, `journal_add_memory_note`. Also: watcher backfill retry with backoff (was: service died when OKX unreachable at boot — verified live). Driving the report exposed a PF bug (declared-LOSS-at-+R made PF "inf") → PF sums only sign-matching R. 172 tests pass. matplotlib is NOT installed here, so `chart_png` is untested visually.
- **Built phase J3**: `journal/coach.py` (AsyncAnthropic tool runner, 11 read-only `@beta_async_tool` wrappers, `parity_check()` number guard, `/review` via `messages.parse` + pydantic, every answer stored in `ai_analysis`, daily budget), `journal/prompts.py` (coach_v1 / review_v1 / weekly_v1), `notify/telegram_bot.py` (long-poll command bot, allowlist, `/shot` photos, pure `handle_command`), `build_telegram_bot()` in `__main__`, `telegram_bot:` + `ai:` config, CLI `ask` / `review`. Installed `anthropic 1.7.0` into the hermes venv. 20 unit tests with a fake client + 3 key-gated live evals (marker `live`). 154 pass, 3 skipped. **No API key on this machine** → live evals and a real `/ask` are untested; the SDK request shape (adaptive thinking, cache_control on system, max_iterations) was taken from the claude-api skill docs for SDK 1.x.
- **Built phase J2**: watcher persists every alert to `journal.db` (`Watcher(..., journal=)`, wired in `__main__`), `replay.py --to-journal`, `journal import-signals`, `analytics.signal_history()` + `journal/footer.py` alert footer (`format_message(sig, footer)` keeps `notify` independent of `journal`), MCP tool `journal_signal_history` (24 tools now). Imported `signals_sol_1d_binance.csv` into the real `data/journal.db` (10 backtest signals, ids 1–10, gitignored). 7 new tests (128 total). Watcher test exercises `_on_close` on the synthetic breakout with an in-memory `State` + journal — no network.

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
4. Pick up at Next steps #0 (journal J0) unless the user says otherwise.
5. Update this file's session log and state before ending the session.
