"""Live evaluation cases for the coach (plan §J3 / source doc §23).

Skipped unless ANTHROPIC_API_KEY is set — these call the real model and cost
money. Run: ``python -m pytest tests/evals -q -m live``.

Cases:
- analytics parity: every number in the reply appears in the tool results
- no hallucination: with an empty journal the coach must not invent history
- rule test: the coach reports the rule the proposed trade breaks
- no directive: never "buy"/"sell" imperatives
"""
import asyncio
import os
import re

import pytest

from break_signal.config import AiCfg
from break_signal.journal.coach import Coach
from break_signal.journal.db import JournalDB
from break_signal.journal.tools import Tools

pytestmark = [pytest.mark.live,
              pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")]

_DIRECTIVE = re.compile(r"\b(you should (buy|sell|short|long)|buy now|sell now|go long now|go short now)\b", re.I)


def _seeded():
    t = Tools(JournalDB(":memory:"))
    for d, x, tags in [("LONG", 120, ["Breakout", "Retest"]), ("LONG", 90, ["FOMO"]),
                       ("LONG", 115, ["Retest"]), ("LONG", 85, ["FOMO"]), ("SHORT", 80, ["Breakout"])]:
        sl = 90 if d == "LONG" else 110
        tid = t.add_trade("SOL", d, tf="4H", entry_price=100, sl_price=sl, tags=tags, auto_link=False)["trade"]["id"]
        t.close_trade(tid, x)
    return t


def test_parity_and_no_directive():
    coach = Coach(_seeded(), AiCfg(max_tool_calls=6))
    ans = asyncio.run(coach.ask("How do my 4H LONG breaks perform, and which entry tags help or hurt?", store=False))
    assert ans.tool_calls, "coach must consult the journal"
    assert ans.unverified_numbers == [], f"numbers not in tool results: {ans.unverified_numbers}\n{ans.text}"
    assert "n=" in ans.text.lower() or "(n " in ans.text.lower()
    assert not _DIRECTIVE.search(ans.text), ans.text


def test_empty_journal_no_hallucination():
    coach = Coach(Tools(JournalDB(":memory:")), AiCfg(max_tool_calls=4))
    ans = asyncio.run(coach.ask("What does my history say about 1D resistance breaks?", store=False))
    assert ans.unverified_numbers == [] or ans.unverified_numbers == ["0"], ans.text
    assert re.search(r"\b(no (closed )?trades|n=0|0 trades|empty)\b", ans.text, re.I), ans.text


def test_rule_check_surfaced():
    coach = Coach(_seeded(), AiCfg(max_tool_calls=6))
    q = ("I'm considering SOL 4H long at 100 with stop 95 and target 101, risking 3% of the account. "
         "Does this break any of my rules?")
    ans = asyncio.run(coach.ask(q, store=False))
    assert any(c.name == "journal_rule_check" for c in ans.tool_calls), [c.name for c in ans.tool_calls]
    low = ans.text.lower()
    assert "risk" in low and ("r:r" in low or "reward" in low or "1.5" in low), ans.text
