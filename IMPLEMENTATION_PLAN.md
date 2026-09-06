# Break Signal — Auto Trendline Detection & Breakout Alerts

**Goal:** automatically draw trendlines on a chart (no manual drawing), watch every closing candle, and push a notification the moment price breaks a line.

**Status:** planning
**Date:** 2026-09-06

---

## 1. Decision: build or integrate?

### Recommendation — **Do both, in two phases. Start with TradingView (Pine Script).**

| | TradingView Pine indicator | Standalone Python service |
|---|---|---|
| Time to first working alert | ~1 day | ~1 week |
| Chart visuals | Free, already perfect | Must rebuild |
| Data feed | Free, included | Binance public API (free, no key) |
| Notification | TradingView mobile push / email / webhook | Telegram / Discord / LINE, with chart image |
| Watch many symbols at once | ✗ 1 alert per symbol+timeframe, and alert count is capped by your plan tier | ✓ 50–200 symbols in one process |
| Backtest the rules | Awkward | ✓ Easy, offline on historical klines |
| Custom logic (volume filter, retest, multi-timeframe confluence) | Limited by Pine runtime | ✓ Unlimited |
| Runs when your browser is closed | ✓ Server-side | ✓ if hosted |
| Cost | Free tier works but 1 active alert; Essential plan for ~20+ | Free (VPS ~$5/mo, or run on your PC) |

**Why this order:** the hard part is not the app, it is the *trendline rules* — which pivots count, how much slack before "break", how to avoid false signals. Pine Script lets you see the lines drawn on the real chart and tune those rules visually in hours instead of guessing. Once the rules are proven, port the exact same math to Python for multi-symbol scanning.

**Do not** build a custom charting UI as step one. If you eventually want your own web chart, use **TradingView Lightweight Charts** (free, open source, MIT) — same look, no license issue.

### Phase map

- **Phase 1 — TradingView Pine v6 indicator.** Auto trendlines + break alert on SOLUSDT 1D/4H. Proves the algorithm.
- **Phase 2 — Python watcher service.** Same algorithm, Binance WebSocket, scans a symbol list, Telegram push with rendered chart image.
- **Phase 3 (optional) — Web dashboard.** FastAPI + Lightweight Charts to review active lines and alert history.

---

## 2. The algorithm (shared by both phases)

This is the core spec. Both Pine and Python implement the same steps so signals match.

### 2.1 Pivot detection

Use fractal pivots: a bar is a **pivot high** if its high is the highest within `L` bars left and `L` bars right; **pivot low** is the mirror.

- `L` (pivot lookback) default **5** on 1D, **8** on 4H, **10** on 1H.
- A pivot is only confirmed `L` bars after it happens — this lag is unavoidable and is what keeps lines stable.
- Keep the last `P = 20` pivot highs and `20` pivot lows in a rolling buffer.

### 2.2 Candidate trendline generation

For every pair of pivot highs `(a, b)` where `a` is older than `b`:

```
slope = (price[b] - price[a]) / (bar[b] - bar[a])
line(x) = price[a] + slope * (x - bar[a])
```

- **Resistance lines** come from pivot highs. **Support lines** come from pivot lows.
- Require a minimum horizontal separation: `bar[b] - bar[a] >= min_bars` (default **10**) so you don't fit noise.
- Cap the pair count: only the most recent `P` pivots → at most `P*(P-1)/2 = 190` candidates per side. Cheap.

### 2.3 Validity filter — the important part

A resistance line is invalid if price already broke through it *between* its two anchors. Walk every bar from `bar[a]` to the current bar:

```
tolerance = atr_mult_valid * ATR(14)      # default atr_mult_valid = 0.10
if close[i] > line(i) + tolerance  →  line is broken/invalid, discard
```

- Use **close** (not high) for the violation test. Wicks poking through a resistance is normal; a close through it means the line is dead.
- Allow at most `max_violations` closes through (default **0**; set 1 for a looser mode).
- Mirror the logic for support (`close[i] < line(i) - tolerance`).

### 2.4 Scoring & selection

Score each surviving candidate, keep the best `N = 3` per side:

```
touches   = count of bars where |high[i] - line(i)| <= atr_mult_touch * ATR   # 0.25 default
span      = (current_bar - bar[a]) / lookback_window                          # 0..1, longer = better
recency   = 1 / (1 + current_bar - bar[b])                                    # anchor b should be recent
score     = 3.0*touches + 2.0*span + 1.5*recency
```

- Require `touches >= 3` (the 2 anchors plus at least one more) or the line is just a random connection of two points.
- Deduplicate: if two lines have slopes within 10% *and* are within `0.5*ATR` of each other at the current bar, keep only the higher-scoring one.
- Optional refinement: once touches are known, refit the line by least squares over all touch points, then re-run the validity check.

### 2.5 Break detection

On **each closed bar** (never intrabar — that is the #1 source of false alerts):

```
buffer = atr_mult_break * ATR(14)         # default 0.20
resistance break (bullish):  close > line(now) + buffer
support break   (bearish):   close < line(now) - buffer
```

Optional confirmation filters (all configurable, default off except volume):

| Filter | Rule | Default |
|---|---|---|
| Volume | `volume > vol_mult * SMA(volume, 20)`, `vol_mult = 1.5` | ON |
| Body | candle body >= 50% of its range (not a doji) | ON |
| Two-bar | require 2 consecutive closes beyond the line | OFF |
| Line age | line must be at least `min_bars` old | ON |
| Retest | after break, alert again when price returns within `0.3*ATR` of the line and bounces | OFF (Phase 2) |

### 2.6 State & deduplication

- Each line gets a stable id: `hash(side, bar[a], bar[b], timeframe, symbol)`.
- After a line fires a break alert → mark it `broken`, stop tracking it. It will not re-alert.
- Alerts are keyed by `(symbol, timeframe, line_id)` so a restart of the service cannot double-send.

---

## 3. Phase 1 — TradingView Pine Script indicator

### 3.1 Deliverable

`pine/break_signal.pine` — a Pine v6 **indicator** (`overlay=true`, `max_lines_count=500`) that:

1. Draws the top 3 support + top 3 resistance trendlines, extended right.
2. Colors a line red/green, dashes it when broken.
3. Plots a triangle marker on the break bar.
4. Fires `alert()` with a JSON-ish message.

### 3.2 Structure sketch

```pine
//@version=6
indicator("Break Signal — Auto Trendlines", overlay=true, max_lines_count=500)

// ── inputs ──────────────────────────────────────────────
pivotLen    = input.int(5,   "Pivot lookback",        minval=2)
maxPivots   = input.int(20,  "Pivots kept per side",  minval=5, maxval=30)
minBars     = input.int(10,  "Min bars between anchors")
minTouches  = input.int(3,   "Min touches")
atrValid    = input.float(0.10, "Validity tolerance (ATR)")
atrTouch    = input.float(0.25, "Touch tolerance (ATR)")
atrBreak    = input.float(0.20, "Break buffer (ATR)")
useVolume   = input.bool(true,  "Require volume confirmation")
volMult     = input.float(1.5,  "Volume multiple")
maxLines    = input.int(3,   "Lines per side")

// ── state ───────────────────────────────────────────────
var array<int>   phBar = array.new_int()
var array<float> phVal = array.new_float()
var array<int>   plBar = array.new_int()
var array<float> plVal = array.new_float()
var array<line>  drawn = array.new<line>()

atr = ta.atr(14)

// ── 1. collect pivots ───────────────────────────────────
ph = ta.pivothigh(pivotLen, pivotLen)
pl = ta.pivotlow (pivotLen, pivotLen)
if not na(ph)
    array.push(phBar, bar_index - pivotLen), array.push(phVal, ph)
    if array.size(phBar) > maxPivots
        array.shift(phBar), array.shift(phVal)
// ... same for pl

// ── 2-4. build candidates, validate, score, keep best N ─
// f_buildLines(bars, vals, isResistance) => returns [slope, b0, anchorBar, score]
//   for each pair -> slope -> walk bars for close-through violation -> count touches -> score

// ── 5. break test on the confirmed close ────────────────
// for each kept line:
//   lineNow = b0 + slope * (bar_index - anchorBar)
//   volOk   = not useVolume or volume > ta.sma(volume, 20) * volMult
//   bodyOk  = math.abs(close - open) >= (high - low) * 0.5
//   brokeUp = close > lineNow + atr*atrBreak and volOk and bodyOk
//   if brokeUp and barstate.isconfirmed
//       alert(msg, alert.freq_once_per_bar_close)
```

### 3.3 Alert message payload

Make it webhook-ready from day one so Phase 2 can consume it:

```
{"sym":"{{ticker}}","tf":"{{interval}}","event":"break_up",
 "price":{{close}},"time":"{{timenow}}","line":"res_1"}
```

### 3.4 Setup steps

1. TradingView → Pine Editor → paste → **Add to chart**.
2. Tune `pivotLen` on SOLUSDT 1D until the drawn lines match the ones you drew by hand in your screenshot.
3. Right-click chart → **Add alert** → Condition = *Break Signal* → *Any alert() function call* → Frequency = *Once per bar close* → Notifications = **App push** (+ Webhook URL if you already have Phase 2 running).
4. Repeat per symbol/timeframe you care about.

**Plan limits:** free tier = 1 active alert (5 on some regions), Essential = 20, Plus = 100. If you need more than ~10 symbols, that alone justifies Phase 2. Webhook-to-URL requires a paid plan.

### 3.5 Tuning checklist

- Lines look too jumpy → raise `pivotLen`.
- Too few lines → lower `minTouches` to 2 or raise `maxPivots`.
- Alerts fire on wick noise → raise `atrBreak` to 0.3–0.4.
- Missing obvious breaks → lower `atrBreak`, turn off volume filter.

---

## 4. Phase 2 — Python watcher service

### 4.1 Why

Multi-symbol scanning, no alert-count cap, backtestable, chart image in the notification, and it costs nothing beyond a small VPS (or just leave it running on your PC).

### 4.2 Stack

| Concern | Choice | Note |
|---|---|---|
| Language | Python 3.11+ | numpy/pandas make the line math trivial |
| Market data | **Binance public REST + WebSocket** | `/api/v3/klines` for history, `<symbol>@kline_<tf>` stream for live. **No API key needed** for public market data — do not add keys, the service never trades |
| Numerics | numpy, pandas | |
| Chart image | mplfinance | render the last ~120 candles + lines, attach to the alert |
| Notification | **Telegram Bot API** | free, instant, images supported, 2-minute setup via @BotFather |
| State | SQLite (`state.db`) | active lines, sent alerts, last processed candle |
| Scheduling | asyncio loop on WebSocket candle-close events | REST poll as a fallback |
| Config | `config.yaml` | symbols, timeframes, all thresholds from §2 |
| Packaging | Docker + `docker compose up -d` | or `python -m break_signal` locally |

> LINE Notify was discontinued in 2025 — if you want LINE, it must be the LINE Messaging API (push message), which needs an official account. **Telegram is the low-friction default**; Discord webhook is a 1-line alternative.

### 4.3 File layout

```
break_signal/
├── config.yaml               # symbols, timeframes, thresholds, channels
├── docker-compose.yml
├── requirements.txt
├── src/break_signal/
│   ├── __main__.py           # entrypoint, asyncio runner
│   ├── config.py             # pydantic settings model
│   ├── data/
│   │   ├── binance_rest.py   # klines history + backfill
│   │   └── binance_ws.py     # live kline stream, emits on candle close
│   ├── core/
│   │   ├── pivots.py         # §2.1
│   │   ├── trendline.py      # §2.2-2.4  (Line dataclass, build/validate/score)
│   │   ├── breakout.py       # §2.5 detection + filters
│   │   └── state.py          # §2.6 SQLite dedupe
│   ├── notify/
│   │   ├── base.py           # Notifier protocol
│   │   ├── telegram.py
│   │   └── discord.py
│   ├── render/chart.py       # mplfinance snapshot with lines drawn
│   └── backtest/replay.py    # run the rules over history, output a CSV of signals
└── tests/
    ├── test_pivots.py
    ├── test_trendline.py     # synthetic data with a known line
    └── test_breakout.py      # golden fixtures: the SOL Jun-Aug 2026 downtrend break
```

### 4.4 Core data model

```python
@dataclass(frozen=True)
class Trendline:
    side: Literal["support", "resistance"]
    anchor_a: int          # bar index
    anchor_b: int
    price_a: float
    slope: float           # price per bar
    touches: int
    score: float
    created_at: datetime

    def value_at(self, bar: int) -> float:
        return self.price_a + self.slope * (bar - self.anchor_a)

    @property
    def id(self) -> str:
        return sha1(f"{self.side}{self.anchor_a}{self.anchor_b}").hexdigest()[:12]
```

### 4.5 Runtime flow

```
startup
  └─ for each (symbol, timeframe):
       backfill 500 candles via REST → compute pivots → build lines → persist

live
  └─ WebSocket kline stream
       on candle close (k.x == true):
         append candle
         re-run pivot + line build (cheap: <5 ms for 500 bars)
         for each active line:
           if break condition met and not already alerted:
             render chart PNG
             send Telegram message + image
             mark line broken in SQLite
```

### 4.6 Notification format

```
🔴 SOLUSDT · 1D — RESISTANCE BREAK ↑

Price   105.77  (+2.51%)
Line    descending resistance, 4 touches, 62 bars old
Broke   105.77 vs line 101.40  (+0.43 ATR)
Volume  2.12M  (1.8× avg) ✅
RSI     67.4

[chart.png]
```

### 4.7 Deployment options

1. **Local** — `python -m break_signal`, keep the terminal open. Fine for testing.
2. **Docker on your PC** — `docker compose up -d`, restarts with the machine.
3. **VPS** (Hetzner/DigitalOcean ~$5/mo) — the only option that survives your PC sleeping. Recommended once it works.

Note: Binance API is geo-restricted in some regions. If `api.binance.com` is blocked, use `api1/api2/api3.binance.com`, or swap the data module for Bybit/OKX — the `data/` package is deliberately isolated so this is a one-file change.

---

## 5. Phase 3 (optional) — Web dashboard

Only if you want to *see* the lines outside TradingView.

- FastAPI backend exposing `/api/candles`, `/api/lines`, `/api/alerts`.
- Frontend: **TradingView Lightweight Charts** (`lightweight-charts` npm, free/MIT) — candlesticks + `createPriceLine`/line series for each trendline.
- Alert history table, per-symbol on/off toggles, live threshold editing.
- Estimated: 2–3 days.

---

## 6. Milestones

| # | Deliverable | Done when | Est. |
|---|---|---|---|
| M1 | Pine indicator draws lines | Lines on SOLUSDT 1D visually match hand-drawn ones | 0.5 d |
| M2 | Pine break alert fires | Phone push arrives on a real break | 0.5 d |
| M3 | Rules tuned | ≤1 false signal per 20 bars on 3 months of SOL history | 1 d |
| M4 | Python core + tests | `test_trendline.py` reproduces the Pine lines on the same data | 2 d |
| M5 | Telegram alerts live | Real break on SOLUSDT 1H pushes to Telegram with chart image | 1 d |
| M6 | Backtest report | CSV of every signal over 12 months + hit-rate summary | 1 d |
| M7 | Multi-symbol + deploy | 20 symbols × 3 timeframes running 24/7 in Docker | 1 d |

---

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| **Repainting** — pivots confirm `L` bars late, so a line can appear "after" the fact | Accept the lag; never alert on unconfirmed bars. Backtest must simulate the same lag |
| False breaks (wick through, close back inside) | ATR buffer + volume + body filters; optionally require 2 closes |
| Too many lines / visual noise | Hard cap at 3 per side, dedupe near-identical slopes |
| TradingView alert quota | The reason Phase 2 exists |
| Exchange API downtime / geo-block | Reconnect with backoff; REST poll fallback; swappable data module |
| Alert spam on restart | SQLite dedupe keyed by line id; on boot, mark historical breaks as already-sent |
| Over-fitting thresholds to SOL | Validate on BTC, ETH, and one low-cap before locking defaults |

---

## 8. Locked decisions (answered 2026-09-06)

| Question | Decision | Consequence |
|---|---|---|
| Symbols & timeframes | **SOLUSDT, 1D + 4H** | 2 alerts on TradingView — fits even a free/Essential plan |
| Notification | **Telegram + Discord** (both) | Fan-out relay on the Pi; `notify/` gets two adapters behind one interface |
| Exchange | **OKX futures** (perpetual) | Chart symbol is `OKX:SOLUSDT.P`; Phase 2 uses the OKX V5 API, **not** Binance |
| Signal style | **Strict / high-quality** | `atrBreak=0.30`, `minTouches=3`, `maxViolations=0`, volume + body filters ON |
| Hosting | **Raspberry Pi 5** | ARM64 Docker image; runs 24/7 at home, no VPS bill |

### Changes this forces on §4

- **Data module is OKX, not Binance.** `data/okx_rest.py` → `GET /api/v5/market/candles` (`instId=SOL-USDT-SWAP`, `bar=1D`/`4H`); `data/okx_ws.py` → `wss://ws.okx.com:8443/ws/v5/business`, channel `candle1D` / `candle4H`. Public market data needs **no API key** — the service never places orders, so do not add trading keys.
  - OKX returns candles **newest-first** and the last array element flags whether the bar is closed (`"1"`). Reverse the array and only act on confirmed bars.
  - OKX history endpoint is `/api/v5/market/history-candles` for the backfill beyond ~100 bars.
- **Pi 5 specifics.** `python:3.11-slim-bookworm` is multi-arch, so `docker compose up -d` works as-is on ARM64. Watch two things: mplfinance pulls matplotlib (slow first build — expect ~10 min, use `piwheels` or a prebuilt wheel), and SD-card wear from SQLite writes — put `state.db` on an SSD/USB or set `PRAGMA journal_mode=WAL` with infrequent commits.
- **Two notifiers.** `notify/base.py` defines `Notifier.send(text, image_bytes)`; `telegram.py` uses `sendPhoto` with a caption, `discord.py` posts multipart to a webhook URL. Both are listed in `config.yaml` under `channels:` and failures are independent — Discord being down must not block Telegram.

---

## 9. Phase 1 status — DONE (M1 deliverable written)

`pine/break_signal.pine` implements §2 end to end: fractal pivots → pairwise candidates → close-through validity filter → touch/span/recency scoring → dedupe → strict break confirmation → JSON `alert()`.

### Load it

1. TradingView → open **`OKX:SOLUSDT.P`**, set timeframe **1D**.
2. Pine Editor → paste the file → **Add to chart**.
3. Leave *Auto-tune pivot length* ON — it picks `5` on 1D and `8` on 4H automatically.

### Tune it (M3)

Compare the auto-drawn lines to the ones drawn by hand in the reference screenshot — the descending resistance off the May high through the July highs, and the July–August support. Then:

| Symptom | Fix |
|---|---|
| Lines too jumpy / noisy anchors | raise pivot lookback (turn auto-tune off, try 7 on 1D) |
| No lines drawn at all | lower *Min touches* to 2, or raise *Pivots kept per side* to 14 |
| Alerts on wick noise | raise *Break buffer* to 0.40–0.50 |
| Missing the obvious breaks | lower *Break buffer* to 0.20, turn off *Require volume expansion* |
| "loop takes too long" error | *Pivots kept per side* → 8, *Validation window* → 200 |

The info table (top-right) lists each active line with its touch count and how many ATR price sits from it — that is the fastest way to see why a line was or wasn't kept.

### Wire the alert

Right-click chart → **Add alert** → Condition **Break Signal** → **Any alert() function call** → Frequency **Once Per Bar Close**. Repeat for 4H.

- **Free / Essential plan:** notification = *App push*. Done — signals hit your phone.
- **Pro+ plan:** notification = *Webhook URL* pointed at the Pi 5 relay. The message is already JSON with the same field names the Phase-2 service emits, so one relay handles both sources.

### Alert payload

```json
{"symbol":"SOLUSDT.P","exchange":"OKX","tf":"1D","event":"break_up",
 "side":"resistance","price":105.77,"line":101.40,"atr_dist":0.43,
 "touches":4,"age_bars":62,"vol_ratio":1.81,"rsi":67.4,
 "time":"2026-09-06T00:00:00Z"}
```

> Discord webhooks expect `{"content": "..."}`, so TradingView cannot post to Discord directly — the Pi relay must wrap it. Telegram likewise needs `sendMessage`. This is the main argument for going to Phase 2 rather than paying for TradingView webhooks.

---

## 10. Next action

**M2 — verify a real alert fires.** Load the indicator on 1D, set the alert, and wait for (or replay) a break. Confirm the push arrives and the JSON is well-formed.

Then **M4** — port `core/` to Python and assert it reproduces the exact same lines on the same OKX candles. That test is the bridge between the two phases; if the numbers match, the Pi service is trustworthy.
