"""``python -m break_signal.journal <command>`` — shell front-end to the journal.

Commands: add, close, skip, event, tag, list, show, signals, stats, export.
Resolves the DB path and symbol aliases from ``config.yaml`` when present
(``--config``), else falls back to ``data/journal.db``.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import analytics
from .db import JournalDB, now_ms
from .models import TAG_CATEGORIES, Trade
from .parser import ParseError, parse_close, parse_trade

DEFAULT_DB = "data/journal.db"


# ── formatting helpers ───────────────────────────────────────────────────────
def _ts(ms: int | None) -> str:
    if ms is None:
        return "-"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _f(x: float | None, nd: int = 2) -> str:
    if x is None:
        return "-"
    if x == float("inf"):
        return "inf"
    return f"{x:.{nd}f}"


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:.0f}%"


def _table(rows: list[list[str]], header: list[str]) -> str:
    widths = [max(len(str(c)) for c in col) for col in zip(header, *rows)] if rows else [len(h) for h in header]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines = [fmt.format(*header), fmt.format(*["-" * w for w in widths])]
    lines += [fmt.format(*[str(c) for c in r]) for r in rows]
    return "\n".join(lines)


def _trade_line(t: Trade) -> list[str]:
    return [
        f"#{t.id}", t.status, t.symbol, t.tf or "-", t.direction,
        _f(t.entry_price, 4), _f(t.sl_price, 4), _f(t.tp_price, 4), _f(t.exit_price, 4),
        t.outcome or "-", _f(t.r_multiple), _ts(t.opened_ts),
        " ".join(f"#{x}" for x in t.tags),
    ]


_TRADE_HEADER = ["id", "status", "symbol", "tf", "dir", "entry", "sl", "tp", "exit",
                 "outcome", "R", "opened (UTC)", "tags"]


def _summary_block(title: str, s: dict) -> str:
    st = s["streaks"]
    pf = s["profit_factor"]
    return (
        f"{title}\n"
        f"  n={s['n']}  W/L/BE={s['wins']}/{s['losses']}/{s['be']}  win_rate={_pct(s['win_rate'])}\n"
        f"  avg_R={_f(s['avg_r'])}  total_R={_f(s['total_r'])}  PF={_f(pf) if isinstance(pf, float) else pf}"
        f"  avg_win={_f(s['avg_win_r'])}R  avg_loss={_f(s['avg_loss_r'])}R  max_DD={_f(s['max_drawdown_r'])}R\n"
        f"  streaks: max_win={st['max_win']} max_loss={st['max_loss']} "
        f"current={st['current']} {st['current_kind'] or ''}"
        + (f"\n  PnL={_f(s['total_pnl'])} (n={s['pnl_n']})" if s["total_pnl"] is not None else "")
    )


def _group_table(groups: dict[str, dict]) -> str:
    rows = [[k, v["n"], f"{v['wins']}/{v['losses']}/{v['be']}", _pct(v["win_rate"]),
             _f(v["avg_r"]), _f(v["profit_factor"]) if isinstance(v["profit_factor"], float) else str(v["profit_factor"])]
            for k, v in groups.items()]
    return _table(rows, ["bucket", "n", "W/L/BE", "win%", "avg_R", "PF"])


# ── command handlers ─────────────────────────────────────────────────────────
def cmd_add(db: JournalDB, args, aliases) -> int:
    p = parse_trade(" ".join(args.line), aliases)
    kw = p.db_fields()
    if args.signal is not None:
        kw["signal_id"] = args.signal
    t = db.add_trade(p.symbol, p.direction, entry_tags=p.tags, **kw)
    rr = analytics.planned_rr(t.direction, t.entry_price, t.sl_price, t.tp_price)
    print(f"added trade #{t.id}: {t.symbol} {t.tf or ''} {t.direction} @ {_f(t.entry_price, 4)} "
          f"sl {_f(t.sl_price, 4)} tp {_f(t.tp_price, 4)}  planned R:R={_f(rr)}  tags={t.tags}")
    return 0


def cmd_close(db: JournalDB, args, aliases) -> int:
    p = parse_close(" ".join(args.line))
    t = db.close_trade(p.trade_id, p.exit_price, outcome=p.outcome, exit_reason=p.reason,
                       exit_tags=p.tags)
    print(f"closed trade #{t.id}: {t.outcome} R={_f(t.r_multiple)} pnl={_f(t.pnl_amount)} "
          f"exit_tags={t.exit_tags}")
    return 0


def cmd_skip(db: JournalDB, args, aliases) -> int:
    t = db.skip_signal(args.signal_id, reason=" ".join(args.reason) or None, tags=args.tag)
    print(f"recorded skip #{t.id} on signal #{args.signal_id} ({t.symbol} {t.tf} {t.direction})")
    return 0


def cmd_event(db: JournalDB, args, aliases) -> int:
    data = {}
    for kv in args.data:
        k, _, v = kv.partition("=")
        try:
            data[k] = float(v)
        except ValueError:
            data[k] = v
    e = db.add_event(args.trade_id, args.type, data)
    print(f"event #{e.id} on trade #{e.trade_id}: {e.type} {e.data}")
    return 0


def cmd_tag(db: JournalDB, args, aliases) -> int:
    if args.tag_cmd == "list":
        tags = db.list_tags(args.category)
        print(_table([[t.category, t.name] for t in tags], ["category", "name"]))
    elif args.tag_cmd == "add":
        t = db.get_or_create_tag(args.name, args.category)
        print(f"tag '{t.name}' ({t.category})")
    elif args.tag_cmd == "rename":
        t = db.rename_tag(args.old, args.new)
        print(f"renamed → '{t.name}'")
    elif args.tag_cmd == "category":
        t = db.set_tag_category(args.name, args.category)
        print(f"'{t.name}' → {t.category}")
    elif args.tag_cmd == "attach":
        db.attach_tags(args.trade_id, args.names, args.phase)
        print(f"attached {args.names} ({args.phase}) to #{args.trade_id}")
    elif args.tag_cmd == "detach":
        db.detach_tag(args.trade_id, args.name)
        print(f"detached '{args.name}' from #{args.trade_id}")
    return 0


def _filtered(db: JournalDB, args) -> list[Trade]:
    return db.list_trades(
        symbol=getattr(args, "symbol", None), tf=getattr(args, "tf", None),
        direction=getattr(args, "direction", None), status=getattr(args, "status", None),
        outcome=getattr(args, "outcome", None), tags=getattr(args, "tag", None) or None,
        since=analytics.period_to_since(getattr(args, "period", None)),
        limit=getattr(args, "limit", None),
    )


def cmd_list(db: JournalDB, args, aliases) -> int:
    trades = _filtered(db, args)
    if not trades:
        print("no trades")
        return 0
    print(_table([_trade_line(t) for t in trades], _TRADE_HEADER))
    return 0


def cmd_show(db: JournalDB, args, aliases) -> int:
    t = db.get_trade(args.trade_id)
    if t is None:
        print(f"no trade #{args.trade_id}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(t.to_dict(), ensure_ascii=False, indent=2))
        return 0
    rr = analytics.planned_rr(t.direction, t.entry_price, t.sl_price, t.tp_price)
    print(f"trade #{t.id}  {t.status}  {t.symbol} {t.tf or '-'} {t.direction}")
    print(f"  entry {_f(t.entry_price, 4)}  sl {_f(t.sl_price, 4)}  tp {_f(t.tp_price, 4)}  "
          f"exit {_f(t.exit_price, 4)}  planned R:R {_f(rr)}")
    print(f"  size {_f(t.position_size, 4)}  risk {_f(t.risk_amount)} ({_f(t.risk_pct)}%)  "
          f"lev {_f(t.leverage)}  fees {_f(t.fees)}  conf {t.confidence or '-'}")
    print(f"  outcome {t.outcome or '-'}  R {_f(t.r_multiple)}  pnl {_f(t.pnl_amount)}")
    print(f"  opened {_ts(t.opened_ts)}  closed {_ts(t.closed_ts)}  session {t.ctx_session or '-'}")
    print(f"  entry tags: {t.entry_tags}\n  exit tags:  {t.exit_tags}")
    if t.entry_reason:
        print(f"  entry reason: {t.entry_reason}")
    if t.exit_reason:
        print(f"  exit reason:  {t.exit_reason}")
    if t.notes:
        print(f"  notes: {t.notes}")
    if t.signal:
        s = t.signal
        print(f"  signal #{s.id}: {s.event} {s.side} line {s.line_price} touches={s.touches} "
              f"rsi={s.rsi} vol={s.vol_ratio} atr_dist={s.atr_dist} @ {_ts(s.candle_ts)}")
    for e in t.events:
        print(f"  event {_ts(e.event_ts)} {e.type} {e.data}")
    for sc in t.screenshots:
        print(f"  screenshot {sc.phase}: {sc.path}")
    return 0


def cmd_signals(db: JournalDB, args, aliases) -> int:
    sigs = db.list_signals(symbol=args.symbol, tf=args.tf, source=args.source, limit=args.limit)
    if not sigs:
        print("no signals")
        return 0
    rows = [[f"#{s.id}", s.source, s.symbol, s.tf, s.event, s.side, _f(s.price, 4),
             _f(s.line_price, 4), s.touches, _f(s.rsi, 1), _f(s.vol_ratio), _ts(s.candle_ts)]
            for s in sigs]
    print(_table(rows, ["id", "src", "symbol", "tf", "event", "side", "price", "line",
                        "touch", "rsi", "vol", "candle (UTC)"]))
    return 0


def cmd_stats(db: JournalDB, args, aliases) -> int:
    trades = _filtered(db, args)
    label = f"period={args.period or 'all'}" + (f" symbol={args.symbol}" if args.symbol else "") \
        + (f" tf={args.tf}" if args.tf else "")
    if args.json:
        out = {"filters": label, "summary": analytics.summarize(trades)}
        if args.by in ("tags", "all"):
            out["tags"] = analytics.tag_stats(trades)
        if args.by in ("features", "all"):
            out["features"] = analytics.feature_stats(trades)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    print(_summary_block(f"Summary ({label})", analytics.summarize(trades)))
    if args.by in ("tags", "all"):
        print("\nBy tag (entry):")
        print(_group_table(analytics.tag_stats(trades, "ENTRY")) or "  (none)")
        print("\nBy tag (exit):")
        print(_group_table(analytics.tag_stats(trades, "EXIT")) or "  (none)")
    if args.by in ("features", "all"):
        for name, groups in analytics.feature_stats(trades).items():
            if groups:
                print(f"\nBy {name}:")
                print(_group_table(groups))
    return 0


def cmd_export(db: JournalDB, args, aliases) -> int:
    trades = list(reversed(_filtered(db, args)))  # oldest first for reading
    if args.format == "json":
        text = json.dumps([t.to_dict() for t in trades], ensure_ascii=False, indent=2)
    elif args.format == "csv":
        buf = io.StringIO()
        cols = ["id", "status", "signal_id", "symbol", "tf", "direction", "entry_price", "sl_price",
                "tp_price", "exit_price", "position_size", "risk_amount", "risk_pct", "fees",
                "outcome", "r_multiple", "pnl_amount", "opened_ts", "closed_ts", "ctx_session",
                "confidence", "entry_tags", "exit_tags", "entry_reason", "exit_reason", "notes"]
        w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        for t in trades:
            d = t.to_dict()
            d["entry_tags"] = "|".join(t.entry_tags)
            d["exit_tags"] = "|".join(t.exit_tags)
            w.writerow({c: d.get(c) for c in cols})
        text = buf.getvalue()
    else:  # markdown
        lines = ["# Trading journal export", "",
                 f"_{len(trades)} trades, exported {_ts(now_ms())} UTC_", ""]
        lines.append(_summary_block("## Summary", analytics.summarize(trades)))
        lines.append("")
        for t in trades:
            lines.append(f"## #{t.id} {t.symbol} {t.tf or ''} {t.direction} — {t.status}"
                         + (f" {t.outcome} {_f(t.r_multiple)}R" if t.outcome else ""))
            lines.append(f"- opened {_ts(t.opened_ts)} · closed {_ts(t.closed_ts)}")
            lines.append(f"- entry {_f(t.entry_price, 4)} · sl {_f(t.sl_price, 4)} · "
                         f"tp {_f(t.tp_price, 4)} · exit {_f(t.exit_price, 4)}")
            if t.entry_tags:
                lines.append(f"- entry tags: {', '.join(t.entry_tags)}")
            if t.exit_tags:
                lines.append(f"- exit tags: {', '.join(t.exit_tags)}")
            if t.entry_reason:
                lines.append(f"- entry reason: {t.entry_reason}")
            if t.exit_reason:
                lines.append(f"- exit reason: {t.exit_reason}")
            for e in t.events:
                lines.append(f"- event {_ts(e.event_ts)}: {e.type} {e.data}")
            lines.append("")
        text = "\n".join(lines)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


# ── argparse ─────────────────────────────────────────────────────────────────
def _add_filters(p: argparse.ArgumentParser, with_limit: bool = True) -> None:
    p.add_argument("--symbol")
    p.add_argument("--tf")
    p.add_argument("--direction", choices=["LONG", "SHORT"])
    p.add_argument("--status", choices=["OPEN", "CLOSED", "SKIPPED"])
    p.add_argument("--outcome", choices=["WIN", "LOSS", "BE"])
    p.add_argument("--tag", action="append", help="require this tag (repeatable)")
    p.add_argument("--period", help="30d, 12w, 6m, 1y, all (default all)")
    if with_limit:
        p.add_argument("--limit", type=int)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m break_signal.journal", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", help=f"journal sqlite path (default: config journal.db or {DEFAULT_DB})")
    ap.add_argument("-c", "--config", default="config.yaml", help="config.yaml (optional)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="log a new trade from a one-line description")
    p.add_argument("line", nargs="+")
    p.add_argument("--signal", type=int, help="link to a signal id")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("close", help="close: <id> <exit> [win|loss|be] [#tags] [reason]")
    p.add_argument("line", nargs="+")
    p.set_defaults(fn=cmd_close)

    p = sub.add_parser("skip", help="record a deliberate pass on a signal")
    p.add_argument("signal_id", type=int)
    p.add_argument("reason", nargs="*")
    p.add_argument("--tag", action="append")
    p.set_defaults(fn=cmd_skip)

    p = sub.add_parser("event", help="log something that happened during a trade")
    p.add_argument("trade_id", type=int)
    p.add_argument("type", help="sl_moved | tp_moved | partial_close | added | note")
    p.add_argument("data", nargs="*", help="key=value pairs, e.g. from=225 to=222")
    p.set_defaults(fn=cmd_event)

    p = sub.add_parser("tag", help="manage the word bank")
    ts = p.add_subparsers(dest="tag_cmd", required=True)
    q = ts.add_parser("list"); q.add_argument("--category", choices=TAG_CATEGORIES)
    q = ts.add_parser("add"); q.add_argument("name"); q.add_argument("--category", default="OTHER", choices=TAG_CATEGORIES)
    q = ts.add_parser("rename"); q.add_argument("old"); q.add_argument("new")
    q = ts.add_parser("category"); q.add_argument("name"); q.add_argument("category", choices=TAG_CATEGORIES)
    q = ts.add_parser("attach"); q.add_argument("trade_id", type=int); q.add_argument("phase", choices=["ENTRY", "EXIT"]); q.add_argument("names", nargs="+")
    q = ts.add_parser("detach"); q.add_argument("trade_id", type=int); q.add_argument("name")
    p.set_defaults(fn=cmd_tag)

    p = sub.add_parser("list", help="list trades")
    _add_filters(p)
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("show", help="show one trade in full")
    p.add_argument("trade_id", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("signals", help="list stored breakout signals")
    p.add_argument("--symbol"); p.add_argument("--tf"); p.add_argument("--source")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(fn=cmd_signals)

    p = sub.add_parser("stats", help="deterministic performance numbers")
    _add_filters(p, with_limit=False)
    p.add_argument("--by", choices=["summary", "tags", "features", "all"], default="all")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_stats)

    p = sub.add_parser("export", help="dump the journal")
    _add_filters(p, with_limit=False)
    p.add_argument("--format", choices=["md", "csv", "json"], default="md")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_export)
    return ap


def _open_db(args) -> tuple[JournalDB, dict[str, str]]:
    db_path = args.db
    aliases: dict[str, str] = {}
    account_size = None
    cfg_path = Path(args.config)
    if cfg_path.exists():
        from ..config import load_config
        cfg = load_config(cfg_path)
        aliases = cfg.journal.symbol_aliases
        account_size = cfg.journal.account_size
        db_path = db_path or cfg.journal.db
    else:
        from ..config import JournalCfg
        aliases = JournalCfg().symbol_aliases
    return JournalDB(db_path or DEFAULT_DB, account_size=account_size), aliases


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db, aliases = _open_db(args)
    try:
        return args.fn(db, args, aliases)
    except (ParseError, ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
