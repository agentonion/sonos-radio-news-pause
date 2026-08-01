#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PLIST_SRC="$ROOT/com.user.sonos-news-pause.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.user.sonos-news-pause.plist"
VENV="$ROOT/.venv"

mkdir -p "$ROOT/logs" "$HOME/Library/LaunchAgents"

if [[ ! -x "$VENV/bin/python" ]]; then
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -r "$ROOT/requirements.txt"
fi

sed "s|PROJECT_DIR|$ROOT|g" "$PLIST_SRC" > "$PLIST_DST"

launchctl bootout "gui/$(id -u)/com.user.sonos-news-pause" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
launchctl enable "gui/$(id -u)/com.user.sonos-news-pause"
launchctl kickstart -k "gui/$(id -u)/com.user.sonos-news-pause"

echo "Installed and started: com.user.sonos-news-pause"
echo "Logs: $ROOT/logs/sonos-news-pause.log"
echo "Stop with: ./uninstall_launchd.sh"
echo "Or: launchctl bootout \"gui/$(id -u)/com.user.sonos-news-pause\""
