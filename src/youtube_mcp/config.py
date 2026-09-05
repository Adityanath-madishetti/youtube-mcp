"""Environment configuration, validated once at startup."""

from __future__ import annotations

import os
from dataclasses import dataclass

# Scopes requested from Google.
#
# `youtube.readonly` is classified by Google as a SENSITIVE scope, which is what
# caps an unverified app at 100 test users (see README "Going past 100 users").
# `openid` + `userinfo.email` are what let GoogleProvider identify the user, which
# we need for per-user cache keying.
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/youtube.readonly",
]


@dataclass(frozen=True)
class Settings:
    google_client_id: str
    google_client_secret: str
    base_url: str
    jwt_signing_key: str
    storage_encryption_key: str
    storage_path: str = "/data/oauth"
    port: int = 8000
    cache_ttl_seconds: int = 900


def load_settings() -> Settings:
    """Read and validate configuration.

    Deliberately fails loudly at import time rather than on the first request:
    a server that starts up "fine" and then rejects every OAuth callback because
    BASE_URL had a trailing slash is far harder to diagnose.
    """

    def required(key: str) -> str:
        value = os.environ.get(key, "").strip()
        if not value:
            raise RuntimeError(
                f"Missing required environment variable: {key}. "
                "See .env.example for the full list."
            )
        return value

    # A trailing slash here would produce '<base>//auth/callback', which no longer
    # byte-matches the redirect URI registered in Google Cloud Console.
    base_url = required("BASE_URL").rstrip("/")

    return Settings(
        google_client_id=required("GOOGLE_CLIENT_ID"),
        google_client_secret=required("GOOGLE_CLIENT_SECRET"),
        base_url=base_url,
        jwt_signing_key=required("JWT_SIGNING_KEY"),
        storage_encryption_key=required("STORAGE_ENCRYPTION_KEY"),
        storage_path=os.environ.get("STORAGE_PATH", "/data/oauth"),
        port=int(os.environ.get("PORT", "8000")),
        cache_ttl_seconds=int(os.environ.get("CACHE_TTL_SECONDS", "900")),
    )
