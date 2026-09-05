# YouTube Personal — MCP Server

A remote [MCP](https://modelcontextprotocol.io) server that lets Claude read **your** personal
YouTube data. Add it once as a custom connector, log in with Google, and Claude can answer
questions like *"what's new worth watching?"* or *"recommend something based on my taste"*.

## Who supplies what

| Thing | Who | How |
|---|---|---|
| **YouTube data** (subscriptions, likes, playlists) | **The end user** | Clicks *Connect* in Claude → Google login → done. **No configuration on their side.** |
| **Google OAuth app credentials** | **You**, once | One Google Cloud project. Server-side env vars. Users never see these. |

End users never create a Google Cloud project and never paste an API key. They log in — that's it.

## Tools

| Tool | What it does | Approx. quota cost |
|---|---|---|
| `whats_new_from_subscriptions` | Digest of recent uploads from channels you follow, grouped by channel | ~46 units |
| `get_taste_profile` | Your taste summarized from subscriptions + liked videos | ~7 units |
| `search_my_subscriptions` | Keyword search across **only** the channels you follow | ~67 units |
| `explore_my_library` | Browse liked videos and your playlists | 1–5 units |

## What this deliberately cannot do

**Watch history is not available.** YouTube removed `watchHistory` and `watchLater` from the Data
API in August 2016 (they return empty), the `activities` endpoint is deprecated, and there is no
history OAuth scope. No YouTube API integration can read your watch history — that is a platform
limitation, not an oversight here. Personalization is built from **subscriptions, liked videos,
and playlists**, which are all fully available.

---

## Setup

### 1. Google Cloud Console (one time, by you)

1. Create or select a project at [console.cloud.google.com](https://console.cloud.google.com).
2. **APIs & Services → Library →** search *YouTube Data API v3* → **Enable**.
3. **OAuth consent screen →** User type **External**. Fill in app name, user support email,
   developer contact.
4. **Add scopes** — these three:
   - `openid`
   - `https://www.googleapis.com/auth/userinfo.email`
   - `https://www.googleapis.com/auth/youtube.readonly` ← console flags this **Sensitive**
5. **Test users →** add each Google account that will use the server.
   **Hard cap: 100 users** while unverified, and their sessions expire after **7 days**,
   forcing re-login. See [Going past 100 users](#going-past-100-users).
6. **Credentials → Create Credentials → OAuth client ID → Web application.**
7. **Authorized redirect URIs →** add exactly one:
   ```
   https://<your-host>/auth/callback
   ```
   This must equal `BASE_URL` + `/auth/callback` **character for character** — no trailing slash.
8. Copy the client ID (`...apps.googleusercontent.com`) and secret (`GOCSPX-...`).

### 2. Generate server keys

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"                                # JWT_SIGNING_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # STORAGE_ENCRYPTION_KEY
```

Keep both stable. Losing `STORAGE_ENCRYPTION_KEY` forces every user to re-authenticate.

### 3. Deploy to Render

The included `render.yaml` defines the service and a **persistent disk at `/data`**. The disk is
not optional: OAuth client registrations and encrypted refresh tokens live there, and on an
ephemeral filesystem every user is silently logged out on each redeploy.

1. Push this repo to GitHub, then **New → Blueprint** on Render and point it at the repo.
2. Set the secret env vars in the Render dashboard (marked `sync: false` in `render.yaml`):
   `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `JWT_SIGNING_KEY`, `STORAGE_ENCRYPTION_KEY`.
3. Set `BASE_URL` to your service URL, e.g. `https://youtube-mcp.onrender.com` (no trailing slash).
4. Go back to Google Cloud and make sure the redirect URI matches that host exactly.

> **Keep this at one instance.** The in-process cache and disk-backed token store assume a single
> replica. Scaling out requires moving `client_storage` to Redis.

### 4. Connect from Claude

**Settings → Connectors → Add custom connector**, URL:

```
https://<your-host>/mcp
```

Claude walks you through the Google login. Then just ask it what's new on YouTube.

---

## Local development

```bash
uv sync
cp .env.example .env      # fill in the values
```

For a local OAuth round-trip, add `http://localhost:8000/auth/callback` as a redirect URI in
Google Cloud, set `BASE_URL=http://localhost:8000` and `STORAGE_PATH=./data/oauth` in `.env`,
then:

```bash
set -a; source .env; set +a
export PYTHONPATH=src
.venv/bin/python -m youtube_mcp.server
```

> `uv run youtube-mcp` should also work, but on some `uv`/Python 3.14 combinations the editable
> install's `.pth` file isn't picked up reliably, causing `ModuleNotFoundError: youtube_mcp`. The
> command above (invoking the module directly, with `src` on `PYTHONPATH`) always works and is
> the one used in development. If `uv run youtube-mcp` fails for you too, use this instead.

In a second terminal:

```bash
npx @modelcontextprotocol/inspector    # connect to http://localhost:8000/mcp
```

Inspector will open a browser window; connecting triggers the Google login and consent screen.
Once connected, call any of the 4 tools directly to see results from your real YouTube account.

Run the tests (no network or credentials needed):

```bash
uv run pytest
```

---

## Quota

The YouTube Data API gives **10,000 units/day, shared across all users of your project** — it is
not per-user. Most `list` calls cost 1 unit; `search.list` costs **100**, which is why this server
never uses it and instead walks each channel's uploads playlist (1 unit) and batches
`videos.list` 50 IDs at a time.

Practical ceiling: **~200 digest calls/day**, roughly **20–40 active users**. Every tool returns
`quota_units_used` so cost is visible. If you outgrow this, request an increase via the
[YouTube API Services Audit and Quota Extension Form](https://support.google.com/youtube/contact/yt_api_form).

## Going past 100 users

While your Google app is unverified you are capped at 100 manually-added test users, and their
refresh tokens expire weekly. Lifting that requires submitting for **OAuth verification**: a
privacy policy, a homepage on a domain you've verified in Search Console, and a demo video of the
consent flow. Google's review typically takes **several weeks**. Fine for personal or small-group
use as-is; plan ahead if you intend to launch publicly.

## Privacy

The server stores only OAuth tokens, encrypted at rest with Fernet, on its own disk. YouTube data
is fetched live per request, held in an in-memory cache for at most a few hours, and never written
to disk. Tokens are never logged.
