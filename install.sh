#!/bin/sh
# Install cswap-chrome for the current user. Run from the repo clone; the clone stays the install.
set -eu

REPO=$(cd "$(dirname "$0")" && pwd)
LABEL=com.valachpatrik.cswap-chrome
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

chmod +x "$REPO/cswap_chrome.py"
mkdir -p "$HOME/.local/bin" "$HOME/.config/cswap-chrome" "$HOME/.local/state/cswap-chrome"
ln -sf "$REPO/cswap_chrome.py" "$HOME/.local/bin/cswap-chrome"

if [ ! -f "$HOME/.config/cswap-chrome/config.json" ]; then
  cp "$REPO/config.example.json" "$HOME/.config/cswap-chrome/config.json"
  echo "Edit $HOME/.config/cswap-chrome/config.json: set the Chrome profile folder for each account."
fi

sed -e "s#@REPO@#$REPO#g" -e "s#@HOME@#$HOME#g" "$REPO/launchd.plist.in" > "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl list | grep "$LABEL"
