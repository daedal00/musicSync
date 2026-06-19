#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCHEDULE="${1:-0 * * * *}"
UV_BIN="$(command -v uv || true)"

if [[ -z "$UV_BIN" ]]; then
  echo "uv is required. Install from https://docs.astral.sh/uv/ then re-run." >&2
  exit 1
fi

RUNNER="$ROOT/scripts/run_sync.sh"
chmod +x "$RUNNER" "$ROOT/sync_music.py"
mkdir -p "$ROOT/logs" "$ROOT/.secrets"

START_MARK="# musicSync start"
END_MARK="# musicSync end"
CRON_LINE="$SCHEDULE cd '$ROOT' && PATH='$(dirname "$UV_BIN")':\$PATH '$RUNNER'"
TMP="$(mktemp)"

crontab -l 2>/dev/null | awk "/$START_MARK/{skip=1; next} /$END_MARK/{skip=0; next} !skip{print}" > "$TMP"
{
  cat "$TMP"
  echo "$START_MARK"
  echo "$CRON_LINE"
  echo "$END_MARK"
} | crontab -
rm "$TMP"

echo "Installed cron job:"
echo "  $CRON_LINE"
echo "Logs: $ROOT/logs/music-sync.log"
