# Break Signal × Trading Journal × AI Coach — Implementation Plan

**Goal:** turn Break Signal from a one-way alert bot into a closed loop:
the system fires a breakout → you take (or skip) the trade → you log the result
*and the reason* → the AI reads your whole history and, the next time you ask
"should I take this?", answers with price-action suggestions grounded in **your own
trades**, not generic advice.

**Status:** J0–J5 code done (2026-09-20). Remaining: live checks (API key, Telegram, OKX) and the actual Pi deploy. J6 optional.
**Date:** 2026-09-20
**Source docs:** `IMPLEMENTATION_PLAN.md` (Break Signal, Phases 1–2 built),
`trading_journal_implementation_plan_AI_extended.md` (journal + AI concept, analysed in §1)

---

## 0. TL;DR — what gets built

| Layer | What | Where you use it |
|---|---|---|
| **Journal** | SQLite trade log with word-bank tags (setup / psychology / exit reason), win/loss + reason text, screenshots, link to the Break Signal alert that triggered it | CLI, Telegram, or just tell Claude in chat |
| **Analytics** | Deterministic stats: win rate, profit factor, expectancy, avg R, drawdown, **per-tag** and **per-signal-feature** performance | Feeds every AI answer |
| **AI Coach in this chat** | An MCP server that gives Claude Code tools to read/write the journal and pull the live OKX market snapshot (lines, ATR, RSI). You ask, Claude scans your list and suggests | Claude Code — this chat |
| **AI Coach on the phone** | Telegram `/trade`, `/close`, `/ask` commands; `/ask` calls Claude via the Anthropic API with the same tools | Telegram, anywhere |
| **Signal enrichment** | Every breakout alert gets a footer: *"Your history on this setup: 7 trades, 57% win, +0.8R avg"* — no LLM needed | Telegram / Discord alerts |
| **Rules + reports** | Structured trading rules checked on every trade, weekly review, long-term coach memory | Telegram weekly push, chat |

Everything runs on the existing **Python 3.11 + SQLite + Pi 5** stack. No Next.js,
no Supabase, no LangGraph, no pgvector (see §1.2 for why).

---

## 1. Analysis of the source journal plan

### 1.1 What to keep (core ideas that fit Break Signal)

| Idea from source doc | Verdict | How it maps |
|---|---|---|
| Trade entry with symbol / side / entry / SL / TP / exit / PnL / R:R | **Keep** | `trades` table (§4.1) |
| Word bank tags — Setup, Psychology, Exit Reason; user adds new chips freely, Thai OK | **Keep** — this is the heart of the "give reason" workflow | `tags` + `trade_tags`, UTF-8, categories `SETUP / PSYCH / EXIT / MISTAKE` |
| Pre/post-trade screenshots | **Keep**, simplified | File paths under `data/journal/screenshots/`; Claude Code reads PNGs natively, Telegram photos are downloaded |
| Tag ↔ outcome correlation ("FOMO 20% win vs ตามวินัย 75%") | **Keep** — the single most valuable feature | `analytics.tag_stats()` |
| Equity curve / drawdown | Keep | `analytics.equity_curve()`, rendered with the existing mplfinance/matplotlib stack |
| Price alerts / watchlist / TradingView widget | **Already exists** as Break Signal itself | Skip — trendline breaks *are* the alerts |
| FACT / CALCULATION / AI ANALYSIS / USER DECISION separation | **Keep as a hard rule** | §6.3, enforced by the tool design and the coach system prompt |
| AI extraction ("BTC long 104,200 sl 103,500…" → structured) | Keep | Claude in chat calls `journal_add_trade` with parsed fields; deterministic regex parser for CLI/Telegram |
| AI post-trade review | Keep | `journal_review_trade` tool + `/review` |
| Rule engine (structured conditions, backend-checked) | Keep | `rules` table + `rules.py` (§4.3) |
| Similar-trade retrieval | Keep, **v1 deterministic** | Feature matching on (symbol, side, tf, tags, RSI band, ATR-distance band). Embeddings deferred |
| Weekly report, coach memory | Keep | §5 phase J4 |
| AI must not calculate financial metrics | **Keep as a hard rule** | LLM only ever sees numbers produced by `analytics.py` |

### 1.2 What to drop or replace, and why

| Source doc proposes | Decision | Reason |
|---|---|---|
| Next.js + Tailwind + shadcn UI | **Drop for now** (optional Phase J6) | Single user, primary interfaces are Claude Code chat and Telegram — both already exist. A web UI is the Break Signal Phase 3 dashboard, still optional |
| Supabase / PostgreSQL + Auth | **Replace with SQLite** | Break Signal already runs SQLite WAL on the Pi. Single user → no auth. `journal.db` is a separate file from `state.db` so wiping alert state never touches trades |
| FastAPI AI layer | **Replace with MCP server + Telegram handlers** | Claude Code speaks MCP natively — that is the "talk in this chat" requirement. FastAPI only if J6 web UI happens |
| LangGraph | **Drop** | The Anthropic SDK tool runner already drives the tool loop; the workflow is not multi-branch enough to justify a graph library |
| pgvector / embeddings | **Defer** | A personal journal will have hundreds, not millions, of trades. Deterministic feature matching + tag overlap is transparent and good enough. Revisit with Voyage embeddings if similar-trade recall feels weak |
| Celery / Redis | **Drop** | asyncio tasks inside the existing watcher process; weekly report via `asyncio` timer or cron |
| Multi-user `USERS` table | **Drop** | Single trader. Add later if ever needed |

### 1.3 The gap the source doc does not cover — signal ↔ trade linkage

The source doc treats the journal and the market watcher as separate features.
For Break Signal the killer combination is joining them:

```
Break Signal alert          Your trade               Outcome + reason
─────────────────           ──────────────           ─────────────────
break_up, resistance,       LONG @ 231.5             WIN +1.8R
touches=4, rsi=61,     ──►  #breakout #retest   ──►  "clean retest, held plan"
vol_ratio=1.9, 4H           sl 225  tp 245           #discipline
```

Every alert the watcher emits is stored as a `signals` row. A trade can point at
the signal that triggered it. That lets the AI answer questions like:

- *"When the bot fires break_up with RSI > 65 and I take it, what happens?"*
- *"My 4H breaks vs 1D breaks — which do I actually make money on?"*
- *"Show me the alerts I skipped that would have worked."*

This is only possible because Break Signal already computes `touches`, `age_bars`,
`atr_dist`, `vol_ratio`, `rsi` for every alert (`core/types.py:Signal`).

---

## 2. Architecture

```
                         ┌──────────────────────────────┐
                         │  Claude Code (this chat)      │
                         │  reads CLAUDE.md coach rules  │
                         └──────────────┬───────────────┘
                                        │ MCP (stdio)
                                        ▼
┌───────────────┐        ┌──────────────────────────────┐        ┌─────────────────┐
│ Telegram user │◄──────►│ break_signal.journal          │◄──────►│ OKX public API  │
│ /trade /close │  bot   │  ├─ db.py        (SQLite)     │ Engine │ (candles only)  │
│ /ask /review  │        │  ├─ analytics.py (numbers)    │        └─────────────────┘
└───────────────┘        │  ├─ similar.py   (retrieval)  │
        ▲                │  ├─ rules.py     (violations) │
        │                │  ├─ parser.py    (1-line NL)  │
        │ alerts +       │  ├─ tools.py     (shared tool │
        │ history footer │  │                 functions) │
        │                │  ├─ mcp_server.py             │
┌───────┴────────┐       │  ├─ coach.py     (Claude API) │
│ Watcher        │──────►│  └─ cli.py                    │
│ (existing)     │signals└──────────────┬───────────────┘
└────────────────┘                      │
                                        ▼
                              data/journal.db  +  data/journal/screenshots/
```

**One set of tool functions, three front-ends.** `journal/tools.py` holds plain
Python functions (`add_trade`, `search_trades`, `tag_stats`, `market_snapshot`, …).
`mcp_server.py` exposes them to Claude Code; `coach.py` exposes the same functions to
Claude via the Anthropic SDK tool runner for Telegram `/ask`; `cli.py` exposes them
to the shell. Analytics numbers are computed in exactly one place.

### 2.1 Package layout (additions to `src/break_signal/`)

```
src/break_signal/
├── journal/
│   ├── __init__.py
│   ├── db.py             # JournalDB: schema, CRUD, migrations (SQLite WAL, like core/state.py)
│   ├── models.py         # dataclasses: Trade, Tag, Rule, Violation, SignalRef
│   ├── parser.py         # one-line trade syntax → Trade fields (deterministic, tested)
│   ├── analytics.py      # win rate, PF, expectancy, R, drawdown, tag stats, feature buckets
│   ├── similar.py        # deterministic similar-trade retrieval
│   ├── rules.py          # rule engine: evaluate JSON conditions against a trade
│   ├── tools.py          # the shared tool functions (pure Python, JSON-serialisable I/O)
│   ├── mcp_server.py     # FastMCP stdio server wrapping tools.py  → Claude Code
│   ├── coach.py          # Anthropic SDK tool runner wrapping tools.py → Telegram /ask
│   ├── prompts.py        # versioned system prompts (COACH_V1, REVIEW_V1, WEEKLY_V1)
│   ├── report.py         # weekly / monthly report assembly (metrics + LLM narrative)
│   ├── memory.py         # coach memory: evidence-backed observations
│   └── cli.py            # python -m break_signal.journal ...
├── notify/
│   └── telegram_bot.py   # NEW: long-polling command handler (/trade /close /ask …)
├── watcher.py            # MODIFIED: persist Signal rows, append history footer
└── config.py             # MODIFIED: journal + ai config blocks
.mcp.json                 # NEW: registers the journal MCP server for Claude Code
CLAUDE.md                 # NEW: coach behaviour rules Claude Code follows in this repo
tests/
├── test_journal_db.py
├── test_parser.py
├── test_analytics.py     # golden numbers on a fixture journal
├── test_similar.py
├── test_rules.py
└── test_tools.py
```

### 2.2 New dependencies

| Package | Why | Where |
|---|---|---|
| `mcp` | FastMCP stdio server for Claude Code | `journal/mcp_server.py` |
| `anthropic` | Claude API for Telegram `/ask`, reviews, weekly report | `journal/coach.py`, `report.py` |
| (none for Telegram bot) | `aiohttp` already present; bot uses `getUpdates` long-polling | `notify/telegram_bot.py` |

`mcp` and `anthropic` go in an optional extra `[ai]` in `pyproject.toml` so the
Pi image can stay slim if you only want the journal + enrichment without the LLM.

---

## 3. The user workflow (what it feels like)

### 3.1 Logging a trade — three ways, same result

**In this chat (natural language, Claude extracts):**
> "took the SOL 4H break, long 231.5, sl 225, tp 245, breakout + retest, felt calm"

Claude calls `journal_add_trade(symbol="SOL-USDT-SWAP", tf="4H", direction="LONG",
entry=231.5, sl=225, tp=245, tags=["Breakout","Retest","ตามวินัย"], signal_id=<latest matching alert>)`
and replies with the trade id and the computed planned R:R. Fields you did not say
are stored as `NULL`, never guessed (source doc §8.1 rule).

**Telegram (one-line syntax, deterministic parser):**
```
/trade SOL 4H long 231.5 sl 225 tp 245 #breakout #retest #calm
```

**CLI:**
```bash
python -m break_signal.journal add "SOL 4H long 231.5 sl 225 tp 245 #breakout #retest"
```

### 3.2 Closing a trade — this is where the knowledge comes from

```
/close 12 244 win  hit TP, clean retest, held the plan  #discipline
/close 13 224 loss moved SL wider then got stopped, FOMO entry  #แหกกฎเลื่อนSL #FOMO
```

`outcome`, `exit_price`, `reason` (free text) and tags are stored. R-multiple and PnL
are computed by `analytics.py`, not typed by you. If the reason mentions "moved SL" the
rule engine flags a `sl_moved` violation (§4.3) — you can also log it explicitly with
`/event 13 sl_moved 222`.

### 3.3 Asking for a suggestion — the AI scans your list

> **You (in this chat):** "SOL just broke the 1D resistance at 236, RSI 68. Worth taking?"

What Claude does (visible tool calls):
1. `market_snapshot("SOL-USDT-SWAP","1D")` → live lines, ATR, RSI, distance to nearest support/resistance, whether an alert fired on the last bar
2. `journal_similar_trades(symbol, direction="LONG", tf="1D", side="resistance", rsi=68)` → the N most similar past trades with outcomes and reasons
3. `journal_tag_stats(period="180d")` → your best/worst tags
4. `journal_rule_check(proposed_trade)` → would this break any of your rules?

What Claude answers (shape enforced by `CLAUDE.md` / `COACH_V1`):

```
FACTS (live):  1D resistance line 234.1 broke on today's close, buffer +0.42 ATR,
               vol 1.7× avg, RSI 68.2. Next resistance line: none within 12 ATR.
               Nearest support line: 221.4 (–6.2%).

YOUR HISTORY (deterministic, 8 similar trades, last 180 d):
  • 1D resistance breaks, LONG:  5 W / 3 L, avg +0.6R, PF 1.9
  • …with RSI > 65 at entry:     2 W / 3 L, avg –0.2R   ← small sample
  • Tag "Retest" on entry:        4 W / 0 L, avg +1.4R
  • Tag "FOMO" on entry:          0 W / 3 L
  • Your rule "no entry when RSI > 70 on 1D" — NOT violated (68.2)

INTERPRETATION (AI):
  The setup itself has been profitable for you, but your extended-RSI entries have
  not. Your winners on this setup all waited for a retest. Price action to watch:
  a pullback toward the broken line (~234) that holds on a 4H close would match
  your best-performing pattern; a straight continuation entry matches your worst.

YOUR DECISION. If you take it, log it with the signal id so we can learn from it.
```

Every number in "YOUR HISTORY" comes from `analytics.py`; Claude only writes the
"INTERPRETATION" paragraph and must cite sample sizes. No "buy/sell" directives.

### 3.4 Alert enrichment (no LLM, always on)

The existing Telegram/Discord alert gains a footer, computed by `analytics.signal_history()`:

```
🔴 SOL-USDT-SWAP · 4H — RESISTANCE BREAK ↑
Price 231.50  Line 229.80 (+0.35 ATR)  Vol 1.9×  RSI 61.3
Line: 4 touches, 51 bars old

📒 Your history on 4H resistance breaks (LONG): 7 trades · 57% win · +0.8R avg
   Best tag: Retest (+1.4R)  Worst tag: FOMO (–1.0R)
   Reply /trade 4H long <entry> sl <sl> tp <tp> to log, or /skip 118 to record a pass
```

`/skip <signal_id> <reason>` records a *deliberate non-trade*, so later the AI can
tell you whether your skips were good.

---

## 4. Data model

Stored in `data/journal.db` (separate file from `state.db`; same WAL pragmas as
`core/state.py`). All timestamps are epoch ms UTC, matching `Candles.ts`.

### 4.1 Core tables

```sql
-- Every breakout the watcher emits (mirror of Signal.to_dict() + id). Also written by replay.py
-- with source='backtest' so history is queryable before you ever trade.
CREATE TABLE signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,            -- 'live' | 'backtest' | 'pine'
    symbol      TEXT NOT NULL,
    exchange    TEXT NOT NULL,
    tf          TEXT NOT NULL,
    event       TEXT NOT NULL,            -- break_up | break_down
    side        TEXT NOT NULL,            -- resistance | support
    line_id     TEXT NOT NULL,
    price       REAL NOT NULL,
    line_price  REAL NOT NULL,
    atr_dist    REAL, touches INTEGER, age_bars INTEGER, vol_ratio REAL, rsi REAL,
    candle_ts   INTEGER NOT NULL,
    created_ts  INTEGER NOT NULL,
    UNIQUE (symbol, tf, line_id, candle_ts)
);

CREATE TABLE trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id       INTEGER REFERENCES signals(id),   -- NULL if discretionary
    symbol          TEXT NOT NULL,
    tf              TEXT,                              -- decision timeframe
    direction       TEXT NOT NULL CHECK (direction IN ('LONG','SHORT')),
    status          TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','CLOSED','SKIPPED')),
    -- objective
    entry_price     REAL, sl_price REAL, tp_price REAL, exit_price REAL,
    position_size   REAL, leverage REAL, fees REAL,
    risk_amount     REAL,                              -- account currency at risk
    risk_pct        REAL,                              -- % of account
    opened_ts       INTEGER, closed_ts INTEGER,
    -- outcome (user-declared; r_multiple/pnl are COMPUTED by analytics, cached here)
    outcome         TEXT CHECK (outcome IN ('WIN','LOSS','BE',NULL)),
    pnl_amount      REAL, r_multiple REAL,
    -- subjective
    entry_reason    TEXT,                              -- why you entered
    exit_reason     TEXT,                              -- why you exited / the lesson
    confidence      INTEGER CHECK (confidence BETWEEN 1 AND 5),
    emotion_before  TEXT, emotion_after TEXT,
    notes           TEXT,
    -- market context snapshot at entry (copied from market_snapshot, for similarity)
    ctx_rsi REAL, ctx_atr REAL, ctx_atr_dist REAL, ctx_vol_ratio REAL,
    ctx_session     TEXT,                              -- ASIA | LONDON | NY (from opened_ts)
    created_ts      INTEGER NOT NULL, updated_ts INTEGER NOT NULL
);

CREATE TABLE tags (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL,                           -- "Breakout", "FOMO", "ตามวินัย", "แหกกฎเลื่อน SL"
    category  TEXT NOT NULL CHECK (category IN ('SETUP','PSYCH','EXIT','MISTAKE','OTHER')),
    UNIQUE (name COLLATE NOCASE)
);

CREATE TABLE trade_tags (
    trade_id  INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
    tag_id    INTEGER NOT NULL REFERENCES tags(id),
    phase     TEXT NOT NULL CHECK (phase IN ('ENTRY','EXIT')),   -- which moment the tag describes
    PRIMARY KEY (trade_id, tag_id, phase)
);

-- Things that happened during the trade. sl_moved is what the rule engine cares about most.
CREATE TABLE trade_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id  INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
    type      TEXT NOT NULL,     -- sl_moved | tp_moved | partial_close | added | note
    data      TEXT,              -- JSON, e.g. {"from":225,"to":222}
    event_ts  INTEGER NOT NULL
);

CREATE TABLE screenshots (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id  INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
    phase     TEXT NOT NULL CHECK (phase IN ('PRE','POST')),
    path      TEXT NOT NULL,     -- data/journal/screenshots/<trade_id>_<phase>_<ts>.png
    created_ts INTEGER NOT NULL
);
```

### 4.2 AI-side tables

```sql
CREATE TABLE rules (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL UNIQUE,       -- "Max risk 1%"
    condition TEXT NOT NULL,              -- JSON: {"field":"risk_pct","op":"<=","value":1}
    severity  TEXT NOT NULL DEFAULT 'high',
    enabled   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE rule_violations (
    trade_id  INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
    rule_id   INTEGER NOT NULL REFERENCES rules(id),
    detail    TEXT,
    PRIMARY KEY (trade_id, rule_id)
);

-- Any LLM output we keep. Always tagged with model + prompt version so it can be re-run.
CREATE TABLE ai_analysis (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id       INTEGER REFERENCES trades(id) ON DELETE CASCADE,   -- NULL for reports
    kind           TEXT NOT NULL,   -- review | suggestion | weekly | monthly | vision
    model          TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    input_metrics  TEXT NOT NULL,   -- JSON of the deterministic numbers the LLM was given
    output         TEXT NOT NULL,   -- JSON (structured) or markdown
    created_ts     INTEGER NOT NULL
);

-- Coach memory: evidence-backed, never a personality judgement (source doc §15).
CREATE TABLE memories (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    type           TEXT NOT NULL,   -- pattern | rule | preference | terminology
    content        TEXT NOT NULL,   -- "11 trades tagged FOMO in the last 90 d; 2 W / 9 L"
    evidence       TEXT NOT NULL,   -- JSON: {"trade_ids":[...], "period":"..."}
    confirmed      INTEGER NOT NULL DEFAULT 0,   -- user said "yes that's right"
    first_seen_ts  INTEGER NOT NULL, last_seen_ts INTEGER NOT NULL
);
```

### 4.3 Rule engine semantics

`rules.py` evaluates `condition` against a flat dict built from the trade row plus
derived fields (`planned_rr`, `has_event_sl_moved`, `entry_hour_utc`, `tags`):

| Operator | Example |
|---|---|
| `<=, <, >=, >, ==, !=` | `{"field":"risk_pct","op":"<=","value":1}` |
| `in / not_in` | `{"field":"tf","op":"in","value":["4H","1D"]}` |
| `has_tag / not_has_tag` | `{"field":"tags","op":"not_has_tag","value":"FOMO"}` |
| `is_false` | `{"field":"has_event_sl_moved","op":"is_false"}` |

Seed rules (editable): max risk 1 %, planned R:R ≥ 1.5, never widen SL, only tagged
setups from an approved list, no entry when the 1D RSI > 75 / < 25.

---

## 5. Roadmap

Work is ordered so that **the "AI suggestion in this chat" feature lands in the
second phase** — it is the thing you asked for and needs no API key or Telegram work.

### Phase J0 — Journal foundation (2 days) — DONE 2026-09-20

```
[x] journal/db.py — schema above, WAL, migrations table, JournalDB class mirroring core/state.py style
[x] journal/models.py — dataclasses
[x] journal/parser.py — one-line syntax:
      "<SYM> [<tf>] long|short <entry> [sl <x>] [tp <x>] [size <x>] [risk <x>%] [#tag ...] [-- free reason]"
      symbol aliases: SOL → SOL-USDT-SWAP (from config journal.symbol_aliases); Thai tags allowed
[x] journal/analytics.py — win_rate, profit_factor, expectancy, avg_r, max_drawdown, streaks,
      equity_curve, tag_stats(phase, period), feature_bucket_stats(rsi_band, tf, side, event)
      r_multiple = (exit-entry)/(entry-sl) signed by direction; BE if |r| < 0.1
[x] journal/cli.py — add, close, skip, event, tag, list, show, signals, stats, export (csv/json/md)
[x] Seed tag word bank (Setup / Psych / Exit / Mistake — bilingual defaults, user extends)
[x] tests: test_journal_db, test_parser, test_analytics (fixture journal with hand-computed numbers) — 59 tests
[x] config.py / config.example.yaml: `journal:` block (db, screenshots_dir, symbol_aliases, account_size)
```

Implementation notes (J0):
- `outcome` CHECK is `IN ('WIN','LOSS','BE')` (NULL passes CHECK by SQL semantics; the `,NULL` in §4.1 was wrong).
- **Wins / losses / BE / streaks / PF are counted by the stored `outcome`** (review fix 2026-09-20), so
  `journal_stats` always agrees with `journal_search_trades(outcome=…)`. `outcome` defaults to the sign of R
  (±0.1R BE band); the user's stated outcome wins over the derived one; R itself is never edited. `n` = closed
  trades with an outcome; `r_n` = those that also have an R (avg R / PF / drawdown use only these).
- Over MCP, an all-winning profit factor is the string `"inf"` (JSON has no infinity); `null` means no data.
- The CLI's `add / close / skip / event` go through `journal/tools.py` like the MCP server, so rule checks,
  seeding, auto-link and signal-context copying are identical across front-ends.
- Tags: `#liquidity_sweep` → "liquidity sweep" (underscore → space) so one-word chips can hit multi-word seed tags.
  `#12` is accepted as a trade id in `close`. Unknown tags are created with category OTHER.
- `insert_signal` accepts a `core.types.Signal` directly and derives `candle_ts` from its ISO `time`.
- AI-side tables (`rules`, `rule_violations`, `ai_analysis`, `memories`) are created in v1 of the schema so
  J3/J4 need no migration.

**Done when:** you can log, close, and see `stats` + `tag_stats` from the CLI, and the
golden analytics test pins the exact numbers. ✔

### Phase J1 — AI Coach in Claude Code (this chat) (2 days) — CODE DONE 2026-09-20, chat smoke pending

```
[x] journal/tools.py — `Tools` class, every method returns JSON-safe dicts (numpy/NaN scrubbed):
      add_trade (+ auto-link to a matching alert ≤3 bars old, copies its RSI/ATR ctx), add_trade_line,
      close_trade, close_trade_line, skip_signal, add_event, add_tag, tag_trade, add_screenshot, update_trade,
      search_trades, get_trade (+ stored violations), recent_signals, list_tags, list_rules,
      stats, tag_stats, feature_stats, equity_curve, similar_trades, rule_check, review_context,
      market_snapshot (async; snapshot_from_candles() is the pure, tested part)
[x] journal/similar.py — score = tag Jaccard×3 + same side/event×2 + same symbol×2 + same tf×1 + rsi ±8×1
      + atr_dist band×1; direction is a hard filter; ties by recency; per-match breakdown returned
[x] journal/rules.py — pulled forward from J4: operators (§4.3), trade_facts(), seed rules, check/record;
      violations are evaluated + stored on every add/close/event. (J4 keeps: surfacing in alert footer/Telegram.)
[x] journal/mcp_server.py — FastMCP stdio, 23 tools, docstrings state FACT / CALC / WRITE; stderr logging only
[x] .mcp.json — python -m break_signal.journal.mcp_server with PYTHONPATH=src; DB from $JOURNAL_DB / config.yaml
[x] CLAUDE.md — coach rules + answer shape + logging examples + codebase rules
[x] tests: test_tools.py, test_similar.py, test_rules.py (40 new; 121 total pass)
[x] Smoke: drove the server over stdio with the MCP Python client — all tools round-trip
[ ] Smoke in Claude Code: open a NEW session in this repo (so .mcp.json is picked up), ask
      "what's my win rate on 4H breaks?" and watch it call journal_stats
[ ] market_snapshot live: OKX hosts are DNS-blocked on this machine (ISP). Verified only on synthetic candles.
      Options: OKX_REST_URL=https://aws.okx.com in .mcp.json env (also blocked here), VPN, or run on the Pi.
```

**Done when:** in this chat, "should I take this SOL break?" produces the §3.3 answer
with real numbers from your journal and a live OKX snapshot. (Journal half ✔; live snapshot blocked by network.)

### Phase J2 — Signal ↔ trade linkage + alert enrichment (1 day) — DONE 2026-09-20

```
[x] watcher.py: Watcher(..., journal=JournalDB) — insert_signal(sig, 'live') BEFORE dispatch so the alert
      can carry the signal id; failures never block the alert. __main__ opens journal.db and passes it in.
[x] backtest/replay.py: replay_signals() returns Signal objects (with line_id); --to-journal [DB] writes
      them with source='backtest', idempotent
[x] journal import-signals <csv> [--symbol X]: loads a replay CSV (line_id synthesised as csv:<side>:<line>).
      signals_sol_1d_binance.csv imported into data/journal.db (10 rows, ids 1–10)
[x] analytics.signal_history(): same tf + implied direction; signal-linked trades must match side;
      discretionary trades count; best/worst entry tag need n ≥ 2. Exposed as MCP tool journal_signal_history.
[x] journal/footer.py: alert_footer() — stats block when n ≥ 3, else "0 closed trade(s) … stats shown from 3";
      always the log/skip hint with the signal id. notify/base.format_message(sig, footer=None) — notify
      stays independent of journal. Config journal.history_footer toggles it (signals are stored regardless).
[x] tools.add_trade auto-link (done in J1)
[x] journal footer <signal_id>: CLI preview
[x] tests/test_signal_linkage.py (7 tests; 128 total pass)
```

### Phase J3 — Telegram bot + Claude API coach (2 days) — CODE DONE 2026-09-20, live run pending

```
[x] notify/telegram_bot.py — getUpdates long-poll task inside __main__ (build_telegram_bot); chat_id allowlist;
      commands: /trade /close /skip /event /tag /tags /rules /list /show /stats /signals /ask /review /shot
      (photo caption "/shot <id> pre|post" → screenshots dir + table). handle_command() is pure → unit-tested.
[x] journal/coach.py — Anthropic SDK 1.7 (AsyncAnthropic): claude-opus-5, thinking adaptive,
      client.beta.messages.tool_runner(max_iterations=ai.max_tool_calls) with @beta_async_tool wrappers over the
      READ-ONLY Tools methods (11 tools; no add/close/skip/update), system = prompts.COACH_V1 with cache_control
      ephemeral, question (+ optional signal JSON) last. Non-streaming at max_tokens 16000.
[x] parity_check(): deterministic guard — numbers in the reply not present in any tool result are listed as
      `unverified_numbers` (shown as ⚠ on Telegram/CLI, stored with the answer). Heuristic, warning only.
[x] /review <id> → client.messages.parse(output_format=Review pydantic) over tools.review_context(); REVIEW_V1;
      stored in ai_analysis(kind='review'); format_review() for Telegram
[x] Every /ask stored in ai_analysis(kind='suggestion') with model, prompt_version, question, tool calls + results,
      usage. Daily budget = count of today's 'suggestion' rows vs ai.daily_ask_limit (store=False bypasses).
[x] config: telegram_bot {enabled, allowed_chat_ids, poll_timeout}; ai {enabled, model, max_tokens, max_tool_calls,
      daily_ask_limit, weekly_report, weekly_report_cron, api_key}; prompts.py versioned (coach_v1, review_v1, weekly_v1)
[x] CLI: journal ask "<q>", journal review <id> — try the coach without Telegram
[x] tests/test_coach.py (fake client; 10 tests), tests/test_telegram_bot.py (7), tests/evals/test_coach_live.py
      (3 live cases: parity + no-directive, empty-journal no-hallucination, rule surfaced — skipped without
      ANTHROPIC_API_KEY, marker `live`). 154 pass. "Extraction test" lives in test_parser (deterministic parser).
[ ] Live: set ANTHROPIC_API_KEY, run `python -m pytest tests/evals -q` and `journal ask "how are my 4H breaks?"`
[ ] Live: config.yaml with telegram_bot.enabled + allowed_chat_ids → `python -m break_signal -c config.yaml`, send /help
```

### Phase J4 — Rules, weekly review, coach memory (2 days) — DONE 2026-09-20

```
[x] journal/rules.py + seed rules (J1); run on add/close/event (J1); violations in /close reply (J1) and in the
      alert footer: "⚠ Rules: No 1D entry when RSI > 75" from a rule_check of {direction, tf, ctx_rsi} — only
      context rules can be judged before there is an entry/stop. Seed rules now land ONCE on DB creation
      (db._init_schema, fresh only) so deleting one sticks.
[x] journal/report.py — build_metrics() (pure): period vs all-time summary, closed trades, best/worst, open,
      alerts/skips, tag + tf/direction tables, rule violations, last-30 equity curve, memories. render() → markdown.
      generate() refreshes memories, adds a WEEKLY_V1 narrative via Coach.narrative() when a coach is given
      (parity-checked), stores in ai_analysis(kind=weekly|monthly, model, prompt_version|metrics_only).
      run_scheduler(): weekly at ai.weekly_report_cron ("MON 00:15"), monthly on the 1st 00:30 UTC; pushes
      markdown (+PNG) through the alert notifiers; a failed report never kills the loop. Wired in __main__
      when ai.weekly_report and at least one notifier. CLI: journal report [--kind] [--narrative] [--dry-run]
      [--png path] [--json]. Telegram /report [weekly|monthly] (falls back to metrics-only on CoachError).
[x] journal/memory.py — derive(): deterministic, evidence-backed observations — entry tag / tf / direction /
      RSI band with n ≥ 5 and win ≥ 70 % or ≤ 35 % over 90 d; rules broken ≥ 3×. refresh() upserts by key,
      prunes unconfirmed observations the journal no longer supports, keeps confirmed ones and trader notes.
      /memories /confirm <id> /forget <id> on Telegram; `journal memories list|confirm|forget|note` on CLI;
      MCP journal_memories / journal_confirm_memory / journal_forget_memory / journal_add_memory_note (+
      journal_report); coach read tool journal_memories. Schema v2 migration adds memories.key (first use of
      the migrations table; verified on the real data/journal.db).
[x] chart_png(): equity curve + avg-R-by-tag bars; lazy matplotlib import, None when missing (not installed
      on the dev box — untested visually; unit test accepts either).
[x] tests/test_memory_report.py (13) + bot/CLI cases; 172 pass. Found & fixed while driving: PF went "inf"
      when a LOSS was declared on a +R exit (gross loss turned negative) → PF now sums only sign-matching R.
```

### Phase J5 — Pi deployment + hardening (1 day) — DONE 2026-09-20 (not yet deployed)

```
[x] docker-compose: the single ./data volume now documents state.db, journal.db, journal/screenshots/,
      backups/; env passthrough for ANTHROPIC_API_KEY, OKX_REST_URL, OKX_WS_URL; build arg AI_ENABLED.
      `docker compose config` validates.
[x] journal/backup.py: online sqlite3 backup() → data/backups/journal-<YYYYMMDD-HHMM>.db (+ .md twin via
      export.to_markdown), snapshot forced to journal_mode=DELETE so it is ONE file, prune to backup_keep,
      verify() integrity + row counts. In-process daily scheduler (journal.backup_time, default 00:05 UTC)
      wired in __main__; CLI `journal backup [--dir --keep | --verify file]`. Run for real on data/journal.db.
[x] Optional AI deps: requirements-ai.txt (anthropic, mcp) installed only with --build-arg AI_ENABLED=1;
      pyproject extra [ai] (J1) for local installs.
[x] Markdown export existed since J0; moved into journal/export.py (md/csv/json) so backup and CLI share it.
[x] README deploy section, handoff, this plan.
[ ] Deploy: on the Pi, `docker compose up -d --build`, then check `docker compose logs` for
      "Starting N watcher(s) + telegram bot + report scheduler + nightly backup".
```

### Phase J6 — Optional later

- Screenshot vision review: `/review` attaches PRE/POST PNGs as image blocks; observations stored as
  `ai_analysis.kind='vision'` and labelled *observation*, never fact
- Embedding-based similar trades (Voyage) if deterministic matching feels weak past ~500 trades
- Web dashboard (Break Signal Phase 3): FastAPI + Lightweight Charts showing lines, alerts, and trade markers
- Pine alert webhook → `signals` (source='pine') once TradingView webhooks are available on the plan

---

## 6. AI design details

### 6.1 Tool surface (shared by MCP and the Telegram coach)

| Tool | Kind | Returns |
|---|---|---|
| `market_snapshot(symbol, tf, bars=300)` | FACT (live OKX) + CALC (Engine) | `{price, atr, rsi, vol_ratio, lines:[{side,value,touches,age_bars,dist_atr}], last_signal}` |
| `journal_recent_signals(symbol?, tf?, limit)` | FACT | signal rows |
| `journal_search_trades(symbol?, tf?, direction?, outcome?, tags?, since?, until?, signal_linked?)` | FACT | trade rows + tags + events |
| `journal_get_trade(id)` | FACT | full trade incl. events, screenshots paths, violations, prior AI analysis |
| `journal_stats(period, filters)` | CALC | win_rate, pf, expectancy, avg_r, max_dd, n, streaks |
| `journal_tag_stats(period, phase)` | CALC | per tag: n, wins, losses, avg_r, pf |
| `journal_feature_stats(period)` | CALC | by tf / side / event / rsi band / vol band |
| `journal_similar_trades(symbol, direction, tf, side?, tags?, rsi?, atr_dist?, k=8)` | CALC | ranked trades + aggregate of the k |
| `journal_rule_check(proposed_trade)` | CALC | violations list |
| `journal_review_context(trade_id)` | FACT+CALC | everything REVIEW_V1 needs, pre-assembled |
| `journal_add_trade / close_trade / skip_signal / add_event / add_tag / add_screenshot` | WRITE | id + computed fields |

MCP (Claude Code) gets all tools; Claude Code's own permission prompt guards the writes.
The Telegram `/ask` coach gets **read-only** tools — writes happen only via explicit
commands, satisfying "AI must not modify journal records without confirmation".

### 6.2 Coach prompt structure (`prompts.COACH_V1`)

```
SYSTEM (cached, stable):
  You are the trading-journal coach for one trader. You analyse THEIR history; you do
  not predict markets or tell them to buy or sell.
  Hard rules:
   - Every number you state must come verbatim from a tool result. Never compute.
   - Always show sample size (n) next to any rate or average. n < 5 → say "small sample".
   - Separate the answer into: FACTS (live) / YOUR HISTORY / INTERPRETATION / YOUR DECISION.
   - Cite trade ids (#12, #17) when referring to past trades.
   - Observation ≠ cause: "7 of 10 FOMO trades lost" not "FOMO causes losses".
   - Missing information stays missing; ask, do not assume.
   - Price-action suggestions are conditional ("if price retests X and holds on a 4H close,
     that matches your best pattern"), never imperative.

USER (volatile, last):
  <question>  [+ optional current signal JSON]
```

Tool results are placed by the runner; the system prompt never changes between
requests, so it stays cached (`cache_control: {"type": "ephemeral"}`).

### 6.3 Safety / reliability rules (from source doc §20, made concrete)

| Rule | Enforcement |
|---|---|
| AI never calculates metrics | `analytics.py` is the only place numbers are made; LLM sees them as tool JSON; parity test asserts the reply quotes them |
| AI never invents trade fields | parser + tool schema: missing → `null`; `strict: true` on tool schemas |
| AI never modifies records unasked | MCP writes gated by Claude Code permissions; Telegram coach has read-only tools |
| AI never executes trades | there is no exchange write path anywhere in the codebase (already true) |
| Observations, not verdicts, in memory | `memories.content` must include counts + period; `confirmed` flag for user sign-off |
| Reproducible | every `ai_analysis` row stores `model`, `prompt_version`, `input_metrics` |

### 6.4 Claude API call shape (`coach.py`, Python, Anthropic SDK)

```python
import anthropic
from anthropic import beta_tool

client = anthropic.Anthropic()          # ANTHROPIC_API_KEY from env

@beta_tool
def journal_similar_trades(symbol: str, direction: str, tf: str, rsi: float | None = None, k: int = 8) -> str:
    """Rank the trader's past trades by similarity to a proposed setup. Returns JSON with
    the top-k trades (id, outcome, r_multiple, entry_reason, exit_reason, tags) and their aggregate."""
    return json.dumps(tools.journal_similar_trades(symbol, direction, tf, rsi=rsi, k=k))

# ... one @beta_tool per read-only function in tools.py

def ask(question: str, signal: dict | None = None) -> str:
    user = question if signal is None else f"{question}\n\nCURRENT SIGNAL:\n{json.dumps(signal)}"
    runner = client.beta.messages.tool_runner(
        model="claude-opus-5",
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=[{"type": "text", "text": prompts.COACH_V1, "cache_control": {"type": "ephemeral"}}],
        tools=[market_snapshot, journal_similar_trades, journal_tag_stats, journal_stats,
               journal_search_trades, journal_rule_check, journal_recent_signals],
        messages=[{"role": "user", "content": user}],
    )
    final = runner.until_done()
    return "".join(b.text for b in final.content if b.type == "text")
```

Telegram replies are chunked at 4 000 chars. Cost guard: `ai.max_tool_calls` (default 8)
and a daily `/ask` budget in config; the deterministic footer (§3.4) is free and always on.

---

## 7. Config additions (`config.example.yaml`)

```yaml
journal:
  db: data/journal.db
  screenshots_dir: data/journal/screenshots
  symbol_aliases: { SOL: SOL-USDT-SWAP, BTC: BTC-USDT-SWAP, ETH: ETH-USDT-SWAP }
  history_footer: true            # append "your history" to alerts when n >= 3
  account_size: 0                 # optional; enables risk_pct auto-calc from risk_amount

telegram_bot:
  enabled: false                  # command handling (separate from alert sending)
  allowed_chat_ids: []            # only these may write to the journal

ai:
  enabled: false
  model: claude-opus-5
  max_tokens: 16000
  max_tool_calls: 8
  daily_ask_limit: 30
  weekly_report: true
  weekly_report_cron: "MON 00:15"   # UTC
  # ANTHROPIC_API_KEY: read from environment; or set api_key here (config.yaml is gitignored)
```

---

## 8. Testing strategy

| Test | What it pins |
|---|---|
| `test_parser.py` | 20+ one-liners incl. Thai tags, missing fields → `None`, aliases |
| `test_analytics.py` | Fixture journal of 12 trades with hand-computed win rate / PF / expectancy / avg R / max DD / tag stats |
| `test_similar.py` | Ranking order on a fixture; ties broken by recency |
| `test_rules.py` | Each operator; `sl_moved` from events; disabled rule ignored |
| `test_tools.py` | Every tool returns JSON-serialisable output; writes return ids; `market_snapshot` mocked candles |
| `test_journal_db.py` | Schema migration idempotent; cascade deletes; UNIQUE on signals |
| `evals/` (J3) | Source doc §23 cases run against the live model, gated behind `ANTHROPIC_API_KEY`; parity check that every number in the reply appears in the tool results |

Existing 20 core tests stay untouched; the journal package imports `core` but `core`
never imports `journal`.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| Low-friction logging fails → empty journal → useless AI | One-line syntax, Telegram command right under each alert, Claude extracts from free text; backtest signals imported day 1 so there is history to talk about |
| Small samples → misleading stats | Every stat carries `n`; prompt forces "small sample" wording under n < 5; footer hidden under n < 3 |
| LLM drifts into "buy now" advice | System prompt + eval case; answer template ends in YOUR DECISION |
| API cost creep | Deterministic footer is free; `/ask` daily limit; prompt caching; weekly report is one call |
| Journal data loss on Pi SD card | `journal.db` on the same SSD/USB mount as `state.db`; nightly `.backup`; `export --md` |
| Pi has no `anthropic`/`mcp` deps | Optional `[ai]` extra; journal + footer + CLI work without it |

---

## 10. Open questions — defaults applied for J0 (2026-09-20), revisit any time

1. **Account currency and size** — **R-multiples primary.** PnL is computed only when `size` or `risk <amount>`
   is given; `risk_pct` auto-fills only if `journal.account_size` is set in config (default: unset).
2. **Word bank seed** — **source-doc examples + a few common ones**, bilingual, in `db.SEED_TAGS`
   (29 tags across SETUP / PSYCH / EXIT / MISTAKE). Rename/recategorise with `tag rename` / `tag category`.
3. **Where do you want to log most often** — not needed for J0; J1 (chat) stays before J2/J3 as planned.
4. **Screenshots** — not needed for J0; `screenshots` table + `add_screenshot()` exist, no capture path yet.

---

## 11. Next action

All planned code (J0–J5) is done. What's left is operational and needs things this dev box doesn't have:

1. `ANTHROPIC_API_KEY` → `python -m pytest tests/evals -q`, `journal ask "…"`, `journal report --narrative`.
2. Telegram: `config.yaml` with the bot token + your chat id in `telegram_bot.allowed_chat_ids` → run the
   service, send `/help`, `/trade …`, `/report`.
3. OKX reachable (VPN or the Pi's network) → `market_snapshot` in Claude Code, and the watcher's live path.
4. Pi: `AI_ENABLED=1 docker compose up -d --build`, watch the logs, wait for the first alert with a 📒 footer.

Then J6 items as wanted (screenshot vision review, embeddings, web dashboard, Pine webhook).
