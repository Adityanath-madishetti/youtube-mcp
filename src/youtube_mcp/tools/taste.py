"""Tool 2: infer the user's taste from subscriptions and liked videos."""

from __future__ import annotations

from collections import Counter

from fastmcp import FastMCP

from ..cache import cache
from ..formatting import category_name, video_summary
from .common import get_liked_playlist_id, get_subscriptions, youtube_session

QUOTA_BUDGET = 60


def register(mcp: FastMCP) -> None:
    @mcp.tool
    async def get_taste_profile(
        max_liked_videos: int = 100,
        max_channels: int = 100,
    ) -> dict:
        """Summarize what this user actually likes on YouTube.

        Builds a profile from their subscriptions and liked videos: top content
        categories, the channels they like most often, and representative
        channels they follow.

        Call this BEFORE recommending videos or channels, so suggestions match
        the user's real interests instead of generic popular content.

        Args:
            max_liked_videos: Recent liked videos to analyze (1-200).
            max_channels: Subscribed channels to include (1-200).
        """
        max_liked_videos = max(1, min(max_liked_videos, 200))
        max_channels = max(1, min(max_channels, 200))

        async with youtube_session(QUOTA_BUDGET) as (client, ledger, user_id):
            cache_key = f"{max_liked_videos}:{max_channels}"
            cached = await cache.get(user_id, "taste_profile", cache_key)
            if cached is not None:
                return {**cached, "quota_units_used": 0, "cached": True}

            subscriptions = await get_subscriptions(client, user_id, max_channels)

            # Liked videos live in the 'LL' playlist. This can be absent (no
            # channel) or 404 (likes set to private) -- both are normal, so fall
            # back to a subscriptions-only profile rather than failing.
            liked_videos: list[dict] = []
            likes_available = True
            liked_playlist_id = await get_liked_playlist_id(client)

            if liked_playlist_id:
                try:
                    liked_ids = [
                        item["contentDetails"]["videoId"]
                        async for item in client.paginate(
                            "playlistItems",
                            max_items=max_liked_videos,
                            part="contentDetails",
                            playlistId=liked_playlist_id,
                        )
                        if item.get("contentDetails", {}).get("videoId")
                    ]
                    if liked_ids:
                        metadata = await client.videos_by_id(
                            liked_ids, part="snippet,contentDetails,statistics"
                        )
                        liked_videos = [
                            metadata[vid] for vid in liked_ids if vid in metadata
                        ]
                except Exception:
                    likes_available = False
            else:
                likes_available = False

            # Categories come only from liked videos: a like is an explicit
            # signal of taste, whereas a subscription may be years stale.
            category_counts = Counter(
                category_name(v.get("snippet", {}).get("categoryId"))
                for v in liked_videos
            )
            liked_channel_counts = Counter(
                v.get("snippet", {}).get("channelTitle")
                for v in liked_videos
                if v.get("snippet", {}).get("channelTitle")
            )

            top_categories = [
                {"category": name, "liked_videos": count}
                for name, count in category_counts.most_common(8)
            ]
            top_liked_channels = [
                {"channel": name, "liked_videos": count}
                for name, count in liked_channel_counts.most_common(15)
            ]

            # Channels the user likes from repeatedly but hasn't subscribed to --
            # a useful signal that generic recommendations would miss.
            subscribed_titles = {s["title"] for s in subscriptions}
            unsubscribed_favourites = [
                entry["channel"]
                for entry in top_liked_channels
                if entry["channel"] not in subscribed_titles and entry["liked_videos"] > 1
            ]

            summary_bits = [f"Subscribed to {len(subscriptions)} channel(s)."]
            if liked_videos:
                summary_bits.append(f"Analyzed {len(liked_videos)} liked video(s).")
                if top_categories:
                    leaders = ", ".join(c["category"] for c in top_categories[:3])
                    summary_bits.append(f"Strongest interests: {leaders}.")
            elif not likes_available:
                summary_bits.append(
                    "Liked videos are private or unavailable, so this profile "
                    "is based on subscriptions alone."
                )

            profile = {
                "top_categories": top_categories,
                "most_liked_channels": top_liked_channels,
                "subscribed_channels": [s["title"] for s in subscriptions[:50]],
                "subscription_count": len(subscriptions),
                "liked_videos_analyzed": len(liked_videos),
                "liked_but_not_subscribed": unsubscribed_favourites,
                "recent_likes": [
                    video_summary(v, include_description=False)
                    for v in liked_videos[:10]
                ],
                "summary": " ".join(summary_bits),
            }

            await cache.set(user_id, "taste_profile", profile, cache_key)
            return {**profile, "quota_units_used": ledger.spent, "cached": False}
