#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$HOME/.bun/bin:$HOME/.cargo/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"

echo "✈ Building modern web frontend with Bun..."
if command -v bun &> /dev/null; then
    (cd "$DIR/web" && bun run build)
else
    echo "Warning: Bun not found in PATH, using existing web/dist bundle if available."
fi

echo "✈ Starting Autopilot AM Web Application & API server..."
exec "$DIR/.venv/bin/python" "$DIR/code/api_server.py" "$@"
