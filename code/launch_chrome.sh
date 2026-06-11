#!/usr/bin/env bash
# Launch Chrome with remote debugging so the MCP server / CDP scripts can drive it.
#
# Usage:
#   code/launch_chrome.sh            # opens Airlines Manager, debug port 9222
#
# After it opens, log in to airlines-manager.com in that window and leave the
# tab open. The MCP server attaches to whichever tab is on airlines-manager.com.
set -euo pipefail

PORT="${CDP_PORT:-9222}"
PROFILE="${CDP_PROFILE:-$HOME/.am-chrome-profile}"
URL="${1:-https://www.airlines-manager.com/network/planning}"

if [[ "$OSTYPE" == darwin* ]]; then
  CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
else
  CHROME="$(command -v google-chrome || command -v chromium || echo google-chrome)"
fi

exec "$CHROME" \
  --remote-debugging-port="$PORT" \
  --remote-allow-origins=* \
  --user-data-dir="$PROFILE" \
  "$URL"
