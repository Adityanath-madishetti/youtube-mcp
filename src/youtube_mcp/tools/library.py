"""Tool 4: browse the user's liked videos and their own playlists."""

from __future__ import annotations

from typing import Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from ..formatting import truncate, video_summary
from .common import get_liked_playlist_id, youtube_session

QUOTA_BUDGET = 40


def register(mcp: FastMCP) -> None:
    @mcp.tool
    async def explore_my_library(
        source: Literal["liked", "playlists", "playlist_items"] = "liked",
        playlist_id: str | None = None,
        max_results: int = 50,
    ) -> dict:
        """Browse this user's own liked videos and playlists.

        Use for "what have I liked recently", "what playlists do I have", or
        "what's in my Watch Later replacement playlist".

        Args:
            source: "liked" for recently liked videos, "playlists" to list the
                user's playlists, "playlist_items" for the contents of one
                playlist.
            playlist_id: The playlist to read. Required when
                source="playlist_items"; get ids from source="playlists".
            max_results: Most items to return (1-200).
        """
        max_results = max(1, min(max_results, 200))

        async with youtube_session(QUOTA_BUDGET) as (client, ledger, user_id):
            if source == "playlists":
                playlists = []
                async for item in client.paginate(
                    "playlists",
                    max_items=max_results,
                    part="snippet,contentDetails",
                    mine="true",
                ):
                    snippet = item.get("snippet", {})
                    playlists.append(
                        {
                            "playlist_id": item.get("id"),
                            "title": snippet.get("title"),
                            "description": truncate(snippet.get("description"), 150),
                            "item_count": item.get("contentDetails", {}).get("itemCount"),
                            "published_at": snippet.get("publishedAt"),
                        }
                    )
                return {
                    "source": "playlists",
                    "playlists": playlists,
                    "summary": f"{len(playlists)} playlist(s).",
                    "quota_units_used": ledger.spent,
                }

            if source == "liked":
                target_playlist = await get_liked_playlist_id(client)
                if not target_playlist:
                    return {
                        "source": "liked",
                        "videos": [],
                        "summary": (
                            "No liked-videos playlist is available for this "
                            "account. It may be private, or the account may have "
                            "no YouTube channel."
                        ),
                        "quota_units_used": ledger.spent,
                    }
            else:  # playlist_items
                if not playlist_id:
                    raise ToolError(
                        "playlist_id is required when source='playlist_items'. "
                        "Call this tool with source='playlists' to list ids."
                    )
                target_playlist = playlist_id

            try:
                video_ids = [
                    item["contentDetails"]["videoId"]
                    async for item in client.paginate(
                        "playlistItems",
                        max_items=max_results,
                        part="contentDetails",
                        playlistId=target_playlist,
                    )
                    if item.get("contentDetails", {}).get("videoId")
                ]
            except ToolError:
                if source == "liked":
                    # A 404 here normally means the user's likes are private.
                    return {
                        "source": "liked",
                        "videos": [],
                        "summary": (
                            "Could not read liked videos -- the playlist is "
                            "likely set to private in YouTube settings."
                        ),
                        "quota_units_used": ledger.spent,
                    }
                raise

            if not video_ids:
                return {
                    "source": source,
                    "playlist_id": target_playlist,
                    "videos": [],
                    "summary": "That playlist is empty.",
                    "quota_units_used": ledger.spent,
                }

            metadata = await client.videos_by_id(
                video_ids, part="snippet,contentDetails,statistics"
            )
            # Preserve playlist order; skip ids that came back missing because the
            # video was deleted or made private.
            videos = [
                video_summary(metadata[vid], include_description=False)
                for vid in video_ids
                if vid in metadata
            ]
            unavailable = len(video_ids) - len(videos)

            summary = f"{len(videos)} video(s)"
            summary += " from your liked videos." if source == "liked" else " in that playlist."
            if unavailable:
                summary += f" {unavailable} more are deleted or private."

            return {
                "source": source,
                "playlist_id": target_playlist,
                "videos": videos,
                "unavailable_count": unavailable,
                "summary": summary,
                "quota_units_used": ledger.spent,
            }
