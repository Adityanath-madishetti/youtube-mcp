"""FastMCP server exposing personal YouTube data over authenticated HTTP.

Auth model: each end user logs in with their *own* Google account. The Google
OAuth client credentials below identify this *server* to Google and are supplied
once by the operator -- users never see or configure them.

Claude's custom connectors require OAuth 2.1 with Dynamic Client Registration,
which Google does not support. GoogleProvider (a subclass of OAuthProxy) bridges
that: it presents a DCR-compliant interface to Claude while using this server's
single pre-registered Google client upstream.
"""

from __future__ import annotations

import logging

from cryptography.fernet import Fernet
from fastmcp import FastMCP
from fastmcp.server.auth.providers.google import GoogleProvider
from key_value.aio.stores.disk import DiskStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

from .config import SCOPES, load_settings
from .tools import register_tools

logger = logging.getLogger(__name__)

settings = load_settings()

# Persist OAuth state to disk, encrypted.
#
# Two things live here: the DCR client registrations Claude creates, and the
# upstream Google token sets. FastMCP defaults this to a platformdirs path, which
# on a container with an ephemeral filesystem is wiped on every deploy -- silently
# logging out every user. The README requires a mounted disk at STORAGE_PATH.
#
# The Fernet wrapper matters because these records contain Google refresh tokens,
# which must not sit at rest in plaintext.
client_storage = FernetEncryptionWrapper(
    key_value=DiskStore(directory=settings.storage_path),
    fernet=Fernet(settings.storage_encryption_key),
)

auth = GoogleProvider(
    client_id=settings.google_client_id,
    client_secret=settings.google_client_secret,
    base_url=settings.base_url,
    required_scopes=SCOPES,
    # Must byte-match the Authorized redirect URI registered in Google Cloud.
    redirect_path="/auth/callback",
    client_storage=client_storage,
    # Set explicitly rather than letting FastMCP derive it from the client
    # secret, so that rotating the Google secret doesn't invalidate every token.
    jwt_signing_key=settings.jwt_signing_key,
    # Refresh slightly early: the digest tool can run for several seconds, and a
    # token that expires mid-call would fail after the work was already done.
    token_expiry_threshold_seconds=120,
)

mcp = FastMCP(
    name="YouTube Personal",
    instructions=(
        "Access to the connected user's personal YouTube account: their "
        "subscriptions, liked videos, and playlists.\n\n"
        "Call get_taste_profile before making recommendations so suggestions "
        "reflect what this user actually watches.\n\n"
        "Note: YouTube's API does not expose watch history or Watch Later -- "
        "those were removed in 2016 and cannot be retrieved by any tool here. "
        "Use subscriptions, likes, and playlists instead."
    ),
    auth=auth,
)

register_tools(mcp)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting YouTube MCP server at %s/mcp", settings.base_url)
    mcp.run(transport="http", host="0.0.0.0", port=settings.port)


if __name__ == "__main__":
    main()
