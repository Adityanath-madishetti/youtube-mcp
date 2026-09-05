"""Compact serialization of API resources for LLM consumption.

The raw API responses are deeply nested and carry a lot of fields (etags, five
thumbnail sizes, region restrictions) that cost tokens without helping the model
answer anything. These helpers flatten each resource to the fields that actually
inform a recommendation, and truncate free text.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .youtube import parse_duration_seconds, parse_published_at

# YouTube's numeric category ids. Used to describe taste in words rather than
# making the model guess what "20" means. Only the categories that appear on
# real consumer accounts are listed; anything else falls back to "Other".
CATEGORY_NAMES = {
    "1": "Film & Animation",
    "2": "Autos & Vehicles",
    "10": "Music",
    "15": "Pets & Animals",
    "17": "Sports",
    "19": "Travel & Events",
    "20": "Gaming",
    "22": "People & Blogs",
    "23": "Comedy",
    "24": "Entertainment",
    "25": "News & Politics",
    "26": "Howto & Style",
    "27": "Education",
    "28": "Science & Technology",
    "29": "Nonprofits & Activism",
}

DESCRIPTION_LIMIT = 300


def truncate(text: str | None, limit: int = DESCRIPTION_LIMIT) -> str:
    """Trim free text to `limit` characters on a word boundary."""
    if not text:
        return ""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit].rsplit(" ", 1)[0] + "..."


def humanize_duration(seconds: int | None) -> str:
    """Render a length as '12:34' / '1:02:03', or 'live/unknown' if absent."""
    if seconds is None:
        return "unknown"
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def relative_age(published: datetime | None) -> str:
    """Describe how long ago something was published, in coarse units."""
    if published is None:
        return "unknown"
    delta = datetime.now(UTC) - published
    days = delta.days
    if days <= 0:
        hours = delta.seconds // 3600
        return "just now" if hours < 1 else f"{hours}h ago"
    if days == 1:
        return "1 day ago"
    if days < 30:
        return f"{days} days ago"
    if days < 365:
        return f"{days // 30} months ago"
    return f"{days // 365} years ago"


def category_name(category_id: str | None) -> str:
    return CATEGORY_NAMES.get(str(category_id or ""), "Other")


def video_summary(video: dict, *, include_description: bool = True) -> dict:
    """Flatten a `videos.list` resource to the fields worth spending tokens on."""
    snippet = video.get("snippet", {})
    details = video.get("contentDetails", {})
    stats = video.get("statistics", {})

    published = parse_published_at(snippet.get("publishedAt"))
    duration = parse_duration_seconds(details.get("duration"))

    summary = {
        "video_id": video.get("id"),
        "title": snippet.get("title"),
        "channel": snippet.get("channelTitle"),
        "channel_id": snippet.get("channelId"),
        "published_at": snippet.get("publishedAt"),
        "published_relative": relative_age(published),
        "duration": humanize_duration(duration),
        "duration_seconds": duration,
        "category": category_name(snippet.get("categoryId")),
        "url": f"https://www.youtube.com/watch?v={video.get('id')}",
    }

    # Stats are absent when the uploader hides them; omit rather than report zero.
    if "viewCount" in stats:
        summary["views"] = int(stats["viewCount"])
    if "likeCount" in stats:
        summary["likes"] = int(stats["likeCount"])
    if include_description:
        summary["description"] = truncate(snippet.get("description"))

    return summary


def is_short(video: dict) -> bool:
    """Heuristic for a YouTube Short: at most 60 seconds.

    Duration is the only signal the Data API exposes -- there is no `isShort`
    field -- so a rare long-form video under a minute counts as a Short here.
    """
    duration = parse_duration_seconds(video.get("contentDetails", {}).get("duration"))
    return duration is not None and duration <= 60
