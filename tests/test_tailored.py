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
