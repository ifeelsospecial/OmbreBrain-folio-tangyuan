# ============================================================
# 引语 quotes(对齐上游 3.1.0 写入 / 3.4.0 trace quotes_replace)
# ============================================================
import pytest
from mcp.server.fastmcp.exceptions import ToolError


class _FakeDehydrator:
    api_available = False

    async def dehydrate(self, content, meta=None):
        return content

    async def analyze(self, content):
        return {"domain": ["日常"], "valence": 0.5, "arousal": 0.3, "tags": [], "suggested_name": content[:8]}


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


def _only_id(buckets):
    assert len(buckets) == 1, buckets
    return buckets[0]["id"]


@pytest.mark.asyncio
async def test_quotes_hidden_by_default_and_returned_on_request(srv):
    server, bm = srv
    await server.hold(content="她在希思罗机场说她不想走", quotes=["我不想走", {"text": "你会等我吗", "speaker": "她"}])
    bid = _only_id(await bm.list_all())
    plain = await server.breath_search(query="希思罗机场")
    assert bid in plain and "我不想走" not in plain
    withq = await server.breath_search(query="希思罗机场", quotes=True)
    assert "「我不想走」" in withq and "「你会等我吗」 —— 她" in withq
    assert "我不想走" not in await server.breath()           # 浮现路径读不到
    assert "我不想走" not in await server.breath_advanced(catalog=True)


@pytest.mark.asyncio
async def test_quote_limits_reject_whole_call(srv):
    server, bm = srv
    with pytest.raises(ToolError, match="最多 3 条"):
        await server.hold(content="四句话", quotes=["一", "二", "三", "四"])
    with pytest.raises(ToolError, match="最多 100 字"):
        await server.hold(content="太长", quotes=["长" * 101])
    with pytest.raises(ToolError, match="feel 不支持引语"):
        await server.hold(content="一条 feel", feel=True, quotes=["原话"])
    assert await bm.list_all() == []                          # 什么都没存


@pytest.mark.asyncio
async def test_merge_appends_quotes_and_reports_overflow(srv, monkeypatch):
    server, bm = srv
    bid = await bm.create(content="她说周末去海边", name="周末去海边", domain=["日常"])
    await bm.update(bid, quotes=["要去海边", "带上相机"])

    async def _always_hit(*a, **k):
        b = await bm.get(bid)
        b["score"] = 99
        return [b]
    monkeypatch.setattr(bm, "search", _always_hit)
    notes = []
    name, merged = await server._merge_or_create(
        content="又说了一遍周末去海边", tags=[], importance=5, domain=["日常"], valence=0.5, arousal=0.3,
        name="", raw_merge=True, quotes=[{"text": "一定要去"}, {"text": "晚上看星星"}], notes=notes,
    )
    assert merged
    saved = [q["text"] for q in (await bm.get(bid))["metadata"]["quotes"]]
    assert saved == ["要去海边", "带上相机", "一定要去"]          # 追加不覆盖, 超限保留先来的
    assert notes and "1 条没存" in notes[0]


@pytest.mark.asyncio
async def test_grow_items_carry_quotes_and_bad_item_rejects_all(srv):
    server, bm = srv
    with pytest.raises(ToolError, match="第 2 条"):
        await server.grow(items=[{"content": "第一件事"}, {"content": "第二件事", "quotes": ["x" * 101]}])
    assert await bm.list_all() == []
    await server.grow(items=[{"content": "她今天去了泰特美术馆", "quotes": ["那幅画好安静"]}])
    bid = _only_id(await bm.list_all())
    assert (await bm.get(bid))["metadata"]["quotes"] == [{"text": "那幅画好安静"}]


@pytest.mark.asyncio
async def test_trace_quotes_replace_edit_delete_no_backfill(srv):
    server, bm = srv
    bare = await bm.create(content="没有引语的记忆")
    with pytest.raises(ToolError, match="不能补录"):
        await server.trace(bucket_id=bare, quotes_replace=["事后补的"])

    bid = await bm.create(content="有引语的记忆")
    await bm.update(bid, quotes=["原话一", "原话二"])
    with pytest.raises(ToolError, match="不能增加"):
        await server.trace(bucket_id=bid, quotes_replace=["a", "b", "c"])
    with pytest.raises(ToolError, match="不能和其他修改同时用"):
        await server.trace(bucket_id=bid, quotes_replace=["原话一"], importance=9)
    out = await server.trace(bucket_id=bid, quotes_replace=["原话一（订正）"])
    assert "「原话一（订正）」" in out
    await server.trace(bucket_id=bid, importance=6)            # 不传 quotes_replace = 不动
    assert (await bm.get(bid))["metadata"]["quotes"] == [{"text": "原话一（订正）"}]
    assert "删除" in await server.trace(bucket_id=bid, quotes_replace=[])
    assert "quotes" not in (await bm.get(bid))["metadata"]
