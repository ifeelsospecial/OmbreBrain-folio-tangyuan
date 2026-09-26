# ============================================================
# 「关于你」偏好档案
# ============================================================
import json
import pytest
from datetime import date, timedelta

import about as about_mod
from about import AboutStore, AboutError


class _FakeDehydrator:
    api_available = False

    async def dehydrate(self, content, meta=None):
        return content


class _NoVec:
    enabled = False

    async def search_similar(self, *a, **k):
        return []


@pytest.fixture
def srv(bucket_mgr, decay_eng, monkeypatch, tmp_path):
    import server
    store = AboutStore(str(tmp_path / "about-root"))
    monkeypatch.setattr(server, "bucket_mgr", bucket_mgr)
    monkeypatch.setattr(server, "decay_engine", decay_eng)
    monkeypatch.setattr(server, "dehydrator", _FakeDehydrator())
    monkeypatch.setattr(server, "embedding_engine", _NoVec())
    monkeypatch.setattr(server, "about_store", store)
    return server, bucket_mgr, store


def _shift_day(monkeypatch, days):
    monkeypatch.setattr(about_mod, "_today", lambda: (date.today() + timedelta(days=days)).isoformat())


@pytest.mark.asyncio
async def test_watching_until_seen_on_two_days(srv, monkeypatch):
    server, bm, store = srv
    out = await server.you(aspect="travel", text="喜欢慢节奏，一天两三个点")
    eid = out.split("[id:")[1].rstrip("]")
    assert "先观察着" in out
    assert "慢节奏" not in store.render_for_model()                      # 观察中的不进开场
    again = await server.you(entry_id=eid, confirm=True)
    assert "1/2" in again                                                 # 同一天再确认不算
    _shift_day(monkeypatch, 1)
    assert "定下来了" in await server.you(entry_id=eid, confirm=True)
    assert "【旅行节奏】喜欢慢节奏" in store.render_for_model()


@pytest.mark.asyncio
async def test_profile_leads_opening_surfacing(srv):
    server, bm, store = srv
    await store.add("comfort", "焦虑时先陪着她，别急着讲道理", source="her", confirmed=True)
    await store.add("food", "偏爱酸辣", source="him")
    await bm.create(content="今天去了超市")
    out = await server.breath()
    assert out.startswith("=== 关于她（我对她的了解）===")
    assert "焦虑时先陪着她" in out and "偏爱酸辣" not in out and "另有 1 条还在观察中" in out

    class _R:
        query_params = {}
    hook = (await server.breath_hook(_R())).body.decode()
    assert "焦虑时先陪着她" in hook.split("---")[0]


@pytest.mark.asyncio
async def test_you_tool_limits_and_edits(srv):
    server, bm, store = srv
    assert "aspect 只能是" in await server.you(aspect="music", text="爱听爵士")
    assert "最多 80 字" in await server.you(aspect="food", text="辣" * 81)
    out = await server.you(aspect="avoid", text="不喜欢跟团")
    eid = out.split("[id:")[1].rstrip("]")
    assert "改好了" in await server.you(entry_id=eid, revise="不喜欢跟团和赶景点")
    assert "不喜欢跟团和赶景点" in await server.you(read=True)
    assert "删掉了" in await server.you(entry_id=eid, remove=True)
    for i in range(about_mod.MAX_PER_ASPECT):
        await store.add("daily", f"习惯{i}")
    assert "已经有 8 条" in await server.you(aspect="daily", text="再来一条")


@pytest.mark.asyncio
async def test_page_actions_count_immediately(srv):
    server, bm, store = srv

    class _R:
        def __init__(self, method, body=None):
            self.method, self._b, self.query_params = method, body, {}

        async def json(self):
            return self._b
    added = json.loads((await server.api_about(_R("POST", {"action": "add", "aspect": "sights", "text": "爱看文艺复兴绘画"}))).body)
    assert added["entry"]["status"] == "confirmed" and added["entry"]["source"] == "her"
    e = await store.add("food", "不爱太甜")
    confirmed = json.loads((await server.api_about(_R("POST", {"action": "confirm", "id": e["id"]}))).body)
    assert confirmed["entry"]["status"] == "confirmed"
    bad = await server.api_about(_R("POST", {"action": "explode"}))
    assert bad.status_code == 400
    got = json.loads((await server.api_about(_R("GET"))).body)
    assert [a["key"] for a in got["aspects"]][:3] == ["travel", "sights", "food"]


@pytest.mark.asyncio
async def test_draft_is_watching_and_rerun_never_confirms(srv, monkeypatch):
    server, bm, store = srv
    for i in range(3):
        await bm.create(content=f"她在博物馆待了一整天，第{i}次", name=f"博物馆{i}")

    class _LLM:
        api_available = True
        model = "fake"

        def __init__(self):
            outer = self
            self.calls = 0

            class _C:
                async def create(self_inner, **kw):
                    outer.calls += 1
                    body = json.dumps({"sights": ["她逛博物馆能待一整天"], "travel": []}, ensure_ascii=False)
                    msg = type("M", (), {"content": body})
                    return type("R", (), {"choices": [type("C", (), {"message": msg})]})
            self.client = type("Cl", (), {"chat": type("Ch", (), {"completions": _C()})})
    llm = _LLM()
    p = {}
    await about_mod.draft_from_memories(bm, llm, store, p, pause_s=0)
    assert p["added"] == 1 and llm.calls == 2                          # 1 批 + 1 次合并
    entry = store.load()["sights"][0]
    assert entry["status"] == "watching" and entry["source"] == "memory"
    _shift_day(monkeypatch, 3)
    await about_mod.draft_from_memories(bm, llm, store, {}, pause_s=0)
    assert store.load()["sights"][0]["status"] == "watching"           # 重跑提炼不会替她定下来
