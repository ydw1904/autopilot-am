#!/usr/bin/env bash
set -euo pipefail

AM_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AM_API_PORT="${AM_API_PORT:-8000}"
AM_WEB_PORT="${AM_WEB_PORT:-3000}"
AM_API_PID=""
AM_WEB_PID=""

require_port_free() {
    local port="$1"
    if command -v lsof >/dev/null && lsof -tiTCP:"$port" -sTCP:LISTEN >/dev/null; then
        echo "Port $port is already in use. Stop that process before running ./run_dev.sh." >&2
        exit 1
    fi
}

cleanup() {
    trap - EXIT INT TERM
    if [ -n "$AM_WEB_PID" ] && kill -0 "$AM_WEB_PID" 2>/dev/null; then
        kill "$AM_WEB_PID"
    fi
    if [ -n "$AM_API_PID" ] && kill -0 "$AM_API_PID" 2>/dev/null; then
        kill "$AM_API_PID"
    fi
    wait 2>/dev/null || true
}

trap cleanup EXIT INT TERM

require_port_free "$AM_API_PORT"
require_port_free "$AM_WEB_PORT"

if [ ! -x "$AM_ROOT/.venv/bin/python" ]; then
    echo "Missing .venv. Create it and install code/requirements.txt first." >&2
    exit 1
fi
if [ ! -d "$AM_ROOT/web/node_modules" ]; then
    echo "Missing web/node_modules. Run bun install or npm install in web/." >&2
    exit 1
fi

"$AM_ROOT/.venv/bin/python" "$AM_ROOT/code/api_server.py" \
    --host 127.0.0.1 --port "$AM_API_PORT" --reload &
AM_API_PID=$!

(
    cd "$AM_ROOT/web"
    if command -v bun >/dev/null; then
        exec bun run dev -- --host 127.0.0.1 --port "$AM_WEB_PORT" --strictPort
    fi
    exec npm run dev -- --host 127.0.0.1 --port "$AM_WEB_PORT" --strictPort
) &
AM_WEB_PID=$!

echo "Development UI: http://localhost:$AM_WEB_PORT/"
echo "API server:     http://127.0.0.1:$AM_API_PORT/"
echo "React/CSS changes use hot reload. Python changes restart FastAPI automatically."

while kill -0 "$AM_API_PID" 2>/dev/null && kill -0 "$AM_WEB_PID" 2>/dev/null; do
    sleep 1
done

exit 1
