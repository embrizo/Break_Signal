import pytest

from break_signal.journal import rules
from break_signal.journal.db import JournalDB


@pytest.fixture
def db():
    d = JournalDB(":memory:")
    rules.ensure_seed(d)
    yield d
    d.close()


def test_seed_only_once(db):
    rules.ensure_seed(db)
    assert len(rules.list_rules(db)) == len(rules.SEED_RULES)


@pytest.mark.parametrize("cond,facts,expected", [
    ({"field": "risk_pct", "op": "<=", "value": 1}, {"risk_pct": 0.5}, True),
    ({"field": "risk_pct", "op": "<=", "value": 1}, {"risk_pct": 2.0}, False),
    ({"field": "risk_pct", "op": "<=", "value": 1}, {"risk_pct": None}, None),
    ({"field": "planned_rr", "op": ">=", "value": 1.5}, {"planned_rr": 1.5}, True),
    ({"field": "planned_rr", "op": ">", "value": 1.5}, {"planned_rr": 1.5}, False),
    ({"field": "tf", "op": "==", "value": "4H"}, {"tf": "4H"}, True),
    ({"field": "tf", "op": "!=", "value": "4H"}, {"tf": "4H"}, False),
    ({"field": "tf", "op": "in", "value": ["4H", "1D"]}, {"tf": "1D"}, True),
    ({"field": "tf", "op": "not_in", "value": ["4H", "1D"]}, {"tf": "1H"}, True),
    ({"field": "tags", "op": "has_tag", "value": "Retest"}, {"tags": ["retest"]}, True),
    ({"field": "tags", "op": "not_has_tag", "value": "FOMO"}, {"tags": ["fomo"]}, False),
    ({"field": "tags", "op": "not_has_tag", "value": "FOMO"}, {"tags": []}, True),
    ({"field": "x", "op": "is_true"}, {"x": True}, True),
    ({"field": "x", "op": "is_false"}, {"x": True}, False),
    ({"field": "x", "op": "is_false"}, {}, None),
])
def test_operators(cond, facts, expected):
    ok, _ = rules.evaluate(cond, facts)
    assert ok is expected


def test_unknown_op_raises():
    with pytest.raises(ValueError):
        rules.evaluate({"field": "x", "op": "~", "value": 1}, {"x": 1})


def test_trade_facts_derived_fields(db):
    t = db.add_trade("SOL-USDT-SWAP", "LONG", tf="1D", entry_price=100, sl_price=90, tp_price=125,
                     risk_pct=2, entry_tags=["FOMO"], ctx_rsi=80, opened_ts=15 * 3_600_000)
    db.add_event(t.id, "sl_moved", {"from": 90, "to": 85})
    f = rules.trade_facts(db.get_trade(t.id))
    assert f["planned_rr"] == pytest.approx(2.5)
    assert f["has_event_sl_moved"] is True
    assert f["tags"] == ["fomo"]
    assert f["entry_hour_utc"] == 15
    assert f["rsi_1d_overbought"] is True and f["rsi_1d_oversold"] is False
    assert f["has_sl"] and f["has_tp"]


@pytest.mark.parametrize("direction,moves,expected", [
    # a stop moved AWAY from entry is widening: down for a LONG, up for a SHORT
    ("LONG", [{"from": 90, "to": 85}], True),
    ("SHORT", [{"from": 110, "to": 115}], True),
    # …toward entry is trailing/locking in, which is the opposite — never a violation
    ("LONG", [{"from": 90, "to": 95}], False),
    ("LONG", [{"from": 90, "to": 100}], False),          # to break-even
    ("SHORT", [{"from": 110, "to": 105}], False),
    # one widening move anywhere in the sequence is enough
    ("LONG", [{"from": 90, "to": 95}, {"from": 95, "to": 88}], True),
    ("LONG", [{"from": 90, "to": 95}, {"from": 95, "to": 97}], False),
    # a later move with only "to" chains off the previous move's "to"
    ("LONG", [{"from": 90, "to": 95}, {"to": 92}], True),
    ("LONG", [{"from": 90, "to": 95}, {"to": 96}], False),
    # unjudgeable: no numbers at all, or a first move with no starting point
    ("LONG", [{}], None),
    ("LONG", [{"to": 95}], None),
    ("LONG", [{"from": 90, "to": 85}, {}], True),        # already widened → still True
    ("LONG", [{"from": 90, "to": 95}, {}], None),        # can't rule it out
    ("LONG", [{"from": "90", "to": "85"}], True),        # strings (came in as k=v text)
])
def test_sl_widened(direction, moves, expected):
    events = [{"type": "sl_moved", "data": m, "event_ts": i} for i, m in enumerate(moves)]
    assert rules.sl_widened(direction, events) is expected


def test_sl_widened_edge_cases():
    assert rules.sl_widened("LONG", []) is False                       # never moved
    assert rules.sl_widened("LONG", [{"type": "note", "data": {"x": 1}}]) is False
    assert rules.sl_widened(None, [{"type": "sl_moved", "data": {"from": 90, "to": 85}}]) is None
    assert rules.sl_widened("long", [{"type": "sl_moved", "data": {"from": 90, "to": 85}}]) is True


def test_trailing_the_stop_is_not_a_violation(db):
    """The bug this rule had: any sl_moved event counted as widening."""
    t = db.add_trade("SOL-USDT-SWAP", "LONG", tf="1D", entry_price=100, sl_price=90, tp_price=125)
    db.add_event(t.id, "sl_moved", {"from": 90, "to": 100})            # trail to break-even
    f = rules.trade_facts(db.get_trade(t.id))
    assert f["sl_widened"] is False and f["has_event_sl_moved"] is True
    res = rules.check(db, db.get_trade(t.id))
    assert "Never widen the stop" not in {v["name"] for v in res["violations"]}
    assert "Never widen the stop" in {p["name"] for p in res["passed"]}

    s = db.add_trade("SOL-USDT-SWAP", "SHORT", tf="1D", entry_price=100, sl_price=110, tp_price=80)
    db.add_event(s.id, "sl_moved", {"from": 110, "to": 104})           # trail down on a short
    assert "Never widen the stop" not in {v["name"] for v in rules.check(db, db.get_trade(s.id))["violations"]}
    db.add_event(s.id, "sl_moved", {"from": 104, "to": 112})           # then widen it
    assert "Never widen the stop" in {v["name"] for v in rules.check(db, db.get_trade(s.id))["violations"]}


def test_unjudgeable_stop_move_is_not_applicable(db):
    t = db.add_trade("SOL-USDT-SWAP", "LONG", tf="1D", entry_price=100, sl_price=90, tp_price=125)
    db.add_event(t.id, "sl_moved", {"note": "tightened a bit"})        # no numbers
    res = rules.check(db, db.get_trade(t.id))
    assert "Never widen the stop" in {x["name"] for x in res["not_applicable"]}
    assert "Never widen the stop" not in {v["name"] for v in res["violations"]}


def test_check_against_seed_rules(db):
    t = db.add_trade("SOL-USDT-SWAP", "LONG", tf="1D", entry_price=100, sl_price=90, tp_price=125,
                     risk_pct=2, entry_tags=["FOMO"], ctx_rsi=80)
    db.add_event(t.id, "sl_moved", {"from": 90, "to": 85})
    res = rules.check(db, db.get_trade(t.id))
    names = {v["name"] for v in res["violations"]}
    assert names == {"Max risk 1%", "Never widen the stop", "No FOMO entries",
                     "No 1D entry when RSI > 75"}
    assert {p["name"] for p in res["passed"]} == {"Planned R:R >= 1.5", "No 1D entry when RSI < 25"}
    assert res["not_applicable"] == []


def test_check_proposed_dict_and_not_applicable(db):
    res = rules.check(db, {"symbol": "SOL-USDT-SWAP", "direction": "LONG", "tf": "4H",
                           "entry_price": 100, "sl_price": 95, "tp_price": 110})
    assert [v["name"] for v in res["violations"]] == []
    na = {x["name"] for x in res["not_applicable"]}
    assert "Max risk 1%" in na                  # no risk_pct given
    assert "No 1D entry when RSI > 75" in na    # not a 1D trade
    assert {p["name"] for p in res["passed"]} >= {"Planned R:R >= 1.5", "Never widen the stop"}


def test_record_and_read_violations(db):
    t = db.add_trade("SOL-USDT-SWAP", "LONG", entry_price=100, sl_price=90, tp_price=101)
    res = rules.check(db, t)
    rules.record_violations(db, t.id, res)
    stored = rules.stored_violations(db, t.id)
    assert [s["name"] for s in stored] == ["Planned R:R >= 1.5"]
    # re-recording replaces, not appends
    rules.record_violations(db, t.id, res)
    assert len(rules.stored_violations(db, t.id)) == 1
    db.delete_trade(t.id)
    assert db.conn.execute("SELECT COUNT(*) FROM rule_violations").fetchone()[0] == 0


def test_add_disable_delete_rule(db):
    r = rules.add_rule(db, "Only 4H/1D", {"field": "tf", "op": "in", "value": ["4H", "1D"]}, "low")
    res = rules.check(db, {"direction": "LONG", "tf": "1H"})
    assert "Only 4H/1D" in {v["name"] for v in res["violations"]}
    rules.set_rule_enabled(db, r["id"], False)
    res = rules.check(db, {"direction": "LONG", "tf": "1H"})
    assert "Only 4H/1D" not in {v["name"] for v in res["violations"]}
    rules.delete_rule(db, r["id"])
    assert all(x["name"] != "Only 4H/1D" for x in rules.list_rules(db))
    with pytest.raises(ValueError):
        rules.add_rule(db, "bad", {"field": "tf", "op": "???"})
