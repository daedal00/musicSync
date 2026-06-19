#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "spotipy>=2.24.0",
#   "ytmusicapi>=1.12.1",
# ]
# ///
"""Sync one playlist between Spotify and YouTube Music.

Use `uv run sync_music.py --help` for commands.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import sys
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Literal
from urllib.parse import parse_qs, urlparse

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from ytmusicapi import YTMusic, setup as setup_ytmusic
from ytmusicapi.exceptions import YTMusicUserError

Direction = Literal["spotify_to_ytmusic", "ytmusic_to_spotify", "bidirectional"]

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config.json"
DEFAULT_SECRETS_DIR = ROOT / ".secrets"
DEFAULT_YTMUSIC_AUTH = DEFAULT_SECRETS_DIR / "ytmusic_browser.json"
DEFAULT_SPOTIFY_CACHE = DEFAULT_SECRETS_DIR / "spotify_token.cache"

SPOTIFY_SCOPES = "playlist-read-private playlist-read-collaborative playlist-modify-private playlist-modify-public"


@dataclass(frozen=True)
class Track:
    title: str
    artists: tuple[str, ...]
    duration_seconds: int | None = None
    spotify_uri: str | None = None
    spotify_id: str | None = None
    ytmusic_video_id: str | None = None
    ytmusic_set_video_id: str | None = None

    @property
    def first_artist(self) -> str:
        return self.artists[0] if self.artists else ""

    @property
    def key(self) -> str:
        return f"{normalize(self.title)}|{normalize(self.first_artist)}"

    @property
    def query(self) -> str:
        artist = self.first_artist
        return f"{self.title} {artist}".strip()

    @property
    def label(self) -> str:
        artist = ", ".join(self.artists) if self.artists else "Unknown artist"
        return f"{artist} - {self.title}"


@dataclass(frozen=True)
class Config:
    direction: Direction
    spotify_playlist_id: str
    youtube_music_playlist_id: str
    spotify_client_id: str
    spotify_client_secret: str
    spotify_redirect_uri: str
    ytmusic_auth_file: Path
    spotify_cache_file: Path
    match_threshold: float
    delete_missing: bool
    batch_size: int


def normalize(value: str) -> str:
    value = value.lower()
    value = re.sub(r"\(.*?(remaster|version|edit|explicit|clean|feat\.?|ft\.).*?\)", " ", value)
    value = re.sub(r"\[.*?(remaster|version|edit|explicit|clean|feat\.?|ft\.).*?\]", " ", value)
    value = re.sub(r"\s*[-–—]\s*(remaster(ed)?|version|edit|explicit|clean)\b.*$", " ", value)
    value = re.sub(r"\b(feat|ft)\.?\b.*$", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def youtube_music_playlist_id(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        playlist_id = parse_qs(parsed.query).get("list", [""])[0]
        if not playlist_id:
            raise SystemExit("YouTube Music playlist URL must include a list= playlist ID.")
        return playlist_id
    return value.strip()


def spotify_playlist_id(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[-2] == "playlist":
            return parts[-1]
    return value.strip()


def load_config(path: Path) -> Config:
    if not path.exists():
        raise SystemExit(f"Missing config: {path}\nRun: cp config.example.json config.json, then edit it.")

    raw = json.loads(path.read_text())
    secrets_dir = Path(raw.get("secrets_dir", DEFAULT_SECRETS_DIR)).expanduser()
    if not secrets_dir.is_absolute():
        secrets_dir = ROOT / secrets_dir

    direction = raw.get("direction", "bidirectional")
    if direction not in {"spotify_to_ytmusic", "ytmusic_to_spotify", "bidirectional"}:
        raise SystemExit("config.direction must be spotify_to_ytmusic, ytmusic_to_spotify, or bidirectional")

    cfg = Config(
        direction=direction,
        spotify_playlist_id=spotify_playlist_id(required(raw, "spotify_playlist_id")),
        youtube_music_playlist_id=youtube_music_playlist_id(required(raw, "youtube_music_playlist_id")),
        spotify_client_id=os.getenv("SPOTIFY_CLIENT_ID", raw.get("spotify_client_id", "")),
        spotify_client_secret=os.getenv("SPOTIFY_CLIENT_SECRET", raw.get("spotify_client_secret", "")),
        spotify_redirect_uri=raw.get("spotify_redirect_uri", "http://127.0.0.1:8888/callback"),
        ytmusic_auth_file=Path(raw.get("ytmusic_auth_file", secrets_dir / "ytmusic_browser.json")).expanduser(),
        spotify_cache_file=Path(raw.get("spotify_cache_file", secrets_dir / "spotify_token.cache")).expanduser(),
        match_threshold=float(raw.get("match_threshold", 0.72)),
        delete_missing=bool(raw.get("delete_missing", False)),
        batch_size=int(raw.get("batch_size", 50)),
    )

    if not cfg.spotify_client_id or not cfg.spotify_client_secret:
        raise SystemExit("Set spotify_client_id and spotify_client_secret in config.json or environment variables.")
    if cfg.delete_missing and cfg.direction == "bidirectional":
        raise SystemExit("delete_missing is only supported for one-way directions, not bidirectional.")
    if not 0.0 <= cfg.match_threshold <= 1.0:
        raise SystemExit("match_threshold must be between 0 and 1.")
    if cfg.batch_size < 1 or cfg.batch_size > 100:
        raise SystemExit("batch_size must be between 1 and 100.")
    return cfg


def required(raw: dict[str, Any], key: str) -> str:
    value = str(raw.get(key, "")).strip()
    if not value or value.startswith("REPLACE_"):
        raise SystemExit(f"Set {key} in config.json")
    return value


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def spotify_client(cfg: Config) -> spotipy.Spotify:
    ensure_parent(cfg.spotify_cache_file)
    auth = SpotifyOAuth(
        client_id=cfg.spotify_client_id,
        client_secret=cfg.spotify_client_secret,
        redirect_uri=cfg.spotify_redirect_uri,
        scope=SPOTIFY_SCOPES,
        cache_path=str(cfg.spotify_cache_file),
        open_browser=True,
    )
    return spotipy.Spotify(auth_manager=auth, requests_timeout=30, retries=3)


def ytmusic_client(cfg: Config) -> YTMusic:
    if not cfg.ytmusic_auth_file.exists():
        raise SystemExit(f"Missing YouTube Music auth file: {cfg.ytmusic_auth_file}\nRun: uv run sync_music.py setup-ytmusic")
    return YTMusic(str(cfg.ytmusic_auth_file))


def spotify_playlist_tracks(sp: spotipy.Spotify, playlist_id: str) -> list[Track]:
    tracks: list[Track] = []
    fields = "items(track(id,uri,name,artists(name),duration_ms,is_local,type)),next"
    page = sp.playlist_items(playlist_id, fields=fields, limit=100, additional_types=("track",))
    while page:
        for item in page.get("items", []):
            track = item.get("track") or {}
            if track.get("type") != "track" or track.get("is_local") or not track.get("uri"):
                continue
            tracks.append(
                Track(
                    title=track.get("name", ""),
                    artists=tuple(a.get("name", "") for a in track.get("artists", []) if a.get("name")),
                    duration_seconds=round((track.get("duration_ms") or 0) / 1000) or None,
                    spotify_uri=track.get("uri"),
                    spotify_id=track.get("id"),
                )
            )
        page = sp.next(page) if page.get("next") else None
    return tracks


def ytmusic_playlist_tracks(yt: YTMusic, playlist_id: str) -> list[Track]:
    data = yt.get_playlist(playlist_id, limit=None)
    tracks: list[Track] = []
    for item in data.get("tracks", []):
        video_id = item.get("videoId")
        if not video_id:
            continue
        artists = tuple(a.get("name", "") for a in item.get("artists", []) if a.get("name"))
        tracks.append(
            Track(
                title=item.get("title", ""),
                artists=artists,
                duration_seconds=item.get("duration_seconds"),
                ytmusic_video_id=video_id,
                ytmusic_set_video_id=item.get("setVideoId"),
            )
        )
    return tracks


def score_match(source: Track, candidate: Track) -> float:
    source_text = normalize(f"{source.title} {source.first_artist}")
    candidate_text = normalize(f"{candidate.title} {candidate.first_artist}")
    text_score = SequenceMatcher(None, source_text, candidate_text).ratio()

    if source.duration_seconds and candidate.duration_seconds:
        diff = abs(source.duration_seconds - candidate.duration_seconds)
        duration_score = max(0.0, 1.0 - (diff / 45.0))
        return (text_score * 0.82) + (duration_score * 0.18)
    return text_score


def best_match(source: Track, candidates: Iterable[Track], threshold: float) -> Track | None:
    scored = [(score_match(source, candidate), candidate) for candidate in candidates]
    if not scored:
        return None
    score, candidate = max(scored, key=lambda item: item[0])
    return candidate if score >= threshold else None


def search_spotify(sp: spotipy.Spotify, source: Track, threshold: float) -> Track | None:
    results = sp.search(q=source.query, type="track", limit=5)
    candidates: list[Track] = []
    for track in results.get("tracks", {}).get("items", []):
        candidates.append(
            Track(
                title=track.get("name", ""),
                artists=tuple(a.get("name", "") for a in track.get("artists", []) if a.get("name")),
                duration_seconds=round((track.get("duration_ms") or 0) / 1000) or None,
                spotify_uri=track.get("uri"),
                spotify_id=track.get("id"),
            )
        )
    return best_match(source, candidates, threshold)


def search_ytmusic(yt: YTMusic, source: Track, threshold: float) -> Track | None:
    results = yt.search(source.query, filter="songs", limit=5)
    candidates: list[Track] = []
    for item in results:
        video_id = item.get("videoId")
        if not video_id:
            continue
        artists = tuple(a.get("name", "") for a in item.get("artists", []) if a.get("name"))
        candidates.append(
            Track(
                title=item.get("title", ""),
                artists=artists,
                duration_seconds=item.get("duration_seconds"),
                ytmusic_video_id=video_id,
            )
        )
    return best_match(source, candidates, threshold)


def chunked(items: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def sync_spotify_to_ytmusic(cfg: Config, sp: spotipy.Spotify, yt: YTMusic, dry_run: bool) -> None:
    spotify_tracks = spotify_playlist_tracks(sp, cfg.spotify_playlist_id)
    ytmusic_tracks = ytmusic_playlist_tracks(yt, cfg.youtube_music_playlist_id)
    ytmusic_by_key = {track.key: track for track in ytmusic_tracks}

    to_add: list[str] = []
    missing: list[Track] = []
    for source in spotify_tracks:
        existing = ytmusic_by_key.get(source.key) or best_match(source, ytmusic_tracks, cfg.match_threshold)
        if existing:
            continue
        match = search_ytmusic(yt, source, cfg.match_threshold)
        if match and match.ytmusic_video_id:
            to_add.append(match.ytmusic_video_id)
            print(f"ADD to YouTube Music: {source.label} -> {match.label}")
        else:
            missing.append(source)
            print(f"NO MATCH on YouTube Music: {source.label}")
        time.sleep(0.15)

    spotify_keys = {track.key for track in spotify_tracks}
    remove_from_yt = [
        track for track in ytmusic_tracks if track.key not in spotify_keys and not best_match(track, spotify_tracks, cfg.match_threshold)
    ] if cfg.delete_missing else []
    unique_to_add = list(dict.fromkeys(to_add))
    removable_yt = [
        {"videoId": t.ytmusic_video_id, "setVideoId": t.ytmusic_set_video_id}
        for t in remove_from_yt
        if t.ytmusic_video_id and t.ytmusic_set_video_id
    ]

    if dry_run:
        print(f"DRY RUN: would add {len(unique_to_add)} track(s) to YouTube Music and remove {len(removable_yt)}.")
    else:
        for batch in chunked(unique_to_add, cfg.batch_size):
            yt.add_playlist_items(cfg.youtube_music_playlist_id, batch, duplicates=False)
        if removable_yt:
            yt.remove_playlist_items(cfg.youtube_music_playlist_id, removable_yt)
    print_summary("Spotify -> YouTube Music", len(spotify_tracks), len(ytmusic_tracks), len(unique_to_add), len(removable_yt), missing)


def sync_ytmusic_to_spotify(cfg: Config, sp: spotipy.Spotify, yt: YTMusic, dry_run: bool) -> None:
    ytmusic_tracks = ytmusic_playlist_tracks(yt, cfg.youtube_music_playlist_id)
    spotify_tracks = spotify_playlist_tracks(sp, cfg.spotify_playlist_id)
    spotify_by_key = {track.key: track for track in spotify_tracks}

    to_add: list[str] = []
    missing: list[Track] = []
    for source in ytmusic_tracks:
        existing = spotify_by_key.get(source.key) or best_match(source, spotify_tracks, cfg.match_threshold)
        if existing:
            continue
        match = search_spotify(sp, source, cfg.match_threshold)
        if match and match.spotify_uri:
            to_add.append(match.spotify_uri)
            print(f"ADD to Spotify: {source.label} -> {match.label}")
        else:
            missing.append(source)
            print(f"NO MATCH on Spotify: {source.label}")
        time.sleep(0.15)

    ytmusic_keys = {track.key for track in ytmusic_tracks}
    remove_from_spotify = [
        track for track in spotify_tracks if track.key not in ytmusic_keys and not best_match(track, ytmusic_tracks, cfg.match_threshold)
    ] if cfg.delete_missing else []
    unique_to_add = list(dict.fromkeys(to_add))
    removable_spotify = [t.spotify_uri for t in remove_from_spotify if t.spotify_uri]

    if dry_run:
        print(f"DRY RUN: would add {len(unique_to_add)} track(s) to Spotify and remove {len(removable_spotify)}.")
    else:
        for batch in chunked(unique_to_add, cfg.batch_size):
            sp.playlist_add_items(cfg.spotify_playlist_id, batch)
        if removable_spotify:
            sp.playlist_remove_all_occurrences_of_items(cfg.spotify_playlist_id, removable_spotify)
    print_summary("YouTube Music -> Spotify", len(ytmusic_tracks), len(spotify_tracks), len(unique_to_add), len(removable_spotify), missing)


def print_summary(direction: str, source_count: int, dest_count: int, added: int, removed: int, missing: list[Track]) -> None:
    print(f"\n{direction} summary")
    print(f"  source tracks: {source_count}")
    print(f"  destination tracks before sync: {dest_count}")
    print(f"  added: {added}")
    print(f"  removed: {removed}")
    print(f"  unmatched: {len(missing)}")
    if missing:
        print("  unmatched tracks:")
        for track in missing[:25]:
            print(f"    - {track.label}")
        if len(missing) > 25:
            print(f"    ... and {len(missing) - 25} more")


def cmd_init_config(args: argparse.Namespace) -> None:
    target = args.config
    if target.exists() and not args.force:
        raise SystemExit(f"Refusing to overwrite existing {target}. Use --force if needed.")
    shutil.copyfile(ROOT / "config.example.json", target)
    print(f"Created {target}. Edit it with your playlist IDs and Spotify app credentials.")


def cmd_setup_spotify(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    user = spotify_client(cfg).current_user()
    print(f"Spotify auth OK: {user.get('display_name') or user.get('id')}")
    print(f"Token cache: {cfg.spotify_cache_file}")


def extract_headers_from_curl(raw: str) -> str:
    """Accept Chrome's "Copy as cURL" output in addition to raw request headers."""
    if "curl " not in raw or " -H " not in raw:
        return raw

    try:
        parts = shlex.split(raw)
    except ValueError:
        return raw

    headers: list[str] = []
    for index, part in enumerate(parts):
        if part in {"-H", "--header"} and index + 1 < len(parts):
            headers.append(parts[index + 1])
        elif part.startswith("-H") and len(part) > 2:
            headers.append(part[2:].strip())
    return "\n".join(headers) if headers else raw


def ytmusic_setup_help() -> str:
    return (
        "YouTube Music setup needs request headers from a logged-in music.youtube.com /browse request.\n"
        "In Chrome/Edge: open music.youtube.com -> DevTools -> Network -> reload -> filter for 'browse' ->\n"
        "right-click a request like /youtubei/v1/browse -> Copy -> Copy request headers.\n"
        "The copied text must include lines starting with 'cookie:' and 'x-goog-authuser:'.\n"
        "You can also use Copy -> Copy as cURL; this script will extract the -H headers."
    )


def cmd_setup_ytmusic(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    ensure_parent(cfg.ytmusic_auth_file)
    headers_raw = ""
    if args.headers_file:
        headers_raw = Path(args.headers_file).read_text()
    elif not sys.stdin.isatty():
        headers_raw = sys.stdin.read()
    else:
        print(ytmusic_setup_help())
        print("Paste headers here, then press Ctrl-D (or use: pbpaste | uv run sync_music.py setup-ytmusic).")
        headers_raw = sys.stdin.read()

    headers_raw = extract_headers_from_curl(headers_raw)
    if not headers_raw.strip():
        raise SystemExit("No YouTube Music headers received.")
    try:
        setup_ytmusic(filepath=str(cfg.ytmusic_auth_file), headers_raw=headers_raw)
    except YTMusicUserError as exc:
        raise SystemExit(f"{exc}\n\n{ytmusic_setup_help()}") from exc
    print(f"YouTube Music auth written to {cfg.ytmusic_auth_file}")


def cmd_sync(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    sp = spotify_client(cfg)
    yt = ytmusic_client(cfg)

    if cfg.direction in {"spotify_to_ytmusic", "bidirectional"}:
        sync_spotify_to_ytmusic(cfg, sp, yt, args.dry_run)
    if cfg.direction in {"ytmusic_to_spotify", "bidirectional"}:
        sync_ytmusic_to_spotify(cfg, sp, yt, args.dry_run)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync one Spotify playlist with one YouTube Music playlist.")
    parser.add_argument("--config", type=Path, default=Path(os.getenv("MUSICSYNC_CONFIG", DEFAULT_CONFIG)))
    sub = parser.add_subparsers(dest="command", required=True)

    init_config = sub.add_parser("init-config", help="Copy config.example.json to config.json")
    init_config.add_argument("--force", action="store_true")
    init_config.set_defaults(func=cmd_init_config)

    setup_spotify = sub.add_parser("setup-spotify", help="Open Spotify OAuth flow and cache a token")
    setup_spotify.set_defaults(func=cmd_setup_spotify)

    setup_yt = sub.add_parser("setup-ytmusic", help="Create YouTube Music browser auth file from copied request headers")
    setup_yt.add_argument("--headers-file", type=Path, help="File containing raw YouTube Music request headers")
    setup_yt.set_defaults(func=cmd_setup_ytmusic)

    sync = sub.add_parser("sync", help="Run the configured playlist sync")
    sync.add_argument("--dry-run", action="store_true", help="Print changes without modifying either playlist")
    sync.set_defaults(func=cmd_sync)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
