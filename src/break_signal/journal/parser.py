"""One-line trade syntax → structured fields. Deterministic, no guessing.

Open::

    <SYM> [<tf>] long|short|buy|sell <entry> [sl <x>] [tp <x>] [size <x>]
          [risk <x>[%]] [lev <x>] [fee <x>] [conf <1-5>] [#tag ...] [-- free reason]

    SOL 4H long 231.5 sl 225 tp 245 #breakout #retest -- clean retest, felt calm

Close::

    <trade_id> <exit> [win|loss|be] [#tag ...] [free reason]

    12 244 win hit TP, clean retest, held the plan #discipline

Anything not stated comes back as ``None``. Keywords accept ``sl 225`` or
``sl=225``; numbers may contain thousands separators (``104,200``). Tags are
``#word`` (Unicode OK, so Thai works) and may appear anywhere.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_NUM = r"[-+]?\d[\d,]*(?:\.\d+)?"
_TF_RE = re.compile(r"^(\d+)([mhdw])$", re.IGNORECASE)
_TAG_RE = re.compile(r"#([^\s#]+)")
_DIRECTION = {"long": "LONG", "buy": "LONG", "l": "LONG",
              "short": "SHORT", "sell": "SHORT", "s": "SHORT"}
_OUTCOME = {"win": "WIN", "w": "WIN", "loss": "LOSS", "lose": "LOSS", "l": "LOSS",
            "be": "BE", "breakeven": "BE"}
# keyword → (field, converter)
_KEYWORDS: dict[str, tuple[str, str]] = {
    "sl": ("sl_price", "num"), "stop": ("sl_price", "num"),
    "tp": ("tp_price", "num"), "target": ("tp_price", "num"),
    "size": ("position_size", "num"), "qty": ("position_size", "num"),
    "risk": ("risk", "risk"),
    "lev": ("leverage", "num"), "leverage": ("leverage", "num"),
    "fee": ("fees", "num"), "fees": ("fees", "num"),
    "conf": ("confidence", "int"), "confidence": ("confidence", "int"),
}


class ParseError(ValueError):
    pass


@dataclass
class ParsedTrade:
    symbol: str
    direction: str
    entry_price: float | None = None
    tf: str | None = None
    sl_price: float | None = None
    tp_price: float | None = None
    position_size: float | None = None
    risk_amount: float | None = None
    risk_pct: float | None = None
    leverage: float | None = None
    fees: float | None = None
    confidence: int | None = None
    tags: list[str] = field(default_factory=list)
    reason: str | None = None

    def db_fields(self) -> dict:
        """Kwargs for ``JournalDB.add_trade`` (symbol/direction/tags excluded)."""
        return {
            "entry_price": self.entry_price, "tf": self.tf, "sl_price": self.sl_price,
            "tp_price": self.tp_price, "position_size": self.position_size,
            "risk_amount": self.risk_amount, "risk_pct": self.risk_pct,
            "leverage": self.leverage, "fees": self.fees, "confidence": self.confidence,
            "entry_reason": self.reason,
        }


@dataclass
class ParsedClose:
    trade_id: int
    exit_price: float
    outcome: str | None = None
    tags: list[str] = field(default_factory=list)
    reason: str | None = None


def normalize_tf(tok: str) -> str | None:
    """``4h`` → ``4H``, ``1d`` → ``1D``, ``15m`` → ``15m`` (OKX bar style)."""
    m = _TF_RE.match(tok)
    if not m:
        return None
    n, unit = m.group(1), m.group(2)
    return f"{n}{unit.lower() if unit.lower() == 'm' else unit.upper()}"


def _num(tok: str) -> float | None:
    if re.fullmatch(_NUM, tok):
        return float(tok.replace(",", ""))
    return None


def resolve_symbol(tok: str, aliases: dict[str, str] | None = None) -> str:
    """``sol`` → ``SOL-USDT-SWAP`` via aliases; otherwise upper-cased as typed."""
    up = tok.upper()
    for k, v in (aliases or {}).items():
        if k.upper() == up:
            return v
    return up


def _split_tags(text: str) -> tuple[list[str], str]:
    tags = [t.replace("_", " ") for t in _TAG_RE.findall(text)]
    return tags, _TAG_RE.sub("", text)


def _split_reason(text: str) -> tuple[str, str | None]:
    if "--" in text:
        head, _, tail = text.partition("--")
        return head, " ".join(tail.split()) or None
    return text, None


def parse_trade(text: str, aliases: dict[str, str] | None = None) -> ParsedTrade:
    text = text.strip()
    if not text:
        raise ParseError("empty trade line")
    tags, text = _split_tags(text)          # tags may sit anywhere, incl. after '--'
    head, reason = _split_reason(text)
    toks = head.split()
    if len(toks) < 2:
        raise ParseError("need at least '<SYMBOL> long|short'")

    symbol = resolve_symbol(toks[0], aliases)
    i = 1
    tf = None
    if i < len(toks) and toks[i].lower() not in _DIRECTION:
        tf = normalize_tf(toks[i])
        if tf is None:
            raise ParseError(f"expected timeframe or long/short, got {toks[i]!r}")
        i += 1
    if i >= len(toks) or toks[i].lower() not in _DIRECTION:
        raise ParseError("missing direction (long|short)")
    direction = _DIRECTION[toks[i].lower()]
    i += 1

    p = ParsedTrade(symbol=symbol, direction=direction, tf=tf, tags=tags, reason=reason)
    # optional bare entry price right after the direction
    if i < len(toks) and _num(toks[i]) is not None:
        p.entry_price = _num(toks[i])
        i += 1

    while i < len(toks):
        tok = toks[i]
        key, _, inline_val = tok.partition("=")
        key = key.lower()
        if key == "entry" or key == "at":
            val = inline_val or (toks[i + 1] if i + 1 < len(toks) else "")
            p.entry_price = _num(val)
            i += 1 if inline_val else 2
            continue
        if key not in _KEYWORDS:
            raise ParseError(f"unexpected token {tok!r}")
        val = inline_val or (toks[i + 1] if i + 1 < len(toks) else "")
        i += 1 if inline_val else 2
        fld, kind = _KEYWORDS[key]
        if kind == "risk":
            if val.endswith("%"):
                p.risk_pct = _num(val[:-1])
                if p.risk_pct is None:
                    raise ParseError(f"bad value for risk: {val!r}")
            else:
                p.risk_amount = _num(val)
                if p.risk_amount is None:
                    raise ParseError(f"bad value for risk: {val!r}")
            continue
        num = _num(val)
        if num is None:
            raise ParseError(f"bad value for {key}: {val!r}")
        if kind == "int":
            setattr(p, fld, int(num))
        else:
            setattr(p, fld, num)
    if p.confidence is not None and not 1 <= p.confidence <= 5:
        raise ParseError("conf must be 1..5")
    return p


def parse_close(text: str) -> ParsedClose:
    text = re.sub(r"^#(?=\d)", "", text.strip())   # allow "#12" as the trade id
    tags, rest = _split_tags(text)
    toks = rest.split()
    if len(toks) < 2:
        raise ParseError("need '<trade_id> <exit_price> [win|loss|be] [reason]'")
    if not toks[0].isdigit():
        raise ParseError(f"bad trade id {toks[0]!r}")
    trade_id = int(toks[0])
    exit_price = _num(toks[1])
    if exit_price is None:
        raise ParseError(f"bad exit price {toks[1]!r}")
    i = 2
    outcome = None
    if i < len(toks) and toks[i].lower() in _OUTCOME:
        outcome = _OUTCOME[toks[i].lower()]
        i += 1
    reason = " ".join(toks[i:]).strip() or None
    return ParsedClose(trade_id=trade_id, exit_price=exit_price, outcome=outcome,
                       tags=tags, reason=reason)
