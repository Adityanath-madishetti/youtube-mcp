"""Per-user TTL cache."""

import time

import pytest

from youtube_mcp.cache import TTLCache


@pytest.fixture
def ttl_cache():
    return TTLCache()


async def test_set_then_get_roundtrips(ttl_cache):
    await ttl_cache.set("user-1", "subscriptions", ["a", "b"])
    assert await ttl_cache.get("user-1", "subscriptions") == ["a", "b"]


async def test_miss_returns_none(ttl_cache):
    assert await ttl_cache.get("user-1", "subscriptions") is None


async def test_users_are_isolated(ttl_cache):
    await ttl_cache.set("user-1", "subscriptions", ["a"])
    assert await ttl_cache.get("user-2", "subscriptions") is None


async def test_expired_entry_is_dropped(ttl_cache, monkeypatch):
    # Force an already-expired entry without sleeping in the test.
    await ttl_cache.set("user-1", "liked_videos", ["v1"])
    key = ("user-1", "liked_videos", "")
    expires_at, value = ttl_cache._entries[key]
    ttl_cache._entries[key] = (time.monotonic() - 1, value)  # already expired
    assert await ttl_cache.get("user-1", "liked_videos") is None
    assert key not in ttl_cache._entries  # swept on read


async def test_get_many_splits_hits_and_misses(ttl_cache):
    await ttl_cache.set("user-1", "uploads_playlists", "UUabc", key="chan-1")
    hits, misses = await ttl_cache.get_many(
        "user-1", "uploads_playlists", ["chan-1", "chan-2"]
    )
    assert hits == {"chan-1": "UUabc"}
    assert misses == ["chan-2"]


async def test_set_many_then_get_many_all_hit(ttl_cache):
    await ttl_cache.set_many(
        "user-1", "uploads_playlists", {"chan-1": "UU1", "chan-2": "UU2"}
    )
    hits, misses = await ttl_cache.get_many(
        "user-1", "uploads_playlists", ["chan-1", "chan-2"]
    )
    assert hits == {"chan-1": "UU1", "chan-2": "UU2"}
    assert misses == []
