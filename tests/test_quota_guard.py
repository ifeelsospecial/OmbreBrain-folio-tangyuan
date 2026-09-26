# ============================================================
# 批量 AI 任务撞额度(429)时: 如实计错、连续几次就提前停, 没处理的不被误标
# ============================================================
import pytest

import utils
from utils import QuotaGuard, is_quota_error


class _Quota(Exception):
    pass


QUOTA = _Quota("Error code: 429 - You exceeded your current quota")


class _FakeBuckets:
    def __init__(self, n, **meta):
        self.items = [{"id": f"b{i}", "content": f"去了伦敦第{i}天", "metadata": {"type": "dynamic", **meta}}
                      for i in range(n)]
        self.writes = []

    async def list_all(self, include_archive=False):
        return self.items

    async def rewrite_bucket_metadata(self, bid, fn):
        self.writes.append(bid)
        return True


class _Always429:
    api_available = True
    model = "x"
    calls = 0

    async def analyze(self, content):
        self.calls += 1
        raise QUOTA

    @property
    def client(self):
        outer = self

        class _C:
            class chat:
                class completions:
                    @staticmethod
                    async def create(**kw):
                        outer.calls += 1
                        raise QUOTA
        return _C


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch):
    orig = QuotaGuard.__init__

    def fast(self, progress, stop_after=3, cooldown_s=60.0):
        orig(self, progress, stop_after, 0)
    monkeypatch.setattr(QuotaGuard, "__init__", fast)


def test_is_quota_error():
    assert is_quota_error(QUOTA)
    assert is_quota_error(RuntimeError("RESOURCE_EXHAUSTED"))
    assert not is_quota_error(ValueError("bad json"))


@pytest.mark.asyncio
async def test_guard_resets_on_success():
    p = {}
    g = QuotaGuard(p)
    assert not await g.failed(QUOTA)
    assert not await g.failed(QUOTA)
    g.ok()
    assert not await g.failed(QUOTA)
    assert not await g.failed(QUOTA)
    assert await g.failed(QUOTA)
    assert "额度用完" in p["stopped"]


@pytest.mark.asyncio
async def test_places_backfill_counts_and_stops():
    from places import backfill_places
    bm, dh, p = _FakeBuckets(20, domain=["出行"]), _Always429(), {}
    await backfill_places(bm, dh, p, pause_s=0, limit=100)
    assert p["errors"] == 3 and p["processed"] == 3      # 以前: errors=0, 20 条全跑
    assert dh.calls == 3
    assert p["stopped"] and not p["running"]
    assert bm.writes == []                               # 没被误标成"看过、没有地点"


@pytest.mark.asyncio
async def test_reclassify_stops():
    from reclassify import reclassify_uncategorized
    bm, dh, p = _FakeBuckets(20, domain=["未分类"]), _Always429(), {}
    await reclassify_uncategorized(bm, dh, p, pause_s=0, limit=100)
    assert p["errors"] == 3 and dh.calls == 3 and p["stopped"]
    assert bm.writes == []


@pytest.mark.asyncio
async def test_about_draft_stops_without_merge(monkeypatch, tmp_path):
    import about as about_mod
    from about import AboutStore, draft_from_memories
    calls = []

    async def ask(dehydrator, prompt, text):
        calls.append(prompt)
        raise QUOTA
    monkeypatch.setattr(about_mod, "_ask_json", ask)
    monkeypatch.setattr(about_mod, "DRAFT_BATCH", 1)
    bm, p = _FakeBuckets(10), {}
    await draft_from_memories(bm, _Always429(), AboutStore(str(tmp_path / "a")), p, pause_s=0)
    assert len(calls) == 3                               # 不再翻完 10 批再白白合并一次
    assert p["stopped"] and "额度" in p["last_error"] and not p["running"]
