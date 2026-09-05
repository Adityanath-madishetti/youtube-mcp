"""Tool 3: keyword search restricted to the user's subscribed channels."""

from __future__ import annotations

import asyncio

from fastmcp import FastMCP

from ..formatting import video_summary
from ..youtube import cutoff_for_days, parse_published_at
from .common import get_subscriptions, get_uploads_playlists, youtube_session

QUOTA_BUDGET = 400
CONCURRENCY = 8

# Deepest slice of each channel's uploads we will read (in items). Bounds the
# cost of a wide `days` window on prolific channels.
MAX_ITEMS_PER_CHANNEL = 100


def _score(query_terms: list[str], title: str, description: str) -> float:
    """Rank a video against the query.

    Title matches count far more than description matches, and a video matching
    every term outranks one matching a single term. Cheap by design -- the point
    is to avoid search.list's 100-unit cost, not to rebuild a search engine.
    """
    title_lower = title.lower()
    description_lower = description.lower()

    matched = 0
    score = 0.0
    for term in query_terms:
        in_title = term in title_lower
        in_description = term in description_lower
        if in_title:
            score += 3.0
        if in_description:
            score += 1.0
        if in_title or in_description:
            matched += 1

    if matched == 0:
        return 0.0
    # Reward covering more of the query.
    return score * (matched / len(query_terms))


def register(mcp: FastMCP) -> None:
    @mcp.tool
    async def search_my_subscriptions(
        query: str,
        days: int = 90,
        max_channels: int = 30,
        max_results: int = 20,
    ) -> dict:
        """Search recent videos from ONLY the channels this user subscribes to.

        Answers questions like "did anyone I follow cover the new iPhone?" or
        "find that video about sourdough from a channel I watch". Matches on
        title and description; results are ranked by relevance.

        This scans the user's subscription feed rather than searching all of
        YouTube, so it finds things from creators they already trust.

        Args:
            query: Words to look for in video titles and descriptions.
            days: Only consider videos published within this many days (1-365).
            max_channels: Subscribed channels to scan (1-60).
            max_results: Most videos to return (1-50).
        """
        query = query.strip()
        if not query:
            return {"error": "Provide a search query.", "results": []}

        days = max(1, min(days, 365))
        max_channels = max(1, min(max_channels, 60))
        max_results = max(1, min(max_results, 50))
        query_terms = [t for t in query.lower().split() if t]

        async with youtube_session(QUOTA_BUDGET) as (client, ledger, user_id):
            subscriptions = await get_subscriptions(client, user_id, max_channels)
            if not subscriptions:
                return {
                    "results": [],
                    "query": query,
                    "summary": "This account has no YouTube subscriptions to search.",
                    "quota_units_used": ledger.spent,
                }

            channel_ids = [s["channel_id"] for s in subscriptions]
            uploads = await get_uploads_playlists(client, user_id, channel_ids)
            cutoff = cutoff_for_days(days)
            semaphore = asyncio.Semaphore(CONCURRENCY)

            async def scan_channel(channel_id: str) -> list[str]:
                playlist_id = uploads.get(channel_id)
                if not playlist_id:
                    return []
                video_ids: list[str] = []
                async with semaphore:
                    try:
                        async for item in client.paginate(
                            "playlistItems",
                            max_items=MAX_ITEMS_PER_CHANNEL,
                            part="contentDetails",
                            playlistId=playlist_id,
                        ):
                            details = item.get("contentDetails", {})
                            published = parse_published_at(details.get("videoPublishedAt"))
                            if published is None:
                                continue
                            # Newest-first ordering: past the cutoff, stop paging.
                            if published < cutoff:
                                break
                            if details.get("videoId"):
                                video_ids.append(details["videoId"])
                    except Exception:
                        return []
                return video_ids

            scanned = await asyncio.gather(*(scan_channel(cid) for cid in channel_ids))
            candidate_ids = [vid for ids in scanned for vid in ids]

            if not candidate_ids:
                return {
                    "results": [],
                    "query": query,
                    "channels_scanned": len(channel_ids),
                    "summary": (
                        f"No videos from your subscriptions in the last {days} days "
                        "to search. Try a longer time window."
                    ),
                    "quota_units_used": ledger.spent,
                }

            metadata = await client.videos_by_id(
                candidate_ids, part="snippet,contentDetails,statistics"
            )

            scored: list[tuple[float, dict]] = []
            for video in metadata.values():
                snippet = video.get("snippet", {})
                score = _score(
                    query_terms,
                    snippet.get("title", ""),
                    snippet.get("description", ""),
                )
                if score > 0:
                    scored.append((score, video))

            scored.sort(
                key=lambda pair: (
                    pair[0],
                    pair[1].get("snippet", {}).get("publishedAt", ""),
                ),
                reverse=True,
            )
            results = [video_summary(video) for _, video in scored[:max_results]]

            return {
                "results": results,
                "query": query,
                "days": days,
                "channels_scanned": len(channel_ids),
                "videos_searched": len(metadata),
                "match_count": len(scored),
                "summary": (
                    f"Found {len(scored)} match(es) for '{query}' across "
                    f"{len(metadata)} recent videos from {len(channel_ids)} "
                    f"subscribed channels."
                ),
                "quota_units_used": ledger.spent,
            }
