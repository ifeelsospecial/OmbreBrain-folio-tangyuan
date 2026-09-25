# ============================================================
# 为这个库定制的功能: capture-hook 原文 / 一个月前的今天 / 约定日期 等
# ============================================================
import json
import pytest


class _FakeDehydrator:
    api_available = False

    async def dehydrate(self, content, meta=None):
        return content

    async def analyze(self, content):
        return {"domain": ["日常"], "valence": 0.5, "arousal": 0.3, "tags": [], "suggested_name": content[:10]}

    async def digest(self, content):
        return [{"content": "她去了泰特美术馆看展", "name": "泰特看展", "domain": ["兴趣"]},
                {"content": "她晚上在南岸散步", "name": "南岸散步", "domain": ["出行"]}]


class _NoVec:
    enabled = False

    async def search_similar(self, *a, **k):
        return []


@pytest.fixture
def srv(bucket_mgr, decay_eng, monkeypatch):
    import server
    monkeypatch.setattr(server, "bucket_mgr", bucket_mgr)
    monkeypatch.setattr(server, "decay_engine", decay_eng)
    monkeypatch.setattr(server, "dehydrator", _FakeDehydrator())
    monkeypatch.setattr(server, "embedding_engine", _NoVec())
    return server, bucket_mgr


class _Req:
    def __init__(self, body):
        self._body = body
        self.query_params = {}

    async def json(self):
        return self._body


@pytest.mark.asyncio
async def test_capture_hook_stores_raw_dialogue_for_source(srv):
    server, bm = srv
    dialogue = "她：今天去了泰特美术馆，晚上又在南岸走了很久。\n我：听起来是很满的一天，累不累？"
    resp = await server.capture_hook(_Req({"content": dialogue}))
    assert json.loads(resp.body)["ok"]
    buckets = await bm.list_all()
    assert len(buckets) == 2
    for b in buckets:
        assert b["metadata"]["raw_source"] == dialogue
        out = await server.source(bucket_id=b["id"])
        assert "泰特美术馆" in out and "累不累" in out


def test_raw_source_append_and_cap():
    import server
    assert server._append_raw_source("", "  第一段  ") == "第一段"
    assert server._append_raw_source("第一段", "第一段") == "第一段"          # 重试不重复
    both = server._append_raw_source("第一段", "第二段")
    assert both.startswith("第一段") and both.endswith("第二段")
    long = server._append_raw_source("旧" * 7000, "新" * 3000)
    assert len(long) <= server._RAW_SOURCE_CAP
    assert long.startswith("（更早的原文已省略）") and long.endswith("新" * 3000)


# ---- 一个月 / 一年前的今天 + 约定日期 ----
from datetime import date, datetime, timedelta


def test_shift_months_edges():
    import server
    assert server._shift_months(date(2026, 3, 31), 1) == date(2026, 2, 28)
    assert server._shift_months(date(2026, 1, 15), 1) == date(2025, 12, 15)
    assert server._shift_months(date(2026, 9, 26), 12) == date(2025, 9, 26)


@pytest.mark.asyncio
async def test_on_this_day_prefers_event_time_and_weight(srv, monkeypatch):
    server, bm = srv
    today = datetime.utcnow().date()
    month_ago = server._shift_months(today, 1).isoformat()
    small = await bm.create(content="一个月前的小事", importance=3, event_time=month_ago)
    big = await bm.create(content="一个月前的大事", importance=9, arousal=0.8, event_time=month_ago)
    await bm.update(big, resolved=True)   # 已沉底的事在"那天"仍会被想起; 也避免它先出现在普通浮现里
    await bm.create(content="一个月前的 feel", bucket_type="feel", event_time=month_ago)
    await bm.create(content="昨天的事", event_time=(today - timedelta(days=1)).isoformat())
    picks = server._on_this_day(await bm.list_all())
    assert [b["id"] for _, b in picks] == [big]
    assert "一个月前的今天" in picks[0][0]
    out = await server.breath()
    assert "=== 那天 ===" in out and "一个月前的大事" in out
    monkeypatch.setitem(server.config, "surfacing", {"on_this_day": False})
    assert server._on_this_day(await bm.list_all()) == []


@pytest.mark.asyncio
async def test_plan_due_surfaces_when_close(srv):
    server, bm = srv
    today = datetime.utcnow().date()
    assert "格式不对" in await server.plan(content="坏日期", due="下周六")
    await server.plan(content="陪她去看展", due=(today + timedelta(days=1)).isoformat())
    await server.plan(content="很久以后的事", due=(today + timedelta(days=30)).isoformat())
    await server.plan(content="早就过期的事", due=(today - timedelta(days=10)).isoformat())
    await server.plan(content="没日期的事")
    out = await server.breath()
    assert "=== 快到的约定 ===" in out
    assert "陪她去看展" in out and "明天" in out
    assert "很久以后的事" not in out and "早就过期的事" not in out and "没日期的事" not in out
    plan_out = await server.breath_advanced(domain="plan")
    assert "[约在:" in plan_out


@pytest.mark.asyncio
async def test_trace_can_move_or_clear_due_on_plans_only(srv):
    server, bm = srv
    today = datetime.utcnow().date()
    await server.plan(content="改期的约定", due=(today + timedelta(days=20)).isoformat())
    pid = [b["id"] for b in await bm.list_all() if b["metadata"].get("type") == "plan"][0]
    await server.trace(bucket_id=pid, due=today.isoformat())
    assert (await bm.get(pid))["metadata"]["due"] == today.isoformat()
    assert "就是今天" in await server.breath()
    await server.trace(bucket_id=pid, due="")
    assert "due" not in (await bm.get(pid))["metadata"]
    normal = await bm.create(content="普通记忆")
    assert "只能用于 plan" in await server.trace(bucket_id=normal, due=today.isoformat())


@pytest.mark.asyncio
async def test_breath_hook_carries_plans_and_that_day(srv):
    server, bm = srv
    today = datetime.utcnow().date()
    await server.plan(content="周末去海边", due=today.isoformat())
    yb = await bm.create(content="一年前的今天她在爱丁堡", event_time=server._shift_months(today, 12).isoformat())
    await bm.update(yb, resolved=True)
    resp = await server.breath_hook(_Req({}))
    text = resp.body.decode()
    assert text.index("📅 约好的事") < text.index("🕰 一年前的今天")
    assert "周末去海边" in text and "爱丁堡" in text
