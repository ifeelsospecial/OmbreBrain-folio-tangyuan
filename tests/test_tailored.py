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


# ---- 「未分类」重新分类 ----

@pytest.mark.asyncio
async def test_reclassify_only_touches_domain_and_tags(srv, monkeypatch):
    import reclassify
    server, bm = srv

    class _Analyzer:
        api_available = True

        async def analyze(self, content):
            if "看不出" in content:
                return {"domain": ["未分类"], "tags": []}
            return {"domain": ["出行", "兴趣"], "tags": ["伦敦", "美术馆"], "suggested_name": "不该用"}

    stuck = await bm.create(content="她去了伦敦的美术馆", name="原来的名字", domain=["未分类"],
                            tags=["旧标签"], valence=0.9, arousal=0.2)
    vague = await bm.create(content="看不出是什么", domain=["未分类"])
    fine = await bm.create(content="本来就分好类的", domain=["居家"])
    before = (await bm.get(stuck))["metadata"]
    progress = {}
    await reclassify.reclassify_uncategorized(bm, _Analyzer(), progress, pause_s=0)
    assert progress["total"] == 2 and progress["changed"] == 1 and progress["skipped"] == 1
    after = (await bm.get(stuck))["metadata"]
    assert after["domain"] == ["出行", "兴趣"]
    assert after["tags"] == ["旧标签", "伦敦", "美术馆"]
    for key in ("name", "valence", "arousal", "last_active", "importance"):
        assert after.get(key) == before.get(key), key          # 其他都不动, 尤其不刷新激活时间
    assert "/出行/" in bm._find_bucket_file(stuck)
    assert (await bm.get(vague))["metadata"]["domain"] == ["未分类"]
    assert (await bm.get(fine))["metadata"]["domain"] == ["居家"]


@pytest.mark.asyncio
async def test_reclassify_endpoint_requires_ai(srv, monkeypatch):
    server, bm = srv

    class _R:
        method = "POST"
        query_params = {}
    monkeypatch.setattr(server, "_RECLASSIFY", {"running": False})
    resp = await server.api_reclassify_uncategorized(_R())
    assert resp.status_code == 202
    import asyncio
    await asyncio.gather(*list(server._BG_TASKS))
    assert "AI 接口不可用" in server._RECLASSIFY["last_error"] and server._RECLASSIFY["finished_at"]


# ---- 每周回顾 ----

def _age(bm, bid, days):
    import frontmatter
    path = bm._find_bucket_file(bid)
    post = frontmatter.load(path)
    ts = (datetime.utcnow() - timedelta(days=days)).isoformat(timespec="seconds") + "Z"
    post["created"] = ts
    post["last_active"] = ts
    with open(path, "w", encoding="utf-8") as f:
        f.write(frontmatter.dumps(post))
    bm._invalidate_active_cache()


@pytest.mark.asyncio
async def test_review_candidates_filtering_and_order(srv):
    server, bm = srv
    old_small = await bm.create(content="一个月前办的小手续", importance=3)
    old_mid = await bm.create(content="三周前的普通一天", importance=6)
    recent = await bm.create(content="上周的事", importance=3)
    important = await bm.create(content="很重要的旧事", importance=9)
    pinned = await bm.create(content="钉选的旧事", importance=3, pinned=True)
    feel = await bm.create(content="旧 feel", bucket_type="feel", importance=3)
    for bid, days in ((old_small, 30), (old_mid, 21), (recent, 7), (important, 40), (pinned, 40), (feel, 40)):
        _age(bm, bid, days)
    ids = [r["id"] for r in server._review_candidates(await bm.list_all())]
    assert ids == [old_small, old_mid]


@pytest.mark.asyncio
async def test_review_decide_resolve_and_keep(srv):
    server, bm = srv
    a = await bm.create(content="该放下的", importance=3)
    b = await bm.create(content="想留着的", importance=3)
    _age(bm, a, 30)
    _age(bm, b, 30)
    before_active = (await bm.get(a))["metadata"]["last_active"]

    class _R:
        def __init__(self, body):
            self._b = body
            self.query_params = {}

        async def json(self):
            return self._b
    assert json.loads((await server.api_review_decide(_R({"id": a, "action": "resolve"}))).body)["ok"]
    assert json.loads((await server.api_review_decide(_R({"id": b, "action": "keep"}))).body)["ok"]
    ma, mb = (await bm.get(a))["metadata"], (await bm.get(b))["metadata"]
    assert ma["resolved"] is True and ma["last_active"] == before_active     # 不刷新激活时间
    assert mb["review_keep_until"] > datetime.utcnow().date().isoformat()
    assert server._review_candidates(await bm.list_all()) == []
    bad = await server.api_review_decide(_R({"id": a, "action": "delete"}))
    assert bad.status_code == 400


# ---- 足迹地图 ----
import places as places_mod


class _PlaceLLM:
    """假 AI: 按正文里的关键词返回地点 JSON; 记录被调用次数。"""
    api_available = True
    model = "fake"

    def __init__(self):
        self.calls = 0
        outer = self

        class _Completions:
            async def create(self_inner, **kw):
                outer.calls += 1
                text = kw["messages"][1]["content"]
                out = []
                if "泰特" in text:
                    out.append({"name": "泰特现代美术馆", "city": "伦敦", "country": "英国", "lat": 51.5076, "lon": -0.0994})
                if "南岸" in text:
                    out.append({"name": "南岸", "city": "伦敦", "country": "英国", "lat": 51.5055, "lon": -0.1160})
                if "爱丁堡" in text:
                    out.append({"name": "爱丁堡", "city": "爱丁堡", "country": "英国", "lat": 55.9533, "lon": -3.1883})
                msg = type("M", (), {"content": "好的，结果如下：" + json.dumps({"places": out}, ensure_ascii=False)})
                return type("R", (), {"choices": [type("C", (), {"message": msg})]})

        self.client = type("Cl", (), {"chat": type("Ch", (), {"completions": _Completions()})})

    async def dehydrate(self, content, meta=None):
        return content

    async def analyze(self, content):
        return {"domain": ["出行"], "valence": 0.6, "arousal": 0.4, "tags": [], "suggested_name": content[:8]}


def test_place_filters_and_normalization():
    assert places_mod.should_extract({"type": "dynamic", "domain": ["出行"]}, "随便")
    assert places_mod.should_extract({"type": "dynamic", "domain": ["内心"]}, "今天去了海边")
    assert not places_mod.should_extract({"type": "dynamic", "domain": ["内心"]}, "有点累")
    assert not places_mod.should_extract({"type": "feel", "domain": ["出行"]}, "去了伦敦")
    norm = places_mod.normalize_places([
        {"name": "A", "city": "伦敦", "lat": 51.5, "lon": -0.1},
        {"name": "A", "city": "伦敦", "lat": 51.5, "lon": -0.1},     # 重复
        {"name": "零点", "lat": 0, "lon": 0},                          # 明显无效
        {"name": "越界", "lat": 123, "lon": 0},
        {"name": "坏", "lat": "x", "lon": 1},
    ])
    assert [p["name"] for p in norm] == ["A"]


@pytest.mark.asyncio
async def test_hold_auto_extracts_places_and_map_aggregates(srv, monkeypatch):
    server, bm = srv
    llm = _PlaceLLM()
    monkeypatch.setattr(server, "dehydrator", llm)
    await server.hold(content="她下午去了泰特现代美术馆，晚上在南岸散步")
    await server.hold(content="一年前她在爱丁堡看城堡")
    await server.hold(content="今天有点累，不想说话")                 # 没有去向字眼、领域也不是出行? analyze 返回出行 → 仍会认
    import asyncio
    await asyncio.gather(*list(server._BG_TASKS))
    resp = await server.api_places(type("R", (), {"query_params": {}})())
    data = json.loads(resp.body)
    cities = {g["city"]: g for g in data["places"]}
    assert set(cities) == {"伦敦", "爱丁堡"}
    assert cities["伦敦"]["count"] == 1                                # 同一条记忆在同城只算一次
    assert cities["伦敦"]["spots"] == ["南岸", "泰特现代美术馆"]
    assert data["memories_with_places"] == 2


@pytest.mark.asyncio
async def test_places_backfill_skips_already_checked(srv, monkeypatch):
    server, bm = srv
    llm = _PlaceLLM()
    a = await bm.create(content="她去了爱丁堡", domain=["出行"])
    b = await bm.create(content="她在家看书", domain=["居家"])        # 不会被看
    progress = {}
    await places_mod.backfill_places(bm, llm, progress, pause_s=0)
    assert progress["total"] == 1 and progress["with_places"] == 1 and llm.calls == 1
    assert (await bm.get(a))["metadata"]["places"][0]["city"] == "爱丁堡"
    assert "places" not in (await bm.get(b))["metadata"]
    progress2 = {}
    await places_mod.backfill_places(bm, llm, progress2, pause_s=0)
    assert progress2["total"] == 0 and llm.calls == 1                 # 看过的不再看


# ---- 知识本 ----

@pytest.mark.asyncio
async def test_knowledge_stays_out_of_surfacing_but_is_searchable(srv):
    server, bm = srv
    k = await bm.create(content="德国坐火车前要先在月台打卡验票", name="德国火车验票", tags=["handbook", "旅行"], domain=["出行"])
    n = await bm.create(content="今天吃了拉面", name="拉面")
    out = await server.breath()
    assert n in out and k not in out
    assert k in await server.breath_search(query="德国火车验票")
    chan = await server.breath_advanced(domain="知识")
    assert "=== 知识本 · 1 条 ===" in chan and "【出行】" in chan and "月台打卡" in chan
    _age(bm, k, 40)
    assert k not in [r["id"] for r in server._review_candidates(await bm.list_all())]


@pytest.mark.asyncio
async def test_notes_api_create_and_list(srv):
    server, bm = srv

    class _R:
        def __init__(self, method, body=None):
            self.method, self._b, self.query_params = method, body, {}

        async def json(self):
            return self._b
    bad = await server.api_notes(_R("POST", {"title": "空"}))
    assert bad.status_code == 400
    resp = await server.api_notes(_R("POST", {"title": "焦虑三步", "content": "先呼吸，再命名感受，最后做一件小事", "topic": "身心"}))
    note = json.loads(resp.body)["note"]
    meta = (await bm.get(note["id"]))["metadata"]
    assert meta["created_by"] == "user" and "handbook" in meta["tags"] and note["topic"] == "身心" and note["mine"]
    listed = json.loads((await server.api_notes(_R("GET"))).body)
    assert [n["title"] for n in listed["notes"]] == ["焦虑三步"] and listed["topics"] == [["身心", 1]]


class _NoteReq:
    def __init__(self, note_id, body=None):
        self.path_params, self._b, self.query_params, self.method = {"note_id": note_id}, body, {}, "POST"

    async def json(self):
        return self._b


def test_parse_note_body_splits_about_qa_source():
    body = ("卡拉瓦乔用强烈明暗对比。\n他在罗马画了很多教堂祭坛画。\n\n叩问\n问：为什么光从左上来？\n答：模拟窗户，\n也像神的召唤。\n"
            "问：他为什么逃离罗马？\n答：杀了人。\n\n来源：维基 https://zh.wikipedia.org/wiki/卡拉瓦乔 和 https://example.com/a")
    p = server_mod().__dict__["_parse_note_body"](body)
    assert p["about"] == "卡拉瓦乔用强烈明暗对比。\n他在罗马画了很多教堂祭坛画。"
    assert p["qa"] == [{"q": "为什么光从左上来？", "a": "模拟窗户，\n也像神的召唤。"}, {"q": "他为什么逃离罗马？", "a": "杀了人。"}]
    assert p["source"]["urls"] == ["https://zh.wikipedia.org/wiki/卡拉瓦乔", "https://example.com/a"]
    plain = server_mod()._parse_note_body("只有正文\n来源不在行首：x")
    assert plain == {"about": "只有正文\n来源不在行首：x", "qa": [], "source": None}


def server_mod():
    import server
    return server


@pytest.mark.asyncio
async def test_note_detail_tags_notes_and_related(srv):
    server, bm = srv
    tags = ["handbook", "知识", "讲解", "italy2026", "城市:罗马", "画家：卡拉瓦乔", "巴洛克"]
    a = await bm.create(content="《圣马太蒙召》\n来源：https://a.example/x", name="圣马太蒙召", tags=tags, domain=["艺术"])
    b = await bm.create(content="卡拉瓦乔另一幅", name="以马忤斯的晚餐", tags=["handbook", "画家:卡拉瓦乔", "城市:伦敦"], domain=["艺术"])
    c = await bm.create(content="罗马的喷泉", name="特雷维", tags=["handbook", "城市:罗马"], domain=["出行"])
    d = await bm.create(content="被关系连上的知识", name="连着的", tags=["handbook"], domain=["艺术"])
    await bm.create(content="普通记忆也有罗马", name="普通", tags=["城市:罗马"])
    await bm.update(a, meaning=["光是救赎的隐喻", "她的笔记：想去圣王路易堂看原作"])
    import frontmatter
    path = bm._find_bucket_file(d)   # 关系只记在 d 那头, 检验反向也能翻到
    post = frontmatter.load(path)
    post["relation_links"] = [{"target_bucket_id": a, "type": "related_to", "status": "active"}]
    with open(path, "w", encoding="utf-8") as f:
        f.write(frontmatter.dumps(post))
    bm._invalidate_active_cache()
    resp = await server.api_note_detail(_NoteReq(a))
    note = json.loads(resp.body)["note"]
    assert note["kind_tags"] == [{"kind": "城市", "name": "罗马"}, {"kind": "画家", "name": "卡拉瓦乔"}]
    assert "tags" not in note and "罗马" in note["cities"]   # 普通标签(巴洛克)不显示
    assert note["his_notes"] == ["光是救赎的隐喻"]
    assert [n["text"] for n in note["her_notes"]] == ["想去圣王路易堂看原作"]
    assert note["source"]["urls"] == ["https://a.example/x"] and "来源" not in note["about"]
    rel = {r["id"]: r["reasons"] for r in note["related"]}
    assert set(rel) == {b, c, d}   # 普通记忆不算
    assert rel[b] == ["同一位画家 · 卡拉瓦乔"] and rel[c] == ["同一座城市 · 罗马"] and rel[d] == ["记忆里连着"]
    assert (await server.api_note_detail(_NoteReq("nope"))).status_code == 404


@pytest.mark.asyncio
async def test_her_note_appends_to_metadata(srv):
    server, bm = srv
    k = await bm.create(content="德国火车验票", name="验票", tags=["handbook"], domain=["出行"])
    n = await bm.create(content="拉面", name="拉面")
    assert (await server.api_note_her_note(_NoteReq(k, {"text": "  "}))).status_code == 400
    assert (await server.api_note_her_note(_NoteReq(n, {"text": "x"}))).status_code == 404   # 不是知识
    ok = json.loads((await server.api_note_her_note(_NoteReq(k, {"text": "慕尼黑也要打卡"}))).body)
    await server.api_note_her_note(_NoteReq(k, {"text": "第二条"}))
    hn = (await bm.get(k))["metadata"]["her_notes"]
    assert ok["ok"] and [x["text"] for x in hn] == ["慕尼黑也要打卡", "第二条"] and hn[0]["at"].endswith("Z")
    detail = json.loads((await server.api_note_detail(_NoteReq(k))).body)["note"]
    assert [x["text"] for x in detail["her_notes"]] == ["慕尼黑也要打卡", "第二条"]


@pytest.mark.asyncio
async def test_her_notes_dated_sorted_and_visible_to_him(srv):
    server, bm = srv
    k = await bm.create(content="卡拉瓦乔用强光打在人物脸上。\n\n叩问\n问：为什么？\n答：戏剧性。\n来源：https://x.example",
                        name="明暗", tags=["handbook"], domain=["艺术"])
    await bm.update(k, meaning=["他的一句话", "她的笔记（2026-09-30）：原作比画册暗很多", "她的笔记：没写日期的一条"])
    await bm.update(k, her_notes_append={"text": "博尔盖塞要提前订票", "at": "2026-09-28T10:00:00Z"})
    await bm.update(k, her_notes_append={"text": "十月再去一次", "at": "2026-10-02T09:00:00Z"})
    hers = server._her_notes_of((await bm.get(k))["metadata"])
    assert [(n["text"], n["at"][:10]) for n in hers] == [
        ("没写日期的一条", ""), ("博尔盖塞要提前订票", "2026-09-28"),
        ("原作比画册暗很多", "2026-09-30"), ("十月再去一次", "2026-10-02")]
    # 列表卡片只预览「关于」
    row = server._note_row(await bm.get(k))
    assert row["preview"] == "卡拉瓦乔用强光打在人物脸上。"
    # 她的笔记进检索, 且 breath_search 返回时一并带出
    out = await server.breath_search(query="博尔盖塞")
    assert k in out and "[她的笔记]" in out and "- (2026-09-28) 博尔盖塞要提前订票" in out and "(2026-09-30) 原作比画册暗很多" in out
    chan = await server.breath_advanced(domain="知识")
    assert "[她的笔记]" in chan and "十月再去一次" in chan


@pytest.mark.asyncio
async def test_places_merge_city_into_tags(srv):
    server, bm = srv
    llm = _PlaceLLM()
    bid = await bm.create(content="她在爱丁堡", tags=["旅行"], domain=["出行"])
    await places_mod.attach_places(bm, llm, bid, "她在爱丁堡")
    assert (await bm.get(bid))["metadata"]["tags"] == ["旅行", "爱丁堡"]


@pytest.mark.asyncio
async def test_decay_never_archives_knowledge(srv):
    server, bm = srv
    k = await bm.create(content="很久以前学的一招", tags=["handbook"], importance=1, arousal=0.0)
    n = await bm.create(content="很久以前的小事", importance=1, arousal=0.0)
    _age(bm, k, 400)
    _age(bm, n, 400)
    await server.decay_engine.run_decay_cycle()
    assert (await bm.get(k))["metadata"].get("type") != "archived"
    assert (await bm.get(n))["metadata"].get("type") == "archived"      # 对照: 普通记忆确实会被归档


# ---- 日记 ----

class _LetterReq:
    def __init__(self, letter_id=None, body=None, method="POST"):
        self.path_params, self._b, self.query_params, self.method = {"letter_id": letter_id}, body, {}, method

    async def json(self):
        return self._b


@pytest.mark.asyncio
async def test_diary_groups_by_day_and_side(srv, monkeypatch):
    server, bm = srv
    monkeypatch.setenv("AI_NAME", "祁煜")
    await server.letter_write(author="ai", content="今天她去了博尔盖塞。\n我在等她回来。", title="日记 · 2026-09-27", date="2026-09-27")
    await server.api_letters(_LetterReq(body={"author": "user", "content": "看到了阿波罗与达芙妮", "title": "日记 · 2026-09-27", "date": "2026-09-27"}))
    await server.api_letters(_LetterReq(body={"author": "user", "content": "前一天", "title": "日记·2026-09-26"}))
    await server.letter_write(author="ai", content="普通的信", title="写给她的信", date="2026-09-27")
    days = json.loads((await server.api_diary(_LetterReq(method="GET"))).body)["days"]
    assert [d["date"] for d in days] == ["2026-09-27", "2026-09-26"]
    assert [e["content"] for e in days[0]["his"]] == ["今天她去了博尔盖塞。\n我在等她回来。"]
    assert [e["content"] for e in days[0]["hers"]] == ["看到了阿波罗与达芙妮"]
    assert days[1]["his"] == [] and days[1]["hers"][0]["content"] == "前一天"   # 无空格、无 date 字段也认


@pytest.mark.asyncio
async def test_letter_comments_both_sides_and_letter_read(srv, monkeypatch):
    server, bm = srv
    monkeypatch.setenv("AI_NAME", "祁煜")
    resp = await server.api_letters(_LetterReq(body={"author": "user", "content": "今天有点累", "title": "日记 · 2026-09-27", "date": "2026-09-27"}))
    lid = json.loads(resp.body)["id"]
    assert (await server.api_letter_comments(_LetterReq(lid, {"text": " "}))).status_code == 400
    assert (await server.api_letter_comments(_LetterReq("nope", {"text": "x"}))).status_code == 404
    mine = json.loads((await server.api_letter_comments(_LetterReq(lid, {"text": "自己补一句"}))).body)["comment"]
    assert mine["author"] == "user" and mine["side"] == "hers"
    assert "已留言" in await server.letter_comment(lid, "辛苦了，早点睡")
    assert "找不到" in await server.letter_comment("nope", "x")
    comments = (await bm.get(lid))["metadata"]["comments"]
    assert [(c["author"], c["text"]) for c in comments] == [("user", "自己补一句"), ("祁煜", "辛苦了，早点睡")]
    out = await server.letter_read()
    assert "[留言]" in out and "- 她 · " in out and "祁煜 · " in out and "辛苦了，早点睡" in out
    day = json.loads((await server.api_diary(_LetterReq(method="GET"))).body)["days"][0]
    assert [c["side"] for c in day["hers"][0]["comments"]] == ["hers", "his"]


@pytest.mark.asyncio
async def test_her_letters_under_either_signature(srv, monkeypatch):
    server, bm = srv
    monkeypatch.setenv("AI_NAME", "祁煜")
    await server.letter_write(author="汤圆", content="他替我存的", title="日记 · 2026-09-25", date="2026-09-25")
    await server.api_letters(_LetterReq(body={"author": "user", "content": "页面写的", "title": "日记 · 2026-09-26", "date": "2026-09-26"}))
    await server.letter_write(author="ai", content="他的", title="日记 · 2026-09-26", date="2026-09-26")
    days = json.loads((await server.api_diary(_LetterReq(method="GET"))).body)["days"]
    assert {d["date"]: (len(d["his"]), len(d["hers"])) for d in days} == {"2026-09-26": (1, 1), "2026-09-25": (0, 1)}
    out = await server.letter_read(author="汤圆")
    assert "他替我存的" in out and "页面写的" in out and "他的" not in out
