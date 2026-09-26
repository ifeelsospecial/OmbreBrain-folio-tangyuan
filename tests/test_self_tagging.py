# ============================================================
# 自己打标: 调用方(祁煜)写明领域时, hold / grow items 不再请外部 AI 分析(省免费额度)
# ============================================================
import pytest


class _CountingDehydrator:
    api_available = False

    def __init__(self):
        self.analyze_calls = 0

    async def dehydrate(self, content, meta=None):
        return content

    async def analyze(self, content):
        self.analyze_calls += 1
        return {"domain": ["日常"], "valence": 0.5, "arousal": 0.3, "tags": ["自动"], "suggested_name": "自动名"}

    def _default_analysis(self):
        return {"domain": ["未分类"], "valence": 0.5, "arousal": 0.3, "tags": [], "suggested_name": ""}


class _NoVec:
    enabled = False

    async def search_similar(self, *a, **k):
        return []


@pytest.fixture
def srv(bucket_mgr, decay_eng, monkeypatch):
    import server
    dh = _CountingDehydrator()
    monkeypatch.setattr(server, "bucket_mgr", bucket_mgr)
    monkeypatch.setattr(server, "decay_engine", decay_eng)
    monkeypatch.setattr(server, "dehydrator", dh)
    monkeypatch.setattr(server, "embedding_engine", _NoVec())
    monkeypatch.setattr(server, "_schedule_plan_resolution", lambda *a, **k: None)
    return server, bucket_mgr, dh


async def _only(bm):
    all_ = await bm.list_all()
    assert len(all_) == 1, all_
    return all_[0]["metadata"]


@pytest.mark.asyncio
async def test_hold_with_domain_skips_ai(srv):
    server, bm, dh = srv
    await server.hold(content="她订好了下周去罗马的机票，开心得在床上打滚", domain="出行,情绪",
                      tags="罗马,机票,旅行", name="订好罗马机票", valence=0.9, arousal=0.7)
    assert dh.analyze_calls == 0
    meta = await _only(bm)
    assert meta["domain"] == ["出行", "情绪"]
    assert {"罗马", "机票"} <= set(meta["tags"])
    assert meta["name"] == "订好罗马机票"
    assert meta["valence"] == 0.9 and meta["arousal"] == 0.7


@pytest.mark.asyncio
async def test_hold_without_domain_still_uses_ai(srv):
    server, bm, dh = srv
    await server.hold(content="今天吃了拉面", tags="拉面")
    assert dh.analyze_calls == 1
    assert (await _only(bm))["domain"] == ["日常"]


@pytest.mark.asyncio
async def test_hold_unknown_domain_falls_back(srv):
    server, bm, dh = srv
    await server.hold(content="随便一句", domain="乱写的领域")
    assert dh.analyze_calls == 1


@pytest.mark.asyncio
async def test_hold_domain_without_name_gets_readable_name(srv):
    server, bm, dh = srv
    await server.hold(content="她今天在泰特美术馆看了很久的罗斯科", domain="出行")
    meta = await _only(bm)
    assert dh.analyze_calls == 0
    assert meta["name"].startswith("她今天在泰特")


@pytest.mark.asyncio
async def test_grow_items_mixed(srv):
    server, bm, dh = srv
    out = await server.grow(items=[
        {"content": "她去了阿姆斯特丹看梵高", "domain": "出行", "tags": ["阿姆斯特丹", "梵高"], "name": "阿姆斯特丹"},
        "这一条没带领域，要请 AI 打标",
    ])
    assert "2条" in out
    assert dh.analyze_calls == 1
    by_name = {b["metadata"]["name"]: b["metadata"] for b in await bm.list_all()}
    assert by_name["阿姆斯特丹"]["domain"] == ["出行"]
