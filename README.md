# YouTube Personal — MCP Server

A remote [MCP](https://modelcontextprotocol.io) server that lets Claude read **your own** YouTube
data — subscriptions, liked videos, and playlists — so you can ask things like *"what's new worth
watching?"* or *"recommend something based on my taste"* right inside a Claude conversation.

You connect with your own Google account. Nothing is shared between users, and no YouTube data is
ever stored on the server — it's fetched live for each request.

## Add it to Claude

**Settings → Connectors → Add custom connector**, and use this URL:

```
https://youtube-mcp-8bmw.onrender.com/mcp
```

Claude will walk you through a Google sign-in. Approve the requested (read-only) permissions and
you're connected — works the same way in Claude Desktop and on claude.ai.

> **This app is currently in Google's "Testing" mode**, which means only accounts I've explicitly
> added can sign in. If you'd like to try it, **[contact me](#contact)** and I'll add your Google
> account to the allowed list.

## What you can ask it

| Tool | What it does |
|---|---|
| `whats_new_from_subscriptions` | A digest of recent uploads from the channels you follow, grouped by channel |
| `get_taste_profile` | A summary of your taste, built from your subscriptions and liked videos |
| `search_my_subscriptions` | Keyword search across only the channels you follow |
| `explore_my_library` | Browse your liked videos and your own playlists |

Just ask Claude naturally — e.g. *"anything new from my subscriptions this week?"* or *"what kind
of stuff do I usually watch?"* — and it picks the right tool.

## What this can't do

YouTube's API does not expose **watch history** or **Watch Later** — Google removed both in 2016,
and there is no way for any third-party app to read them, this one included. Everything here is
built from what the API *does* still provide: subscriptions, liked videos, and playlists.

## Privacy

- Your YouTube data is fetched live from Google for each request and is **not** written to disk.
- Only OAuth tokens are stored (needed to keep you signed in), encrypted at rest.
- Nothing is shared across users — every connection only ever sees its own account's data.
- Source code is in this repository if you'd like to check for yourself.

## Contact

Questions, access requests, or feedback: **adityanath5002@gmail.com**
