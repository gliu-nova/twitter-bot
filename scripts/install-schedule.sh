#!/bin/bash
# Local Mac scheduler only — skip if you run production via GitHub Actions.
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PLIST_SRC="$DIR/scripts/com.georgeliu.twitter-bot.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.georgeliu.twitter-bot.plist"
sed "s|REPLACE_PROJECT_DIR|$DIR|g" "$PLIST_SRC" > "$PLIST_DST"
chmod +x "$DIR/scripts/run-bot.sh"
launchctl bootout "gui/$(id -u)/com.georgeliu.twitter-bot" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
launchctl enable "gui/$(id -u)/com.georgeliu.twitter-bot"
echo "Installed 5-minute tick schedule: com.georgeliu.twitter-bot"
echo "Per-indicator poll rates are in config.yaml → scheduler:"
echo "Logs: $DIR/data/bot.log"