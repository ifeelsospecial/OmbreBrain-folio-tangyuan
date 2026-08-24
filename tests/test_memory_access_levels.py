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
