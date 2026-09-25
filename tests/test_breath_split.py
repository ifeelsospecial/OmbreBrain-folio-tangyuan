# ============================================================
# breath 拆分: breath(0 参数公布) / breath_search / breath_advanced
# 走真实的 FastMCP 调用路径(schema 校验 + 参数模型), 不直接调函数。
# ============================================================
import pytest

from bucket_manager import ACCESS_LEVEL


@pytest.fixture
def srv(bucket_mgr, decay_eng, monkeypatch):
    bm, de = bucket_mgr, decay_eng
    import server
    monkeypatch.setattr(server, "bucket_mgr", bm)
    monkeypatch.setattr(server, "decay_engine", de)
    return server, bm


def _text(result) -> str:
    content = result[0] if isinstance(result, tuple) else result
    return "".join(getattr(c, "text", "") for c in content)


async def _feel_and_ordinary(bm):
    await bm.create(content="今天修好了 feel 通道，心里很踏实。", tags=[], importance=5,
                    domain=[], valence=0.8, arousal=0.4, name=None, bucket_type="feel")
    await bm.create(content="普通动态记忆：记得买牛奶。", tags=[], importance=5,
                    domain=["杂事"], valence=0.5, arousal=0.3, name="买牛奶", bucket_type="dynamic")


@pytest.mark.asyncio
async def test_advertised_schemas(srv):
    server, _ = srv
    tools = {t.name: t for t in await server.mcp.list_tools()}
    assert tools["breath"].inputSchema.get("properties") == {}
    assert tools["breath_search"].inputSchema.get("required") == ["query"]
    adv = tools["breath_advanced"].inputSchema["properties"]
    assert {"query", "domain", "valence", "arousal", "max_tokens", "max_results"} <= set(adv)


@pytest.mark.asyncio
async def test_breath_advanced_reads_feel(srv):
    server, bm = srv
    await _feel_and_ordinary(bm)
    out = _text(await server.mcp.call_tool("breath_advanced", {"domain": "feel"}))
    assert "=== 你留下的 feel ===" in out
    assert "feel 通道" in out
    assert "买牛奶" not in out


@pytest.mark.asyncio
async def test_breath_still_honours_legacy_args(srv):
    """缓存了旧 schema 的客户端仍发 domain/query: 不能被静默丢掉变成默认浮现。"""
    server, bm = srv
    await _feel_and_ordinary(bm)
    out = _text(await server.mcp.call_tool("breath", {"query": "", "domain": "feel"}))
    assert "=== 你留下的 feel ===" in out


@pytest.mark.asyncio
async def test_breath_rejects_unknown_args(srv):
    server, _ = srv
    with pytest.raises(Exception):
        await server.mcp.call_tool("breath", {"qurey": "伦敦"})


@pytest.mark.asyncio
async def test_breath_search_requires_query(srv):
    server, _ = srv
    out = _text(await server.mcp.call_tool("breath_search", {"query": "  "}))
    assert "需要 query" in out


@pytest.mark.asyncio
async def test_split_tools_keep_private_level_filter(srv):
    """新入口复用同一实现, 受限通道仍看不到 level-2 私密 feel。"""
    server, bm = srv
    admin = ACCESS_LEVEL.set(2)
    try:
        await bm.create(content="公开的 feel", tags=[], importance=5, domain=[],
                        valence=0.5, arousal=0.3, name=None, bucket_type="feel", level=1)
        await bm.create(content="私密的 feel", tags=[], importance=5, domain=[],
                        valence=0.5, arousal=0.3, name=None, bucket_type="feel", level=2)
        assert "私密的 feel" in _text(await server.mcp.call_tool("breath_advanced", {"domain": "feel"}))
    finally:
        ACCESS_LEVEL.reset(admin)

    restricted = ACCESS_LEVEL.set(1)
    try:
        out = _text(await server.mcp.call_tool("breath_advanced", {"domain": "feel"}))
    finally:
        ACCESS_LEVEL.reset(restricted)
    assert "公开的 feel" in out
    assert "私密的 feel" not in out
