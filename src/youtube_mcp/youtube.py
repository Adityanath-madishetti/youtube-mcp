"""Async YouTube Data API v3 client with explicit quota accounting.

We talk to the REST API directly rather than using google-api-python-client
because that library is synchronous (it would need thread-offloading in an async
server), expects `google.oauth2.credentials.Credentials` objects when all we hold
is a bare token string, and fetches discovery documents at startup. We need six
endpoints; this is simpler and lets us account for quota exactly.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta

import httpx2
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token

API_ROOT = "https://www.googleapis.com/youtube/v3"

# Quota cost per endpoint. Every `list` method costs 1 unit except search, which
# costs 100 -- a 100x cliff that dictates the entire design of this server: we
# never call search, and instead walk each channel's uploads playlist.
ENDPOINT_COSTS = {"search": 100}

# The API rejects more than 50 ids per batched request.
MAX_IDS_PER_REQUEST = 50
MAX_RESULTS_PER_PAGE = 50


class QuotaExceeded(ToolError):
    """Raised when a single tool call would exceed its own unit budget."""


class QuotaLedger:
    """Tracks units spent within one tool invocation.

    Serves two purposes: it stops a runaway fan-out from burning the project's
    whole daily allowance on one call, and it lets each tool report its cost back
    to the model so the trade-off is visible rather than hidden.
    """

    def __init__(self, budget: int) -> None:
        self.budget = budget
        self.spent = 0

    def charge(self, endpoint: str) -> None:
        cost = ENDPOINT_COSTS.get(endpoint, 1)
        if self.spent + cost > self.budget:
            raise QuotaExceeded(
                f"This request would exceed its quota budget "
                f"({self.spent + cost} > {self.budget} units). "
                "Retry with a shorter time window or fewer channels."
            )
        self.spent += cost


def current_google_token() -> str:
    """Return the caller's raw Google OAuth access token.

    With GoogleProvider, the AccessToken handed to tools carries the *upstream*
    Google token, not the FastMCP JWT: OAuthProxy.load_access_token decrypts the
    stored upstream token set and verifies that, and GoogleTokenVerifier builds
    AccessToken(token=<upstream token>, ...). FastMCP refreshes it transparently
    beforehand, so it is always live.
    """
    token = get_access_token()
    if token is None or not token.token:
        raise ToolError(
            "Not authenticated with Google. Reconnect the YouTube connector."
        )
    return token.token


def current_user_id() -> str:
    """Stable Google user id ('sub'), used to key the per-user cache.

    Never key a cache on the token string itself -- it rotates on every refresh,
    which would silently reduce the cache to a no-op.
    """
    token = get_access_token()
    subject = None
    if token is not None:
        subject = token.claims.get("sub") or token.subject
    if not subject:
        raise ToolError("Could not identify the authenticated Google user.")
    return str(subject)


def _error_reason(response: httpx2.Response) -> str:
    """Pull Google's machine-readable error reason out of a failed response."""
    try:
        errors = response.json().get("error", {}).get("errors", [])
        if errors:
            return str(errors[0].get("reason", "unknown"))
    except Exception:
        pass
    return "unknown"


class YouTubeClient:
    """Thin authenticated wrapper over the endpoints this server needs."""

    def __init__(self, http: httpx2.AsyncClient, ledger: QuotaLedger) -> None:
        self._http = http
        self._ledger = ledger
        self._headers = {"Authorization": f"Bearer {current_google_token()}"}

    async def list(self, endpoint: str, **params: object) -> dict:
        """One `list` call against `endpoint`, charged to the ledger."""
        self._ledger.charge(endpoint)
        response = await self._http.get(
            f"{API_ROOT}/{endpoint}", params=params, headers=self._headers
        )

        if response.status_code == 401:
            raise ToolError(
                "Google rejected the access token. Reconnect the YouTube connector."
            )
        if response.status_code == 403:
            # Quota exhaustion and permission problems both surface as 403 but
            # need completely different advice, so branch on the reason.
            reason = _error_reason(response)
            if reason in {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}:
                raise ToolError(
                    "The YouTube API daily quota for this server is exhausted. "
                    "It resets at midnight Pacific Time."
                )
            raise ToolError(f"YouTube denied the request (reason: {reason}).")
        if response.status_code == 404:
            raise ToolError(f"YouTube resource not found (endpoint: {endpoint}).")

        response.raise_for_status()
        return response.json()

    async def paginate(
        self, endpoint: str, max_items: int, **params: object
    ) -> AsyncIterator[dict]:
        """Yield up to `max_items` items, charging quota per page fetched."""
        page_token: str | None = None
        fetched = 0

        while fetched < max_items:
            page_params = dict(params)
            page_params["maxResults"] = min(MAX_RESULTS_PER_PAGE, max_items - fetched)
            if page_token:
                page_params["pageToken"] = page_token

            payload = await self.list(endpoint, **page_params)
            items = payload.get("items", [])
            if not items:
                return

            for item in items:
                yield item
                fetched += 1
                if fetched >= max_items:
                    return

            page_token = payload.get("nextPageToken")
            if not page_token:
                return

    async def videos_by_id(self, video_ids: Sequence[str], part: str) -> dict[str, dict]:
        """Fetch metadata for many videos, batched 50 ids per unit of quota.

        Batching here is the single biggest quota lever in the server: 200 videos
        cost 4 units this way versus 200 one at a time.

        Returns a dict keyed by video id. Videos that were deleted or made private
        are simply absent -- callers must look results up by id rather than
        assuming the response lines up positionally with the request.
        """
        results: dict[str, dict] = {}
        unique_ids = list(dict.fromkeys(video_ids))  # de-dupe, keep order

        for start in range(0, len(unique_ids), MAX_IDS_PER_REQUEST):
            chunk = unique_ids[start : start + MAX_IDS_PER_REQUEST]
            payload = await self.list(
                "videos", part=part, id=",".join(chunk), maxResults=MAX_IDS_PER_REQUEST
            )
            for item in payload.get("items", []):
                results[item["id"]] = item

        return results

    async def channels_by_id(self, channel_ids: Sequence[str], part: str) -> dict[str, dict]:
        """Fetch channel resources, batched 50 ids per unit of quota."""
        results: dict[str, dict] = {}
        unique_ids = list(dict.fromkeys(channel_ids))

        for start in range(0, len(unique_ids), MAX_IDS_PER_REQUEST):
            chunk = unique_ids[start : start + MAX_IDS_PER_REQUEST]
            payload = await self.list(
                "channels", part=part, id=",".join(chunk), maxResults=MAX_IDS_PER_REQUEST
            )
            for item in payload.get("items", []):
                results[item["id"]] = item

        return results

    async def my_channel(self, part: str) -> dict | None:
        """The authenticated user's own channel resource, or None if they lack one."""
        payload = await self.list("channels", part=part, mine="true")
        items = payload.get("items", [])
        return items[0] if items else None


# --- helpers -----------------------------------------------------------------

_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?T?(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?$"
)


def parse_duration_seconds(iso_duration: str | None) -> int | None:
    """Convert an ISO-8601 duration ('PT1M30S') to seconds.

    YouTube reports video length this way; we need seconds to filter out Shorts.
    Returns None for live streams and anything unparseable, so callers can tell
    "unknown length" apart from "zero seconds".
    """
    if not iso_duration:
        return None
    match = _DURATION_RE.match(iso_duration)
    if not match:
        return None

    parts = {k: int(v) for k, v in match.groupdict(default="0").items()}
    return (
        parts["days"] * 86400
        + parts["hours"] * 3600
        + parts["minutes"] * 60
        + parts["seconds"]
    )


def parse_published_at(value: str | None) -> datetime | None:
    """Parse an RFC-3339 timestamp from the API into an aware datetime."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def cutoff_for_days(days: int) -> datetime:
    """UTC timestamp `days` in the past, for time-windowing results."""
    return datetime.now(UTC) - timedelta(days=days)
