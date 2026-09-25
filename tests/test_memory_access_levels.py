import pytest

from bucket_manager import ACCESS_LEVEL, _level_visible


def test_access_level_defaults_to_restricted():
    assert ACCESS_LEVEL.get() == 1


def test_malformed_level_fails_closed_for_restricted_channel():
    token = ACCESS_LEVEL.set(1)
    try:
        assert _level_visible({"level": "broken"}) is False
    finally:
        ACCESS_LEVEL.reset(token)


@pytest.mark.asyncio
async def test_level_can_be_updated_and_is_hidden_from_restricted_channel(bucket_mgr):
    admin_token = ACCESS_LEVEL.set(2)
    try:
        bucket_id = await bucket_mgr.create(content="private", level=1)
        assert await bucket_mgr.update(bucket_id, level=2)
        assert (await bucket_mgr.get(bucket_id))["metadata"]["level"] == 2
    finally:
        ACCESS_LEVEL.reset(admin_token)

    restricted_token = ACCESS_LEVEL.set(1)
    try:
        assert await bucket_mgr.get(bucket_id) is None
    finally:
        ACCESS_LEVEL.reset(restricted_token)


@pytest.mark.asyncio
async def test_active_cache_filled_at_level2_does_not_leak_to_restricted_channel(bucket_mgr):
    """回归: list_all 的活跃集缓存存的是不分级全量, 缓存命中路径也必须跑分级滤网。
    否则 admin(level 2) 先填满缓存后, URL-key 受限通道(level 1)会从缓存里读到私密桶。"""
    admin_token = ACCESS_LEVEL.set(2)
    try:
        public_id = await bucket_mgr.create(content="public memory", level=1)
        private_id = await bucket_mgr.create(content="private memory", level=2)
        admin_ids = {b["id"] for b in await bucket_mgr.list_all()}
        assert {public_id, private_id} <= admin_ids
        assert bucket_mgr._active_cache is not None  # 缓存已由 level-2 视野填满
    finally:
        ACCESS_LEVEL.reset(admin_token)

    restricted_token = ACCESS_LEVEL.set(1)
    try:
        restricted_ids = {b["id"] for b in await bucket_mgr.list_all()}
    finally:
        ACCESS_LEVEL.reset(restricted_token)
    assert public_id in restricted_ids
    assert private_id not in restricted_ids

    # 反向: 受限通道填的缓存不能让 admin 丢掉私密桶
    admin_token = ACCESS_LEVEL.set(2)
    try:
        assert private_id in {b["id"] for b in await bucket_mgr.list_all()}
    finally:
        ACCESS_LEVEL.reset(admin_token)
