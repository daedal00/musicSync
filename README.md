# musicSync

Small Python/uv script to keep one Spotify playlist and one YouTube Music playlist in sync.

Default behavior is **add-only bidirectional sync**: songs found on either playlist are searched on the other service and added when there is a good match. It does not delete anything unless you explicitly enable one-way `delete_missing` in `config.json`.

## Plan

1. Configure one Spotify playlist ID and one YouTube Music playlist ID.
2. Authenticate once:
   - Spotify uses browser OAuth and stores a local token cache.
   - YouTube Music reuses browser request headers via `ytmusicapi` and stores them locally.
3. Run `uv run sync_music.py sync --dry-run` to inspect proposed changes.
4. Run `uv run sync_music.py sync` manually.
5. Install the cron wrapper after the dry run looks correct.

## Requirements

- [`uv`](https://docs.astral.sh/uv/) installed and available on PATH.
- A Spotify developer app:
  1. Create an app at <https://developer.spotify.com/dashboard>.
  2. Add redirect URI: `http://127.0.0.1:8888/callback`.
  3. Copy the client ID and client secret.

`uv` downloads the Python dependencies from the inline metadata in `sync_music.py`, so no virtualenv setup is needed.

## Setup

```bash
uv run sync_music.py init-config
```

Edit `config.json`:

- `spotify_playlist_id`: the Spotify playlist ID or URL.
- `youtube_music_playlist_id`: the YouTube Music playlist ID or playlist URL. If using a URL like `https://music.youtube.com/playlist?list=PL...&jct=...`, the script extracts only the `list=` value.
- `spotify_client_id` / `spotify_client_secret`: from your Spotify developer app.
- `direction`: `bidirectional`, `spotify_to_ytmusic`, or `ytmusic_to_spotify`.

Authenticate Spotify:

```bash
uv run sync_music.py setup-spotify
```

Authenticate YouTube Music:

1. Open <https://music.youtube.com> in a browser where you are logged in.
2. Open DevTools → Network.
3. Click a request to `music.youtube.com/youtubei/v1/...`.
4. Pick a request like `/youtubei/v1/browse` while logged in.
5. Copy either **Copy request headers** or **Copy as cURL**. The copied text must include `cookie:` and `x-goog-authuser:`.
6. Pipe it into setup:

```bash
pbpaste | uv run sync_music.py setup-ytmusic
```

If you are not on macOS, save the copied headers to a file and run:

```bash
uv run sync_music.py setup-ytmusic --headers-file headers.txt
```

Local credentials are written under `.secrets/` and ignored by git.

## Run

Preview changes:

```bash
uv run sync_music.py sync --dry-run
```

Apply changes:

```bash
uv run sync_music.py sync
```

## Cron

Install an hourly cron job:

```bash
./scripts/install_cron.sh
```

Or choose a schedule, for example every 30 minutes:

```bash
./scripts/install_cron.sh "*/30 * * * *"
```

Logs go to `logs/music-sync.log`. The runner uses a simple lock directory so overlapping cron runs exit safely.

## Safety notes

- Keep `delete_missing: false` unless you are sure one service should be the source of truth.
- `delete_missing: true` is intentionally blocked for `bidirectional` mode.
- Matching is fuzzy. Always run `--dry-run` after changing playlist IDs or `match_threshold`.
- Do not commit `config.json` or `.secrets/`; both may contain credentials or tokens.
