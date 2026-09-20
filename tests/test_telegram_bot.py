"""Telegram command bot — pure command handling, no network."""
import asyncio

import pytest

from break_signal.config import Config, Watch
from break_signal.journal.db import JournalDB
from break_signal.journal.tools import Tools
from break_signal.notify.telegram_bot import HELP, TelegramBot


@pytest.fixture
def bot(tmp_path):
    cfg = Config(watches=[Watch(symbol="SOL-USDT-SWAP", timeframe="4H")],
                 telegram_bot={"enabled": True, "allowed_chat_ids": ["111"]},
                 journal={"screenshots_dir": str(tmp_path / "shots")})
    tools = Tools(JournalDB(":memory:"), cfg)
    b = TelegramBot("TOKEN", cfg, tools, coach=None)
    yield b
    tools.db.close()


def cmd(bot, text, photo=None):
    return asyncio.run(bot.handle_command("111", text, photo))


def test_help_and_unknown(bot):
    assert cmd(bot, "/start") == HELP
    assert cmd(bot, "/help@mybot") == HELP
    assert cmd(bot, "hello there") == HELP
    assert cmd(bot, "/frobnicate").startswith("unknown command")
    assert cmd(bot, "") == ""


def test_trade_close_flow_with_rules(bot):
    r = cmd(bot, "/trade SOL 4H long 231.5 sl 225 tp 245 #breakout #retest -- clean retest")
    assert r.startswith("logged #1 OPEN SOL-USDT-SWAP 4H LONG @231.5000")
    assert "planned R:R 2.08" in r and "⚠" not in r
    r = cmd(bot, "/trade SOL 1D long 100 sl 90 tp 101 risk 3% #fomo")
    assert "⚠ Max risk 1%" in r and "⚠ No FOMO entries" in r
    r = cmd(bot, "/close 1 244 hit TP, held the plan #hit_tp")
    assert r.startswith("closed #1: WIN R=1.92")
    assert "error: no trade #99" == cmd(bot, "/close 99 100")
    assert cmd(bot, "/trade").startswith("usage:")


def test_skip_event_tag(bot):
    sid = bot.tools.db.insert_signal(dict(symbol="SOL-USDT-SWAP", exchange="OKX", tf="4H", event="break_down",
                                          side="support", price=1, line=1, atr_dist=0.4, touches=3, age_bars=1,
                                          vol_ratio=1.5, rsi=40.0, time="2026-01-01T00:00:00Z", line_id="x"), "live")
    assert cmd(bot, f"/skip {sid} not at desk") == f"recorded skip #1 on signal #{sid} (SOL-USDT-SWAP 4H SHORT)"
    cmd(bot, "/trade SOL 4H long 100 sl 95 tp 110")
    r = cmd(bot, "/event 2 sl_moved from=95 to=90")
    assert "sl_moved {'from': 95.0, 'to': 90.0}" in r and "⚠ Never widen the stop" in r
    # #liquidity_sweep → "liquidity sweep" → matches the seeded "Liquidity Sweep" (NOCASE)
    assert cmd(bot, "/tag 2 entry #retest #liquidity_sweep") == "#2 entry tags ['Liquidity Sweep', 'Retest'] exit tags []"
    assert cmd(bot, "/tag 2 middle #x").startswith("usage:")


def test_list_show_stats_signals_tags_rules(bot):
    cmd(bot, "/trade SOL 4H long 100 sl 90 tp 120 #breakout")
    cmd(bot, "/close 1 120")
    assert cmd(bot, "/list").startswith("#1 CLOSED SOL-USDT-SWAP 4H LONG")
    assert cmd(bot, "/list 30d").startswith("#1")
    assert cmd(bot, "/list bogus").startswith("error: Bad period")
    show = cmd(bot, "/show 1")
    assert "planned R:R 2.00" in show and "→ WIN R=2.00" in show
    assert cmd(bot, "/show 7") == "no trade #7"
    stats = cmd(bot, "/stats")
    assert "n=1 W/L/BE 1/0/0 win 100%" in stats and "Breakout: n=1" in stats
    assert cmd(bot, "/signals") == "no signals"
    assert "PSYCH: " in cmd(bot, "/tags")
    assert "✓ #1 Max risk 1% (high)" in cmd(bot, "/rules")


def test_photo_screenshot(bot, tmp_path):
    cmd(bot, "/trade SOL 4H long 100 sl 90")
    r = cmd(bot, "/shot 1 pre", photo=b"\xff\xd8jpegbytes")
    assert r.startswith("saved PRE screenshot for #1")
    t = bot.tools.get_trade(1)
    assert len(t["screenshots"]) == 1 and t["screenshots"][0]["phase"] == "PRE"
    assert (tmp_path / "shots").exists() and list((tmp_path / "shots").iterdir())
    assert cmd(bot, "nonsense caption", photo=b"x").startswith("caption must be")


def test_ask_review_without_coach(bot):
    assert cmd(bot, "/ask should I take it?").startswith("AI coach is off")
    assert cmd(bot, "/review 1").startswith("AI coach is off")


def test_allowlist_blocks_other_chats(bot, monkeypatch):
    sent = []

    async def fake_send(session, chat_id, text):
        sent.append((chat_id, text))
    monkeypatch.setattr(bot, "send", fake_send)
    upd = {"update_id": 1, "message": {"chat": {"id": 999}, "text": "/trade SOL long 100"}}
    asyncio.run(bot._handle_update(None, upd))
    assert sent == [] and bot.tools.search_trades()["n"] == 0
    upd = {"update_id": 2, "message": {"chat": {"id": 111}, "text": "/trade SOL long 100"}}
    asyncio.run(bot._handle_update(None, upd))
    assert sent and sent[0][0] == "111" and sent[0][1].startswith("logged #1")
