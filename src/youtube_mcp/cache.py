"""Per-user in-memory cache with per-key-kind TTLs.

Subscriptions change rarely and a channel's uploads-playlist id is effectively
immutable, so caching them collapses the recurring cost of the digest tool from
~46 units to ~44 and, more importantly, removes the multi-second fan-out on
repeat calls within a session.

Deliberately in-process: it assumes the single-replica deployment described in
the README. The interface is async so it can be swapped for Redis unchanged if
the server ever scales out.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

# How long each kind of cached value stays fresh, in seconds.
TTLS: dict[str, int] = {
    "subscriptions": 6 * 3600,
    "uploads_playlists": 30 * 24 * 3600,  # a channel's uploads id never changes
    "video_meta": 24 * 3600,
    "liked_videos": 30 * 60,
    "taste_profile": 3600,
}

DEFAULT_TTL = 900


class TTLCache:
    """Small dict-backed cache keyed by (user id, kind, sub-key)."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str, str], tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, user_id: str, kind: str, key: str = "") -> Any | None:
        async with self._lock:
            entry = self._entries.get((user_id, kind, key))
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at < time.monotonic():
                # Expired; drop it so the dict doesn't grow without bound.
                del self._entries[(user_id, kind, key)]
                return None
            return value

    async def set(self, user_id: str, kind: str, value: Any, key: str = "") -> None:
        ttl = TTLS.get(kind, DEFAULT_TTL)
        async with self._lock:
            self._entries[(user_id, kind, key)] = (time.monotonic() + ttl, value)

    async def get_many(
        self, user_id: str, kind: str, keys: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        """Split `keys` into cached hits and the misses the caller must fetch."""
        hits: dict[str, Any] = {}
        misses: list[str] = []
        for key in keys:
            value = await self.get(user_id, kind, key)
            if value is None:
                misses.append(key)
            else:
                hits[key] = value
        return hits, misses

    async def set_many(self, user_id: str, kind: str, values: dict[str, Any]) -> None:
        for key, value in values.items():
            await self.set(user_id, kind, value, key)

    async def clear_user(self, user_id: str) -> None:
        async with self._lock:
            for entry_key in [k for k in self._entries if k[0] == user_id]:
                del self._entries[entry_key]


# Process-wide cache shared by all tools.
cache = TTLCache()
