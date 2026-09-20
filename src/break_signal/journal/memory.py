"""Coach memory: evidence-backed observations, never personality judgements.

Memories are DERIVED from the journal by deterministic rules (thresholds on
``analytics`` output), keyed so a re-run updates the same row instead of
duplicating it. The LLM never writes memories; it only reads them. The
trader can ``confirm`` one ("yes, that's me") or ``forget`` it.

Kinds:
- ``pattern``: a tag or feature bucket with enough trades and a lopsided result
- ``rule``:    a rule violated repeatedly
"""
from __future__ import annotations

import json
from typing import Any

from . import analytics
from .db import JournalDB, now_ms

LOOKBACK = "90d"
MIN_N = 5            # trades before a pattern is worth remembering
STRONG_WIN = 0.70    # win_rate at or above → "works for you"
WEAK_WIN = 0.35      # win_rate at or below → "hurts you"
MIN_VIOLATIONS = 3   # repeated rule breaks


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:.0f}%"


def _r(x: float | None) -> str:
    return "-" if x is None else f"{x:+.2f}R"


def derive(db: JournalDB, lookback: str = LOOKBACK, now_ms_: int | None = None) -> list[dict]:
    """Compute the observations the journal currently supports (pure; no writes)."""
    now = now_ms_ if now_ms_ is not None else now_ms()
    since = analytics.period_to_since(lookback, now)
    trades = db.list_trades(since=since)
    out: list[dict] = []

    def pattern(key: str, label: str, s: dict, ids: list[int]) -> None:
        if s["n"] < MIN_N or s["win_rate"] is None:
            return
        if s["win_rate"] >= STRONG_WIN:
            verdict = "has worked for you"
        elif s["win_rate"] <= WEAK_WIN:
            verdict = "has not worked for you"
        else:
            return
        out.append({
            "type": "pattern", "key": key,
            "content": (f"{label}: {s['n']} trades in the last {lookback}, {s['wins']} W / {s['losses']} L / "
                        f"{s['be']} BE (win {_pct(s['win_rate'])}, avg {_r(s['avg_r'])}) — {verdict}"),
            "evidence": {"trade_ids": ids, "period": lookback, "n": s["n"], "win_rate": s["win_rate"],
                         "avg_r": s["avg_r"]},
        })

    closed = analytics.closed(trades)
    for tag, s in analytics.tag_stats(trades, "ENTRY").items():
        ids = [t.id for t in closed if tag in t.entry_tags]
        pattern(f"tag:{tag.lower()}", f"Entry tag '{tag}'", s, ids)
    feats = analytics.feature_stats(trades)
    for tf, s in feats["tf"].items():
        pattern(f"tf:{tf}", f"{tf} trades", s, [t.id for t in closed if t.tf == tf])
    for d, s in feats["direction"].items():
        pattern(f"direction:{d}", f"{d} trades", s, [t.id for t in closed if t.direction == d])
    for band, s in feats["rsi_band"].items():
        pattern(f"rsi:{band}", f"Entries with RSI {band}", s,
                [t.id for t in closed if analytics.rsi_band(t.ctx_rsi) == band])

    # repeated rule violations
    rows = db.conn.execute(
        "SELECT r.id AS rule_id, r.name, v.trade_id FROM rule_violations v JOIN rules r ON r.id = v.rule_id "
        "JOIN trades t ON t.id = v.trade_id WHERE t.opened_ts >= ? ORDER BY r.id, v.trade_id",
        (since or 0,)).fetchall()
    by_rule: dict[int, dict] = {}
    for r in rows:
        e = by_rule.setdefault(r["rule_id"], {"name": r["name"], "ids": []})
        e["ids"].append(r["trade_id"])
    for rid, e in by_rule.items():
        if len(e["ids"]) >= MIN_VIOLATIONS:
            out.append({
                "type": "rule", "key": f"rule:{rid}",
                "content": f"Rule '{e['name']}' broken {len(e['ids'])} times in the last {lookback} "
                           f"({', '.join(f'#{i}' for i in e['ids'][:8])}{'…' if len(e['ids']) > 8 else ''})",
                "evidence": {"trade_ids": e["ids"], "period": lookback, "rule_id": rid},
            })
    return out


def refresh(db: JournalDB, lookback: str = LOOKBACK, now_ms_: int | None = None) -> dict:
    """Upsert derived memories. Confirmed rows keep their flag; observations the
    journal no longer supports are removed unless the trader confirmed them."""
    now = now_ms_ if now_ms_ is not None else now_ms()
    derived = derive(db, lookback, now)
    keys = {m["key"] for m in derived}
    added = updated = removed = 0
    for m in derived:
        row = db.conn.execute("SELECT id FROM memories WHERE key=?", (m["key"],)).fetchone()
        ev = json.dumps(m["evidence"], ensure_ascii=False)
        if row:
            db.conn.execute("UPDATE memories SET content=?, evidence=?, last_seen_ts=? WHERE id=?",
                            (m["content"], ev, now, row["id"]))
            updated += 1
        else:
            db.conn.execute(
                "INSERT INTO memories(type, key, content, evidence, confirmed, first_seen_ts, last_seen_ts) "
                "VALUES (?,?,?,?,0,?,?)", (m["type"], m["key"], m["content"], ev, now, now))
            added += 1
    for row in db.conn.execute("SELECT id, key FROM memories WHERE confirmed=0 AND key IS NOT NULL").fetchall():
        if row["key"] not in keys:
            db.conn.execute("DELETE FROM memories WHERE id=?", (row["id"],))
            removed += 1
    db.conn.commit()
    return {"added": added, "updated": updated, "removed": removed, "total": len(list_memories(db))}


def list_memories(db: JournalDB, confirmed_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM memories" + (" WHERE confirmed=1" if confirmed_only else "") + \
          " ORDER BY confirmed DESC, type, key"
    return [{"id": r["id"], "type": r["type"], "key": r["key"], "content": r["content"],
             "evidence": json.loads(r["evidence"]), "confirmed": bool(r["confirmed"]),
             "first_seen_ts": r["first_seen_ts"], "last_seen_ts": r["last_seen_ts"]}
            for r in db.conn.execute(sql)]


def confirm(db: JournalDB, memory_id: int, confirmed: bool = True) -> dict:
    cur = db.conn.execute("UPDATE memories SET confirmed=? WHERE id=?", (1 if confirmed else 0, memory_id))
    db.conn.commit()
    if cur.rowcount == 0:
        raise KeyError(f"no memory #{memory_id}")
    return next(m for m in list_memories(db) if m["id"] == memory_id)


def forget(db: JournalDB, memory_id: int) -> None:
    cur = db.conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
    db.conn.commit()
    if cur.rowcount == 0:
        raise KeyError(f"no memory #{memory_id}")


def add_note(db: JournalDB, content: str, type_: str = "preference") -> dict:
    """A memory the trader states themselves (terminology, preference). Always confirmed."""
    now = now_ms()
    cur = db.conn.execute(
        "INSERT INTO memories(type, key, content, evidence, confirmed, first_seen_ts, last_seen_ts) "
        "VALUES (?,?,?,?,1,?,?)",
        (type_, None, content, json.dumps({"source": "trader"}), now, now))
    db.conn.commit()
    return next(m for m in list_memories(db) if m["id"] == cur.lastrowid)


def format_memories(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "no memories yet — they appear once a pattern has 5+ trades or a rule is broken 3+ times"
    return "\n".join(f"{'✓' if m['confirmed'] else '·'} #{m['id']} [{m['type']}] {m['content']}" for m in rows)
