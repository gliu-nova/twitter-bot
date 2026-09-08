#!/bin/bash
# Disable local launchd scheduler (production uses GitHub Actions only).
set -euo pipefail
LABEL="com.georgeliu.cross-asset-signal-engine"
OLD_LABEL="com.georgeliu.twitter-bot"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootout "gui/$(id -u)/$OLD_LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
rm -f "$HOME/Library/LaunchAgents/$OLD_LABEL.plist"
echo "Disabled local scheduler: $LABEL"