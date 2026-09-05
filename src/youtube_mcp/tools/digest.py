"""Tool 1: what's new from the channels the user subscribes to."""

from __future__ import annotations

import asyncio

from fastmcp import FastMCP

from ..formatting import is_short, video_summary
from ..youtube import cutoff_for_days, parse_published_at
from .common import get_subscriptions, get_uploads_playlists, youtube_session

# Budget generously above the ~46 units this costs at defaults, so that a large
# max_channels still completes, while a runaway can't drain the daily allowance.
QUOTA_BUDGET = 400

# Cap concurrent playlist reads. The API tolerates this comfortably and it turns
# a 40-channel serial fan-out from ~20s into ~2s.
CONCURRENCY = 8


def register(mcp: FastMCP) -> None:
    @mcp.tool
    async def whats_new_from_subscriptions(
        days: int = 7,
        max_channels: int = 40,
        max_videos_per_channel: int = 3,
        include_shorts: bool = False,
    ) -> dict:
        """Get recent uploads from the channels this user subscribes to.

        Use this for "what's new on YouTube", "anything good to watch", or
        catching up after time away. Results are grouped by channel, newest
        first. Channels with new activity are scanned first.

        Args:
            days: How far back to look, in days (1-30).
            max_channels: How many subscribed channels to scan (1-100). Lower
                this if the call reports quota pressure.
            max_videos_per_channel: Most videos to keep per channel (1-10).
            include_shorts: Include videos of 60 seconds or less.
        """
        days = max(1, min(days, 30))
        max_channels = max(1, min(max_channels, 100))
        max_videos_per_channel = max(1, min(max_videos_per_channel, 10))

        async with youtube_session(QUOTA_BUDGET) as (client, ledger, user_id):
            subscriptions = await get_subscriptions(client, user_id, max_channels)
            if not subscriptions:
                return {
                    "videos_by_channel": [],
                    "summary": "This account has no YouTube subscriptions.",
                    "quota_units_used": ledger.spent,
                }

            channel_ids = [s["channel_id"] for s in subscriptions]
            uploads = await get_uploads_playlists(client, user_id, channel_ids)
            cutoff = cutoff_for_days(days)

            # Over-fetch per channel: the uploads playlist is newest-first, so we
            # read a slightly deeper slice, then filter by date and Shorts. Each
            # page is 1 unit regardless of how many items it holds.
            page_size = min(50, max(max_videos_per_channel * 3, 10))
            semaphore = asyncio.Semaphore(CONCURRENCY)

            async def recent_for_channel(channel_id: str) -> tuple[str, list[str]]:
                playlist_id = uploads.get(channel_id)
                if not playlist_id:
                    return channel_id, []
                video_ids: list[str] = []
                async with semaphore:
                    try:
                        async for item in client.paginate(
                            "playlistItems",
                            max_items=page_size,
                            part="contentDetails",
                            playlistId=playlist_id,
                        ):
                            details = item.get("contentDetails", {})
                            published = parse_published_at(details.get("videoPublishedAt"))
                            # Playlist is newest-first, so the first item older
                            # than the cutoff means the rest are too.
                            if published is None:
                                continue
                            if published < cutoff:
                                break
                            if details.get("videoId"):
                                video_ids.append(details["videoId"])
                    except Exception:
                        # One dead channel (terminated, or an empty uploads
                        # playlist) must not sink the whole digest.
                        return channel_id, []
                return channel_id, video_ids

            results = await asyncio.gather(
                *(recent_for_channel(cid) for cid in channel_ids)
            )

            candidate_ids = [vid for _, ids in results for vid in ids]
            if not candidate_ids:
                return {
                    "videos_by_channel": [],
                    "days": days,
                    "channels_scanned": len(channel_ids),
                    "summary": f"No new uploads from your subscriptions in the last {days} days.",
                    "quota_units_used": ledger.spent,
                }

            # One batched call per 50 videos gets duration (for the Shorts
            # filter) plus stats and category in the same unit.
            metadata = await client.videos_by_id(
                candidate_ids, part="snippet,contentDetails,statistics"
            )

            titles = {s["channel_id"]: s["title"] for s in subscriptions}
            grouped: list[dict] = []
            total_videos = 0

            for channel_id, video_ids in results:
                videos = []
                for video_id in video_ids:
                    # Look up by id: deleted/private videos are missing from the
                    # response, so positional alignment would be wrong.
                    video = metadata.get(video_id)
                    if video is None:
                        continue
                    if not include_shorts and is_short(video):
                        continue
                    videos.append(video_summary(video))
                    if len(videos) >= max_videos_per_channel:
                        break

                if videos:
                    grouped.append(
                        {
                            "channel": titles.get(channel_id, "Unknown"),
                            "channel_id": channel_id,
                            "videos": videos,
                        }
                    )
                    total_videos += len(videos)

            # Most-recently-active channels first.
            grouped.sort(
                key=lambda g: g["videos"][0].get("published_at") or "", reverse=True
            )

            return {
                "videos_by_channel": grouped,
                "days": days,
                "channels_scanned": len(channel_ids),
                "channels_with_new_videos": len(grouped),
                "total_videos": total_videos,
                "shorts_included": include_shorts,
                "summary": (
                    f"{total_videos} new video(s) from {len(grouped)} of "
                    f"{len(channel_ids)} channels in the last {days} days."
                ),
                "quota_units_used": ledger.spent,
            }
