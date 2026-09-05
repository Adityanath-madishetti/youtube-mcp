"""Tool registration."""

from fastmcp import FastMCP

from . import digest, library, search, taste


def register_tools(mcp: FastMCP) -> None:
    """Attach every tool to the server."""
    digest.register(mcp)
    taste.register(mcp)
    search.register(mcp)
    library.register(mcp)
