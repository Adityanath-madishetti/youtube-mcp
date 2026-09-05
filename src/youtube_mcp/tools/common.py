"""Shared plumbing for the tools: client lifecycle and subscription fan-out."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import httpx2

from ..cache import cache
from ..youtube import QuotaLedger, YouTubeClient, current_user_id


@asynccontextmanager
async def youtube_session(budget: int) -> AsyncIterator[tuple[YouTubeClient, QuotaLedger, str]]:
    """Open an HTTP client, ledger, and resolve the caller for one tool call."""
    ledger = QuotaLedger(budget)
    user_id = current_user_id()
    async with httpx2.AsyncClient(timeout=20.0) as http:
        yield YouTubeClient(http, ledger), ledger, user_id


async def get_subscriptions(
    client: YouTubeClient, user_id: str, max_channels: int
) -> list[dict]:
    """The user's subscribed channels, newest subscription first (cached 6h).

    `order=unread` surfaces channels with new activity first, which is what makes
    a truncated scan (max_channels < total subscriptions) pick the channels most
    likely to have something new rather than an arbitrary slice.
    """
    cached = await cache.get(user_id, "subscriptions")
    if cached is not None:
        return cached[:max_channels]

    subscriptions: list[dict] = []
    async for item in client.paginate(
        "subscriptions",
        max_items=max_channels,
        part="snippet",
        mine="true",
        order="unread",
    ):
        snippet = item.get("snippet", {})
        channel_id = snippet.get("resourceId", {}).get("channelId")
        if channel_id:
            subscriptions.append(
                {"channel_id": channel_id, "title": snippet.get("title", "")}
            )

    await cache.set(user_id, "subscriptions", subscriptions)
    return subscriptions


async def get_uploads_playlists(
    client: YouTubeClient, user_id: str, channel_ids: Sequence[str]
) -> dict[str, str]:
    """Map channel id -> uploads playlist id, batched 50 per unit and cached 30d.

    Reading a channel's uploads playlist with playlistItems.list costs 1 unit,
    versus 100 for search.list against the same channel. This mapping is what
    makes the digest and search tools affordable.
    """
    hits, misses = await cache.get_many(user_id, "uploads_playlists", list(channel_ids))
    if misses:
        channels = await client.channels_by_id(misses, part="contentDetails")
        fetched = {
            channel_id: resource["contentDetails"]["relatedPlaylists"]["uploads"]
            for channel_id, resource in channels.items()
            if resource.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
        }
        await cache.set_many(user_id, "uploads_playlists", fetched)
        hits.update(fetched)
    return hits


async def get_liked_playlist_id(client: YouTubeClient) -> str | None:
    """The user's liked-videos playlist id ('LL...'), or None if unavailable.

    Note `watchHistory` and `watchLater` were removed from this resource in 2016
    and `favorites` is deprecated; `likes` is the only personal playlist here
    that still carries real data.
    """
    channel = await client.my_channel(part="contentDetails")
    if channel is None:
        return None
    return channel.get("contentDetails", {}).get("relatedPlaylists", {}).get("likes")
