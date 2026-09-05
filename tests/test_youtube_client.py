"""YouTubeClient against a fake transport: pagination, batching, 403 branching."""

import json

import httpx2
import pytest
from fastmcp.exceptions import ToolError

from youtube_mcp.youtube import QuotaLedger, YouTubeClient


def make_client(handler, budget: int = 10_000) -> YouTubeClient:
    transport = httpx2.MockTransport(handler)
    http = httpx2.AsyncClient(transport=transport)
    ledger = QuotaLedger(budget)
    # Bypass current_google_token()/get_access_token(), which need a live MCP
    # request context that doesn't exist in a unit test.
    client = YouTubeClient.__new__(YouTubeClient)
    client._http = http
    client._ledger = ledger
    client._headers = {"Authorization": "Bearer fake-token-for-tests"}
    return client


async def test_list_charges_one_unit_by_default():
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"items": []})

    client = make_client(handler)
    await client.list("channels", part="snippet", mine="true")
    assert client._ledger.spent == 1


async def test_list_charges_one_hundred_for_search():
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"items": []})

    client = make_client(handler)
    await client.list("search", part="snippet", q="test")
    assert client._ledger.spent == 100


async def test_quota_exceeded_403_raises_friendly_error():
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            403,
            json={"error": {"errors": [{"reason": "quotaExceeded"}]}},
        )

    client = make_client(handler)
    with pytest.raises(ToolError, match="quota"):
        await client.list("videos", part="snippet", id="x")


async def test_permission_403_raises_different_error():
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            403,
            json={"error": {"errors": [{"reason": "forbidden"}]}},
        )

    client = make_client(handler)
    with pytest.raises(ToolError, match="denied"):
        await client.list("playlistItems", part="snippet", playlistId="x")


async def test_401_prompts_reconnect():
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(401, json={"error": "invalid_token"})

    client = make_client(handler)
    with pytest.raises(ToolError, match="Reconnect"):
        await client.list("channels", part="snippet", mine="true")


async def test_paginate_stops_at_max_items_not_page_boundary():
    """Requesting 3 items from a 50-item page must yield exactly 3."""
    call_count = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal call_count
        call_count += 1
        items = [{"id": str(i)} for i in range(50)]
        return httpx2.Response(200, json={"items": items})

    client = make_client(handler)
    results = [item async for item in client.paginate("playlistItems", max_items=3, part="snippet")]
    assert len(results) == 3
    assert call_count == 1  # didn't need a second page


async def test_paginate_follows_next_page_token():
    pages = [
        {"items": [{"id": "a"}, {"id": "b"}], "nextPageToken": "p2"},
        {"items": [{"id": "c"}]},
    ]
    seen_tokens = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        token = dict(request.url.params).get("pageToken")
        seen_tokens.append(token)
        return httpx2.Response(200, json=pages.pop(0))

    client = make_client(handler)
    results = [item async for item in client.paginate("playlistItems", max_items=10, part="snippet")]
    assert [r["id"] for r in results] == ["a", "b", "c"]
    assert seen_tokens == [None, "p2"]


async def test_paginate_stops_when_page_is_empty():
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"items": []})

    client = make_client(handler)
    results = [item async for item in client.paginate("playlistItems", max_items=10, part="snippet")]
    assert results == []


async def test_videos_by_id_batches_fifty_per_call():
    calls = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        ids = dict(request.url.params)["id"].split(",")
        calls.append(len(ids))
        return httpx2.Response(200, json={"items": [{"id": i} for i in ids]})

    client = make_client(handler)
    video_ids = [f"v{i}" for i in range(120)]
    result = await client.videos_by_id(video_ids, part="snippet")

    assert calls == [50, 50, 20]  # 120 ids -> 3 batched calls, not 120
    assert client._ledger.spent == 3  # one unit per call, not per id
    assert len(result) == 120


async def test_videos_by_id_deduplicates_ids():
    def handler(request: httpx2.Request) -> httpx2.Response:
        ids = dict(request.url.params)["id"].split(",")
        return httpx2.Response(200, json={"items": [{"id": i} for i in ids]})

    client = make_client(handler)
    result = await client.videos_by_id(["v1", "v1", "v2"], part="snippet")
    assert client._ledger.spent == 1
    assert set(result) == {"v1", "v2"}


async def test_videos_by_id_omits_deleted_videos():
    """A deleted/private video is silently absent from the API response --
    callers must look up by id, never assume positional alignment."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        # Simulate v2 having been deleted: only v1 and v3 come back.
        return httpx2.Response(200, json={"items": [{"id": "v1"}, {"id": "v3"}]})

    client = make_client(handler)
    result = await client.videos_by_id(["v1", "v2", "v3"], part="snippet")
    assert set(result) == {"v1", "v3"}
    assert "v2" not in result


async def test_my_channel_returns_none_when_absent():
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"items": []})

    client = make_client(handler)
    assert await client.my_channel(part="contentDetails") is None
