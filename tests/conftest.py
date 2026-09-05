"""Test fixtures: a fake YouTube API and a stubbed authenticated user.

Tests never touch the network or need Google credentials.
"""

from __future__ import annotations

import os

import pytest

# config.load_settings() runs at import of youtube_mcp.server, so the environment
# must be populated before any test imports it.
os.environ.setdefault("GOOGLE_CLIENT_ID", "test.apps.googleusercontent.com")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "GOCSPX-test")
os.environ.setdefault("BASE_URL", "http://localhost:8000")
os.environ.setdefault("JWT_SIGNING_KEY", "test-signing-key-long-enough")
os.environ.setdefault("STORAGE_PATH", "/tmp/ytmcp-tests")


@pytest.fixture(autouse=True)
def _fernet_key():
    from cryptography.fernet import Fernet

    os.environ.setdefault("STORAGE_ENCRYPTION_KEY", Fernet.generate_key().decode())


@pytest.fixture(autouse=True)
async def _clear_cache():
    """Keep the process-wide cache from leaking between tests."""
    from youtube_mcp.cache import cache

    cache._entries.clear()
    yield
    cache._entries.clear()
