#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/logs"
LOCK_DIR="$ROOT/.secrets/sync.lock"
mkdir -p "$LOG_DIR" "$ROOT/.secrets"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "$(date -Is) music sync already running; exiting"
  exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT

cd "$ROOT"
uv run sync_music.py sync >> "$LOG_DIR/music-sync.log" 2>&1
