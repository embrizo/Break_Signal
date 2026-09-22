"""Rule engine: structured trading rules checked against a trade.

A rule's ``condition`` is JSON ``{"field": ..., "op": ..., "value": ...}``
evaluated against a flat dict built by :func:`trade_facts` (trade columns +
derived fields). A rule PASSES when the condition holds; a violation is
reported when it does not. Rules whose field is missing (``None``) are
skipped — silence is not a violation.

Operators: ``<= < >= > == !=``, ``in / not_in``, ``has_tag / not_has_tag``,
``is_true / is_false``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from . import analytics
from .models import Trade

_CMP = {
    "<=": lambda a, b: a <= b,
    "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    ">": lambda a, b: a > b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}

# Seed rules — editable/deletable by the user; inserted only into an empty table.
SEED_RULES: list[tuple[str, dict, str]] = [
    ("Max risk 1%", {"field": "risk_pct", "op": "<=", "value": 1.0}, "high"),
    ("Planned R:R >= 1.5", {"field": "planned_rr", "op": ">=", "value": 1.5}, "high"),
    ("Never widen the stop", {"field": "sl_widened", "op": "is_false"}, "high"),
    ("No FOMO entries", {"field": "tags", "op": "not_has_tag", "value": "FOMO"}, "medium"),
    ("No 1D entry when RSI > 75", {"field": "rsi_1d_overbought", "op": "is_false"}, "medium"),
    ("No 1D entry when RSI < 25", {"field": "rsi_1d_oversold", "op": "is_false"}, "medium"),
]


# Keys an ``sl_moved`` event may carry for the old and the new stop. ``from``/``to``
# is the documented form (``/event 12 sl_moved from=225 to=222``).
_SL_FROM_KEYS = ("from", "from_price", "old", "prev", "before")
_SL_TO_KEYS = ("to", "to_price", "new", "price", "after")


def _num(data: dict, keys: tuple[str, ...]) -> float | None:
    for k in keys:
        v = data.get(k)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return float(v)
            except ValueError:
                pass
    return None


def sl_widened(direction: str | None, events: list) -> bool | None:
    """Did an ``sl_moved`` event push the stop AWAY from entry (more risk)?

    Moving the stop *toward* entry — trailing to break-even, locking in — is the
    opposite of widening and must not be flagged. For a LONG the stop widens when
    it moves down, for a SHORT when it moves up.

    ``False`` when nothing moved or every move tightened; ``None`` when it cannot
    be judged (unknown direction, or an ``sl_moved`` event without usable numbers)
    so the rule is reported as not-applicable rather than as a violation.
    """
    d = (direction or "").upper()
    if d not in ("LONG", "SHORT"):
        return None
    moves = []
    for e in events or []:
        ev = e if isinstance(e, dict) else e.to_dict()
        if ev.get("type") == "sl_moved":
            moves.append(ev)
    if not moves:
        return False
    prev: float | None = None
    unjudged = False
    for ev in sorted(moves, key=lambda x: x.get("event_ts") or 0):
        data = ev.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        old = _num(data, _SL_FROM_KEYS)
        new = _num(data, _SL_TO_KEYS)
        if old is None:
            old = prev                      # chain: "to" of the previous move
        if old is None or new is None:
            unjudged = True
            prev = new if new is not None else prev
            continue
        if (d == "LONG" and new < old) or (d == "SHORT" and new > old):
            return True
        prev = new
    return None if unjudged else False


def trade_facts(t: Trade | dict) -> dict[str, Any]:
    """Flat dict the conditions are evaluated against. Accepts a hydrated
    ``Trade`` or a plain dict describing a *proposed* trade."""
    d = t.to_dict() if isinstance(t, Trade) else dict(t)
    direction = (d.get("direction") or "").upper()
    events = d.get("events") or []
    event_types = {e["type"] if isinstance(e, dict) else e.type for e in events}
    tags = list(d.get("tags") or []) + list(d.get("entry_tags") or []) + list(d.get("exit_tags") or [])
    opened = d.get("opened_ts")
    facts = dict(d)
    facts.update(
        direction=direction,
        tags=sorted({x.lower() for x in tags}),
        planned_rr=analytics.planned_rr(direction, d.get("entry_price"), d.get("sl_price"), d.get("tp_price")),
        has_event_sl_moved="sl_moved" in event_types,   # kept for user rules; direction-blind
        has_event_tp_moved="tp_moved" in event_types,
        sl_widened=sl_widened(direction, events),
        entry_hour_utc=datetime.fromtimestamp(opened / 1000, tz=timezone.utc).hour if opened else None,
        has_sl=d.get("sl_price") is not None,
        has_tp=d.get("tp_price") is not None,
    )
    rsi = d.get("ctx_rsi")
    is_1d = (d.get("tf") or "").upper() == "1D"
    facts["rsi_1d_overbought"] = None if rsi is None or not is_1d else rsi > 75
    facts["rsi_1d_oversold"] = None if rsi is None or not is_1d else rsi < 25
    return facts


def evaluate(condition: dict, facts: dict[str, Any]) -> tuple[bool | None, str]:
    """Return ``(passed, detail)``. ``passed`` is ``None`` when the field is
    unknown for this trade (rule not applicable)."""
    field, op, value = condition.get("field"), condition.get("op"), condition.get("value")
    actual = facts.get(field)
    if op in _CMP:
        if actual is None:
            return None, f"{field} unknown"
        ok = _CMP[op](actual, value)
        return ok, f"{field}={_fmt(actual)} (rule: {op} {value})"
    if op == "in":
        if actual is None:
            return None, f"{field} unknown"
        return actual in value, f"{field}={_fmt(actual)} (rule: in {value})"
    if op == "not_in":
        if actual is None:
            return None, f"{field} unknown"
        return actual not in value, f"{field}={_fmt(actual)} (rule: not in {value})"
    if op in ("has_tag", "not_has_tag"):
        tags = facts.get("tags") or []
        present = str(value).lower() in tags
        ok = present if op == "has_tag" else not present
        return ok, f"tag '{value}' {'present' if present else 'absent'}"
    if op == "is_true":
        if actual is None:
            return None, f"{field} unknown"
        return bool(actual), f"{field}={actual}"
    if op == "is_false":
        if actual is None:
            return None, f"{field} unknown"
        return not actual, f"{field}={actual}"
    raise ValueError(f"unknown rule op {op!r}")


def _fmt(x: Any) -> str:
    return f"{x:.3g}" if isinstance(x, float) else str(x)


# ── DB-facing helpers (rules table lives in journal.db) ─────────────────────
def ensure_seed(db) -> None:
    n = db.conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0]
    if n == 0:
        db.conn.executemany(
            "INSERT INTO rules(name, condition, severity, enabled) VALUES (?,?,?,1)",
            [(name, json.dumps(cond), sev) for name, cond, sev in SEED_RULES],
        )
        db.conn.commit()


def list_rules(db, enabled_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM rules" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY id"
    return [{"id": r["id"], "name": r["name"], "condition": json.loads(r["condition"]),
             "severity": r["severity"], "enabled": bool(r["enabled"])}
            for r in db.conn.execute(sql)]


def add_rule(db, name: str, condition: dict, severity: str = "high") -> dict:
    evaluate(condition, {})  # validates the operator early
    cur = db.conn.execute(
        "INSERT INTO rules(name, condition, severity, enabled) VALUES (?,?,?,1)",
        (name, json.dumps(condition), severity))
    db.conn.commit()
    return {"id": cur.lastrowid, "name": name, "condition": condition, "severity": severity, "enabled": True}


def set_rule_enabled(db, rule_id: int, enabled: bool) -> None:
    db.conn.execute("UPDATE rules SET enabled=? WHERE id=?", (1 if enabled else 0, rule_id))
    db.conn.commit()


def delete_rule(db, rule_id: int) -> None:
    db.conn.execute("DELETE FROM rule_violations WHERE rule_id=?", (rule_id,))
    db.conn.execute("DELETE FROM rules WHERE id=?", (rule_id,))
    db.conn.commit()


def check(db, trade: Trade | dict) -> dict:
    """Evaluate every enabled rule. Returns
    ``{violations: [...], passed: [...], not_applicable: [...]}``."""
    facts = trade_facts(trade)
    out: dict[str, list] = {"violations": [], "passed": [], "not_applicable": []}
    for rule in list_rules(db, enabled_only=True):
        ok, detail = evaluate(rule["condition"], facts)
        entry = {"rule_id": rule["id"], "name": rule["name"], "severity": rule["severity"], "detail": detail}
        if ok is None:
            out["not_applicable"].append(entry)
        elif ok:
            out["passed"].append(entry)
        else:
            out["violations"].append(entry)
    return out


def record_violations(db, trade_id: int, result: dict) -> None:
    """Persist ``check()`` output for a stored trade (replaces prior rows)."""
    db.conn.execute("DELETE FROM rule_violations WHERE trade_id=?", (trade_id,))
    db.conn.executemany(
        "INSERT INTO rule_violations(trade_id, rule_id, detail) VALUES (?,?,?)",
        [(trade_id, v["rule_id"], v["detail"]) for v in result["violations"]],
    )
    db.conn.commit()


def stored_violations(db, trade_id: int) -> list[dict]:
    return [{"rule_id": r["rule_id"], "name": r["name"], "severity": r["severity"], "detail": r["detail"]}
            for r in db.conn.execute(
                "SELECT v.rule_id, v.detail, r.name, r.severity FROM rule_violations v "
                "JOIN rules r ON r.id = v.rule_id WHERE v.trade_id=? ORDER BY v.rule_id", (trade_id,))]
