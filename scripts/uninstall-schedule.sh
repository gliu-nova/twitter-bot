#!/bin/bash
# Disable local launchd scheduler (production uses GitHub Actions only).
set -euo pipefail
LABEL="com.georgeliu.twitter-bot"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "Disabled local scheduler: $LABEL"