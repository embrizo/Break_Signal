"""Coach unit tests with a fake Anthropic client — no network, no key."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from break_signal.config import AiCfg
from break_signal.journal import coach as C
from break_signal.journal.db import JournalDB
from break_signal.journal.tools import Tools


# ── fakes ────────────────────────────────────────────────────────────────────
class FakeRunner:
    """Pretends to be the SDK tool runner: calls the named tools, then answers."""

    def __init__(self, tools, script, answer):
        self.tools = {t.name: t for t in tools}
        self.script = script          # [(tool_name, input_dict), ...]
        self.answer = answer

    async def until_done(self):
        for name, inp in self.script:
            await self.tools[name].call(inp)   # exercises the real wrapper → Tools → analytics
        return SimpleNamespace(
            content=[SimpleNamespace(type="thinking", thinking=""),
                     SimpleNamespace(type="text", text=self.answer)],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=100, output_tokens=50, cache_read_input_tokens=0,
                                  cache_creation_input_tokens=90),
        )


class FakeClient:
    def __init__(self, script=(), answer="", review=None):
        self.script, self.answer, self.review = list(script), answer, review
        self.calls = []
        client = self

        class Messages:
            def tool_runner(self, **kw):
                client.calls.append(kw)
                return FakeRunner(kw["tools"], client.script, client.answer)

            async def parse(self, **kw):
                client.calls.append(kw)
                return SimpleNamespace(parsed_output=kw["output_format"](**client.review))

        self.beta = SimpleNamespace(messages=Messages())
        self.messages = Messages()


@pytest.fixture
def tools():
    t = Tools(JournalDB(":memory:"))
    for d, x, tags in [("LONG", 120, ["Breakout"]), ("LONG", 90, ["FOMO"]), ("LONG", 115, ["Retest"])]:
        tid = t.add_trade("SOL", d, tf="4H", entry_price=100, sl_price=90, tags=tags, auto_link=False)["trade"]["id"]
        t.close_trade(tid, x)
    yield t
    t.db.close()


# ── parity guard ─────────────────────────────────────────────────────────────
def test_parity_check_accepts_numbers_from_results():
    tc = C.ToolCall("journal_stats", {"period": "all"},
                    {"n": 7, "win_rate": 0.571, "avg_r": 0.8, "trades": [{"id": 12}]})
    text = "You have 7 trades (n=7), 57% win, avg +0.8R; see #12. Rate 0.57."
    assert C.parity_check(text, [tc]) == []


def test_parity_check_flags_invented_numbers():
    tc = C.ToolCall("journal_stats", {}, {"n": 7, "win_rate": 0.571})
    missing = C.parity_check("7 trades, 57% win, expectancy 1.9R and a 12% edge", [tc])
    assert missing == ["1.9", "12"]


def test_chunk_prefers_paragraph_breaks():
    text = "a" * 3000 + "\n" + "b" * 3000
    parts = C.chunk(text, 4000)
    assert parts == ["a" * 3000, "b" * 3000]
    assert C.chunk("x" * 9000, 4000) == ["x" * 4000, "x" * 4000, "x" * 1000]


# ── tool surface ─────────────────────────────────────────────────────────────
def test_coach_tools_are_read_only_and_have_schemas(tools):
    coach = C.Coach(tools, AiCfg(), client=FakeClient())
    names = [t.name for t in coach.build_tools()]
    assert "market_snapshot" in names and "journal_stats" in names and "journal_rule_check" in names
    for bad in ("add", "close", "skip", "update", "delete", "tag_trade", "set_rule"):
        assert not any(bad in n for n in names), names
    for t in coach.build_tools():
        d = t.to_dict()
        assert d["description"] and d["input_schema"]["type"] == "object"


def test_wrapper_returns_json_and_records_call(tools):
    coach = C.Coach(tools, AiCfg(), client=FakeClient())
    stats = next(t for t in coach.build_tools() if t.name == "journal_stats")
    out = json.loads(asyncio.run(stats.call({"period": "all", "tf": "4H"})))
    assert out["n"] == 3 and out["wins"] == 2
    assert coach._calls[0].name == "journal_stats" and coach._calls[0].args["tf"] == "4H"


# ── /ask ─────────────────────────────────────────────────────────────────────
def test_ask_runs_tools_stores_and_parity_checks(tools):
    fake = FakeClient(
        script=[("journal_stats", {"period": "all"}), ("journal_tag_stats", {"phase": "ENTRY"})],
        answer="YOUR HISTORY: 3 trades (n=3, small sample), 67% win, avg +0.83R. FOMO: 0/1. YOUR DECISION.",
    )
    coach = C.Coach(tools, AiCfg(max_tool_calls=5), client=fake)
    ans = asyncio.run(coach.ask("how am I doing on 4H breaks?"))
    assert [c.name for c in ans.tool_calls] == ["journal_stats", "journal_tag_stats"]
    assert ans.tool_calls[0].result["n"] == 3
    assert ans.unverified_numbers == []
    assert ans.usage["output_tokens"] == 50 and ans.stop_reason == "end_turn"
    # request shape
    kw = fake.calls[0]
    assert kw["model"] == "claude-opus-5" and kw["max_iterations"] == 5
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kw["messages"][-1]["role"] == "user"
    # stored for audit
    row = tools.db.conn.execute("SELECT kind, model, prompt_version, input_metrics, output FROM ai_analysis").fetchone()
    assert row["kind"] == "suggestion" and row["prompt_version"] == "coach_v1"
    stored = json.loads(row["input_metrics"])
    assert stored["tool_calls"][0]["name"] == "journal_stats" and stored["question"].startswith("how am I")
    assert ans.analysis_id is not None


def test_ask_flags_invented_numbers(tools):
    fake = FakeClient(script=[("journal_stats", {})], answer="Your win rate is 80% (n=3) with 9.9R expectancy.")
    ans = asyncio.run(C.Coach(tools, AiCfg(), client=fake).ask("?"))
    assert ans.unverified_numbers == ["80", "9.9"]


def test_ask_signal_appended_to_user_turn(tools):
    fake = FakeClient(answer="ok")
    asyncio.run(C.Coach(tools, AiCfg(), client=fake).ask("take it?", signal={"event": "break_up", "rsi": 68}))
    content = fake.calls[0]["messages"][0]["content"]
    assert content.startswith("take it?") and '"rsi": 68' in content


def test_daily_budget(tools):
    fake = FakeClient(answer="ok")
    coach = C.Coach(tools, AiCfg(daily_ask_limit=2), client=fake)
    asyncio.run(coach.ask("1")); asyncio.run(coach.ask("2"))
    assert coach.asks_today() == 2
    with pytest.raises(C.AskBudgetExceeded):
        asyncio.run(coach.ask("3"))
    # no_store answers don't count
    asyncio.run(C.Coach(tools, AiCfg(daily_ask_limit=99), client=fake).ask("x", store=False))
    assert coach.asks_today() == 2


def test_missing_credentials_is_a_clean_error(tools, monkeypatch):
    """Real SDK client, no key → CoachError with a hint, never a raw TypeError traceback.
    ANTHROPIC_BASE_URL points at a closed port so a dev box with an `ant auth` profile
    fails fast on connection instead of spending money."""
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")
    coach = C.Coach(tools, AiCfg())
    with pytest.raises(C.CoachError) as ei:
        asyncio.run(coach.ask("hi", store=False))
    assert "ANTHROPIC_API_KEY" in str(ei.value) or "cannot reach" in str(ei.value)


# ── /review ──────────────────────────────────────────────────────────────────
def test_review_structured_and_stored(tools):
    fake = FakeClient(review={"facts": ["LONG SOL 4H, exit 120 (#1)"], "metrics": ["R 2.0", "same setup n=2"],
                              "rule_violations": [], "observations": ["held to target"],
                              "questions": ["was there a retest?"]})
    coach = C.Coach(tools, AiCfg(), client=fake)
    r = asyncio.run(coach.review(1))
    assert r["trade_id"] == 1 and r["facts"] and r["prompt_version"] == "review_v1"
    assert r["unverified_numbers"] == []
    kw = fake.calls[0]
    assert kw["system"][0]["text"].startswith("You are reviewing ONE closed trade")
    assert "REVIEW CONTEXT" in kw["messages"][0]["content"]
    row = tools.db.conn.execute("SELECT trade_id, kind FROM ai_analysis").fetchone()
    assert (row["trade_id"], row["kind"]) == (1, "review")
    assert "Review of trade #1" in C.format_review(r)
    assert "error" in asyncio.run(coach.review(999))
