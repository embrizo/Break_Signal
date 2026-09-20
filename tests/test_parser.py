import pytest

from break_signal.journal.parser import (
    ParseError,
    normalize_tf,
    parse_close,
    parse_trade,
    resolve_symbol,
)

ALIASES = {"SOL": "SOL-USDT-SWAP", "BTC": "BTC-USDT-SWAP"}


# ── open ─────────────────────────────────────────────────────────────────────
def test_full_line():
    p = parse_trade("SOL 4H long 231.5 sl 225 tp 245 size 2 risk 50 lev 5 fee 1.2 conf 4 "
                    "#breakout #retest -- clean retest, felt calm", ALIASES)
    assert p.symbol == "SOL-USDT-SWAP"
    assert p.tf == "4H"
    assert p.direction == "LONG"
    assert p.entry_price == 231.5
    assert p.sl_price == 225 and p.tp_price == 245
    assert p.position_size == 2 and p.risk_amount == 50 and p.risk_pct is None
    assert p.leverage == 5 and p.fees == 1.2 and p.confidence == 4
    assert p.tags == ["breakout", "retest"]
    assert p.reason == "clean retest, felt calm"


def test_minimal_line_leaves_everything_none():
    p = parse_trade("btc short", ALIASES)
    assert p.symbol == "BTC-USDT-SWAP"
    assert p.direction == "SHORT"
    assert p.tf is None and p.entry_price is None and p.sl_price is None and p.tp_price is None
    assert p.tags == [] and p.reason is None


def test_missing_sl_stays_none_not_guessed():
    p = parse_trade("SOL long 100 tp 120", ALIASES)
    assert p.sl_price is None and p.tp_price == 120


def test_unknown_symbol_is_uppercased_not_aliased():
    assert parse_trade("doge-usdt-swap long 0.1").symbol == "DOGE-USDT-SWAP"


def test_resolve_symbol_case_insensitive():
    assert resolve_symbol("sol", ALIASES) == "SOL-USDT-SWAP"
    assert resolve_symbol("Sol", ALIASES) == "SOL-USDT-SWAP"


@pytest.mark.parametrize("tok,expected", [
    ("4h", "4H"), ("4H", "4H"), ("1d", "1D"), ("15m", "15m"), ("1W", "1W"), ("abc", None),
])
def test_normalize_tf(tok, expected):
    assert normalize_tf(tok) == expected


def test_tf_optional_and_direction_aliases():
    assert parse_trade("SOL buy 10").direction == "LONG"
    assert parse_trade("SOL 1d sell 10").direction == "SHORT"
    assert parse_trade("SOL 1d sell 10").tf == "1D"


def test_thousands_separator():
    p = parse_trade("BTC long 104,200 sl 103,500 tp 106,000", ALIASES)
    assert (p.entry_price, p.sl_price, p.tp_price) == (104200.0, 103500.0, 106000.0)


def test_equals_form():
    p = parse_trade("SOL long 100 sl=95 tp=110 conf=3")
    assert p.sl_price == 95 and p.tp_price == 110 and p.confidence == 3


def test_entry_keyword():
    assert parse_trade("SOL long entry 100 sl 95").entry_price == 100
    assert parse_trade("SOL long at 100").entry_price == 100


def test_risk_percent_vs_amount():
    assert parse_trade("SOL long 100 risk 1%").risk_pct == 1.0
    assert parse_trade("SOL long 100 risk 1%").risk_amount is None
    assert parse_trade("SOL long 100 risk 25").risk_amount == 25.0


def test_thai_tags_and_underscore_to_space():
    p = parse_trade("SOL long 100 #ตามวินัย #liquidity_sweep")
    assert p.tags == ["ตามวินัย", "liquidity sweep"]


def test_tags_after_reason_separator_are_still_tags():
    p = parse_trade("SOL long 100 -- took it late #chased")
    assert p.tags == ["chased"]
    assert p.reason == "took it late"


def test_tags_anywhere():
    p = parse_trade("#fomo SOL long 100 #breakout sl 90")
    assert p.tags == ["fomo", "breakout"] and p.sl_price == 90


def test_reason_whitespace_collapsed():
    assert parse_trade("SOL long 100 --   a   b  ").reason == "a b"


def test_no_direction_raises():
    with pytest.raises(ParseError):
        parse_trade("SOL 4H 100")


def test_bad_tf_raises():
    with pytest.raises(ParseError):
        parse_trade("SOL foo long 100")


def test_unknown_keyword_raises():
    with pytest.raises(ParseError):
        parse_trade("SOL long 100 banana 5")


def test_bad_number_raises():
    with pytest.raises(ParseError):
        parse_trade("SOL long 100 sl abc")


def test_conf_out_of_range_raises():
    with pytest.raises(ParseError):
        parse_trade("SOL long 100 conf 9")


def test_empty_raises():
    with pytest.raises(ParseError):
        parse_trade("   ")


# ── close ────────────────────────────────────────────────────────────────────
def test_close_full():
    c = parse_close("12 244 win hit TP, clean retest, held the plan #discipline")
    assert c.trade_id == 12 and c.exit_price == 244 and c.outcome == "WIN"
    assert c.tags == ["discipline"]
    assert c.reason == "hit TP, clean retest, held the plan"


def test_close_without_outcome():
    c = parse_close("#13 224 moved SL wider #แหกกฎเลื่อนSL #FOMO")
    assert c.trade_id == 13 and c.outcome is None
    assert c.tags == ["แหกกฎเลื่อนSL", "FOMO"]
    assert c.reason == "moved SL wider"


def test_close_outcome_aliases():
    assert parse_close("1 10 loss").outcome == "LOSS"
    assert parse_close("1 10 be").outcome == "BE"
    assert parse_close("1 10 w").outcome == "WIN"


def test_close_minimal():
    c = parse_close("5 99.5")
    assert c.trade_id == 5 and c.exit_price == 99.5 and c.reason is None and c.tags == []


def test_close_bad_id_or_price():
    with pytest.raises(ParseError):
        parse_close("abc 100")
    with pytest.raises(ParseError):
        parse_close("1 xyz")
    with pytest.raises(ParseError):
        parse_close("1")
