# ============================================================
# 对齐上游 3.6.0: 检索只读 / trace reinforce / 日期过滤 / 近期新桶浮现名额
# ============================================================
import pytest
import frontmatter
from datetime import datetime, timedelta


class _FakeDehydrator:
    api_available = True

    async def dehydrate(self, content, meta=None):
        return content


class _NoVectors:
    enabled = False

    async def search_similar(self, *a, **k):
        return []


@pytest.fixture
def srv(bucket_mgr, decay_eng, monkeypatch):
    import server
    monkeypatch.setattr(server, "bucket_mgr", bucket_mgr)
    monkeypatch.setattr(server, "decay_engine", decay_eng)
    monkeypatch.setattr(server, "dehydrator", _FakeDehydrator())
    monkeypatch.setattr(server, "embedding_engine", _NoVectors())
    return server, bucket_mgr


def _backdate(bm, bucket_id, days, field="created"):
    path = bm._find_bucket_file(bucket_id)
    post = frontmatter.load(path)
    ts = (datetime.utcnow() - timedelta(days=days)).isoformat(timespec="seconds") + "Z"
    post[field] = ts
    if field == "created":
        post["last_active"] = ts
    with open(path, "w", encoding="utf-8") as f:
        f.write(frontmatter.dumps(post))
    bm._invalidate_active_cache()


async def _count(bm, bucket_id):
    return (await bm.get(bucket_id))["metadata"].get("activation_count", 0)


@pytest.mark.asyncio
async def test_breath_search_is_read_only(srv):
    server, bm = srv
    bid = await bm.create(content="伦敦的美术馆很安静", name="伦敦美术馆", domain=["旅行"])
    before = await _count(bm, bid)
    out = await server.breath_search(query="伦敦美术馆")
    assert bid in out
    import asyncio
    await asyncio.sleep(0.05)  # 旧实现是后台 task touch, 给它机会跑
    assert await _count(bm, bid) == before


@pytest.mark.asyncio
async def test_trace_reinforce_is_the_explicit_boost(srv):
    server, bm = srv
    bid = await bm.create(content="她第一次去伦敦", name="第一次去伦敦")
    before = await _count(bm, bid)
    out = await server.trace(bucket_id=bid, reinforce=True)
    assert "已强化" in out
    assert await _count(bm, bid) == before + 1

    mixed = await server.trace(bucket_id=bid, reinforce=True, importance=9)
    assert "不能和其他修改同时用" in mixed and "importance" in mixed
    assert await _count(bm, bid) == before + 1
    assert "未找到" in await server.trace(bucket_id="000000000000", reinforce=True)


@pytest.mark.asyncio
async def test_date_range_filters_search_and_prefers_event_time(srv):
    server, bm = srv
    old = await bm.create(content="七月的伦敦下雨", name="七月伦敦", event_time="2026-07-15")
    new = await bm.create(content="九月的伦敦下雨", name="九月伦敦", event_time="2026-09-10")
    out = await server.breath_search(query="伦敦", date_from="2026-07-01", date_to="2026-07-31")
    assert old in out and new not in out
    # date_to 纯日期包含当天全天
    out = await server.breath_search(query="伦敦", date_from="2026-09-10", date_to="2026-09-10")
    assert new in out and old not in out
    assert "日期格式不对" in await server.breath_search(query="伦敦", date_from="2026-13-01")
    assert "晚于" in await server.breath_search(query="伦敦", date_from="2026-09-01", date_to="2026-08-01")


@pytest.mark.asyncio
async def test_date_range_applies_to_feel_and_catalog(srv):
    server, bm = srv
    await bm.create(content="七月的 feel", bucket_type="feel", event_time="2026-07-02")
    await bm.create(content="九月的 feel", bucket_type="feel", event_time="2026-09-02")
    out = await server.breath_advanced(domain="feel", date_from="2026-07-01", date_to="2026-07-31")
    assert "七月的 feel" in out and "九月的 feel" not in out
    await bm.create(content="七月的事", name="七月目录项", event_time="2026-07-03")
    await bm.create(content="九月的事", name="九月目录项", event_time="2026-09-03")
    cat = await server.breath_advanced(catalog=True, date_to="2026-07-31")
    assert "七月目录项" in cat and "九月目录项" not in cat


@pytest.mark.asyncio
async def test_surfacing_reserves_slots_for_recent_buckets(srv, monkeypatch):
    server, bm = srv
    monkeypatch.setitem(server.config, "surfacing", {"recent_slots": 2, "recent_days": 7})
    monkeypatch.setattr(server.random, "shuffle", lambda seq: None)  # 关掉 top-20 多样性抽样, 结果可复现
    olds = []
    for i in range(8):
        bid = await bm.create(content=f"很久以前的要紧事{i}", name=f"旧事{i}", importance=9, arousal=0.9)
        _backdate(bm, bid, 60)
        olds.append(bid)
    recent = []
    for i in range(2):
        bid = await bm.create(content=f"这周的小事{i}", name=f"新事{i}", importance=2, arousal=0.1)
        recent.append(bid)
    # 让旧桶都"被想起过", 避免它们走冷启动通道; 新桶低重要度也不走冷启动
    for bid in olds:
        await bm.touch(bid)
    out = await server.breath(max_results=4)
    for bid in recent:
        assert bid in out, out
    monkeypatch.setitem(server.config, "surfacing", {"recent_slots": 0})
    out = await server.breath(max_results=4)
    assert not any(bid in out for bid in recent)
