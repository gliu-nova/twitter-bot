#!/bin/bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PLIST_SRC="$DIR/scripts/com.georgeliu.twitter-bot.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.georgeliu.twitter-bot.plist"
sed "s|REPLACE_PROJECT_DIR|$DIR|g" "$PLIST_SRC" > "$PLIST_DST"
chmod +x "$DIR/scripts/run-bot.sh"
launchctl bootout "gui/$(id -u)/com.georgeliu.twitter-bot" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
launchctl enable "gui/$(id -u)/com.georgeliu.twitter-bot"
echo "Installed hourly schedule: com.georgeliu.twitter-bot"
echo "Logs: $DIR/data/bot.log"