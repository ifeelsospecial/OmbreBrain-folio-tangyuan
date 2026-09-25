# ============================================================
# 桶间关系(对齐上游 3.2.0 自动建立 / 3.3.0 trace 修正) + 核心准则不可被消化
# ============================================================
import asyncio
import pytest
from mcp.server.fastmcp.exceptions import ToolError

import relation_link
from relation_store import normalize_relation_links


class _Vec:
    enabled = True

    def __init__(self):
        self.scores = {}

    async def search_similar(self, query, top_k=10):
        return sorted(self.scores.items(), key=lambda kv: -kv[1])[:top_k]


class _FakeDehydrator:
    api_available = True

    async def dehydrate(self, content, meta=None):
        return content


@pytest.fixture
def srv(bucket_mgr, decay_eng, monkeypatch):
    import server
    vec = _Vec()
    monkeypatch.setattr(server, "bucket_mgr", bucket_mgr)
    monkeypatch.setattr(server, "decay_engine", decay_eng)
    monkeypatch.setattr(server, "dehydrator", _FakeDehydrator())
    monkeypatch.setattr(server, "embedding_engine", vec)
    return server, bucket_mgr, vec


async def _links(bm, bid):
    return {l["target_bucket_id"]: l for l in normalize_relation_links((await bm.get(bid))["metadata"].get("relation_links"))}


@pytest.mark.asyncio
async def test_inference_thresholds_types_and_exclusions(srv):
    server, bm, vec = srv
    src = await bm.create(content="她在伦敦面试")
    same = await bm.create(content="面试当天的细节")
    related = await bm.create(content="很久以前的伦敦")
    weak = await bm.create(content="无关")
    feel = await bm.create(content="一条 feel", bucket_type="feel")
    vec.scores = {src: 1.0, same: 0.90, related: 0.73, weak: 0.60, feel: 0.99}
    inferred = {l["target_bucket_id"]: l for l in await relation_link.infer_links_for(bm, vec, src, "她在伦敦面试")}
    assert inferred[same]["type"] == "same_event"          # ≥0.85 且同一时间
    assert inferred[related]["type"] in ("related_to", "continuation_of", "same_event")
    assert weak not in inferred                              # 低于 0.72
    assert feel not in inferred and src not in inferred      # feel 不连; 不连自己


@pytest.mark.asyncio
async def test_new_bucket_links_both_sides_in_background(srv):
    server, bm, vec = srv
    old = await bm.create(content="第一次去大英博物馆", name="大英博物馆")
    vec.scores = {old: 0.80}
    new_name, merged = await server._merge_or_create(
        content="又去了一次大英博物馆", tags=[], importance=5, domain=["旅行"],
        valence=0.6, arousal=0.4, name="再去大英博物馆",
    )
    assert not merged
    await asyncio.gather(*list(server._BG_TASKS))
    new = [b["id"] for b in await bm.list_all() if b["id"] != old][0]
    fwd, back = await _links(bm, new), await _links(bm, old)
    assert fwd[old]["auto"] and back[new]["auto"]
    # 反向类型成对: continuation_of <-> continues, related_to/same_event 自反
    pair = {fwd[old]["type"], back[new]["type"]}
    assert pair in ({"related_to"}, {"same_event"}, {"continuation_of", "continues"})


@pytest.mark.asyncio
async def test_hint_shows_only_visible_targets(srv):
    server, bm, vec = srv
    a = await bm.create(content="伦敦的雨", name="伦敦的雨")
    b = await bm.create(content="伦敦的伞", name="伦敦的伞")
    vec.scores = {b: 0.80}
    await relation_link.link_new_bucket(bm, vec, a, "伦敦的雨")
    vec.scores = {}
    out = await server.breath_search(query="伦敦的雨")
    assert f"→ {b}" in out
    await bm.delete(b)                                      # 目标进回收站后不再显示
    out = await server.breath_search(query="伦敦的雨")
    assert f"→ {b}" not in out


@pytest.mark.asyncio
async def test_trace_unlink_and_relink(srv):
    server, bm, vec = srv
    a = await bm.create(content="事件 A")
    b = await bm.create(content="事件 B")
    vec.scores = {b: 0.80}
    assert await relation_link.link_new_bucket(bm, vec, a, "事件 A") == 1

    out = await server.trace(bucket_id=a, relink=b, relation_type="caused_by")
    assert "caused_by" in out and "causes" in out
    fwd, back = await _links(bm, a), await _links(bm, b)
    assert fwd[b]["type"] == "caused_by" and back[a]["type"] == "causes"
    assert "auto" not in fwd[b] and "auto" not in back[a]       # 降为手动关系

    out = await server.trace(bucket_id=a, unlink=b)
    assert "已断开" in out
    assert await _links(bm, a) == {} and await _links(bm, b) == {}
    assert "本来就没有关系" in await server.trace(bucket_id=a, unlink=b)


@pytest.mark.asyncio
async def test_trace_relation_edit_rejections(srv):
    server, bm, vec = srv
    a = await bm.create(content="A")
    b = await bm.create(content="B")
    with pytest.raises(ToolError, match="不能凭空建立"):
        await server.trace(bucket_id=a, relink=b, relation_type="related_to")
    with pytest.raises(ToolError, match="custom"):
        await server.trace(bucket_id=a, relink=b, relation_type="custom")
    with pytest.raises(ToolError, match="必须同时指定"):
        await server.trace(bucket_id=a, relink=b)
    with pytest.raises(ToolError, match="不能和其他修改同时用"):
        await server.trace(bucket_id=a, unlink=b, importance=9)
    with pytest.raises(ToolError, match="找不到目标"):
        await server.trace(bucket_id=a, unlink="000000000000")
    with pytest.raises(ToolError, match="自己"):
        await server.trace(bucket_id=a, unlink=a)


@pytest.mark.asyncio
async def test_core_principle_survives_internalized_mark(srv):
    server, bm, vec = srv
    core = await bm.create(content="她难过时先抱抱再讲道理", name="准则", highlight=True)
    await bm.update(core, internalized=True)
    out = await server.breath()
    assert "她难过时先抱抱再讲道理" in out


# ---- 存量回填 + 星图数据 ----

def _set_created(bm, bid, iso):
    import frontmatter
    path = bm._find_bucket_file(bid)
    post = frontmatter.load(path)
    post["created"] = iso
    with open(path, "w", encoding="utf-8") as f:
        f.write(frontmatter.dumps(post))
    bm._invalidate_active_cache()


@pytest.mark.asyncio
async def test_backfill_links_newer_to_older_once_and_is_idempotent(srv):
    server, bm, vec = srv
    old = await bm.create(content="第一次见面")
    new = await bm.create(content="第二次见面")
    far = await bm.create(content="不相关")
    _set_created(bm, old, "2026-07-01T10:00:00Z")
    _set_created(bm, new, "2026-07-02T10:00:00Z")   # 24 小时后
    _set_created(bm, far, "2026-06-01T10:00:00Z")
    vec.scores = {old: 0.80, new: 0.80, far: 0.10}
    progress = {}
    await relation_link.backfill_links(bm, vec, progress, pause_s=0)
    assert progress["total"] == 3 and progress["errors"] == 0 and progress["built"] == 1
    fwd, back = await _links(bm, new), await _links(bm, old)
    assert fwd[old]["type"] == "continuation_of" and back[new]["type"] == "continues"   # 方向: 新的接续旧的
    assert await _links(bm, far) == {}
    progress2 = {}
    await relation_link.backfill_links(bm, vec, progress2, pause_s=0)
    assert progress2["built"] == 0                                                       # 重跑不重复
    assert len(await _links(bm, new)) == 1


@pytest.mark.asyncio
async def test_backfill_endpoint_and_relations_in_bucket_api(srv, monkeypatch):
    server, bm, vec = srv
    a = await bm.create(content="A 事")
    b = await bm.create(content="B 事")
    vec.scores = {a: 0.80, b: 0.80}

    class _Req:
        def __init__(self, method):
            self.method = method
    monkeypatch.setattr(server, "_RELATION_BACKFILL", {"running": False})
    resp = await server.api_relations_backfill(_Req("POST"))
    assert resp.status_code == 202
    await asyncio.gather(*list(server._BG_TASKS))
    import json
    status = json.loads((await server.api_relations_backfill(_Req("GET"))).body)
    assert status["running"] is False and status["built"] == 1 and status["finished_at"]
    rels = server._relations_for_api((await bm.get(a))["metadata"]) + server._relations_for_api((await bm.get(b))["metadata"])
    assert {r["target"] for r in rels} == {a, b} and all(r["label"] for r in rels)
    assert server._relations_for_api({"relation_links": "broken"}) == []
