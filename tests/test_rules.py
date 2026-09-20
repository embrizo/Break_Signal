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
