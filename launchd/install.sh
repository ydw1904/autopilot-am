#!/usr/bin/env bash
set -euo pipefail

AM_LAUNCHD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AM_ROOT_DIR="$(cd "$AM_LAUNCHD_DIR/.." && pwd)"
AM_USER_HOME="${HOME:?HOME is required}"
AM_AGENT_DIR="$AM_USER_HOME/Library/LaunchAgents"
AM_LOG_DIR="$AM_USER_HOME/.airlines_manager"
AM_GUI_DOMAIN="gui/$(id -u)"

mkdir -p "$AM_AGENT_DIR" "$AM_LOG_DIR"

escape_replacement() {
    printf '%s' "$1" | sed 's/[&|]/\\&/g'
}

AM_ROOT_ESCAPED="$(escape_replacement "$AM_ROOT_DIR")"
AM_HOME_ESCAPED="$(escape_replacement "$AM_USER_HOME")"

for AM_LABEL in com.lobster.am-web-dev com.lobster.am-shm-watcher; do
    AM_TEMPLATE="$AM_LAUNCHD_DIR/$AM_LABEL.plist.in"
    AM_TARGET="$AM_AGENT_DIR/$AM_LABEL.plist"
    AM_TEMP="$AM_TARGET.tmp"

    sed -e "s|@AM_ROOT@|$AM_ROOT_ESCAPED|g" \
        -e "s|@AM_USER_HOME@|$AM_HOME_ESCAPED|g" \
        "$AM_TEMPLATE" > "$AM_TEMP"
    plutil -lint "$AM_TEMP"
    mv "$AM_TEMP" "$AM_TARGET"
    chmod 0644 "$AM_TARGET"

    launchctl bootout "$AM_GUI_DOMAIN/$AM_LABEL" 2>/dev/null || true
    launchctl bootstrap "$AM_GUI_DOMAIN" "$AM_TARGET"
    launchctl enable "$AM_GUI_DOMAIN/$AM_LABEL"
    echo "Installed and started $AM_LABEL"
done

echo "Web UI: http://127.0.0.1:3000/app/"
echo "API:    http://127.0.0.1:8000/"
echo "Logs:   $AM_LOG_DIR/web-dev.log and $AM_LOG_DIR/shm-watcher.log"
