#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AM_PORT="${AM_PORT:-8000}"

for arg in "$@"; do
    case "$arg" in
        --port=*) AM_PORT="${arg#--port=}" ;;
    esac
done

for ((index = 1; index <= $#; index++)); do
    if [ "${!index}" = "--port" ]; then
        next=$((index + 1))
        [ "$next" -le "$#" ] || { echo "--port needs a value" >&2; exit 2; }
        AM_PORT="${!next}"
    fi
done

[[ "$AM_PORT" =~ ^[0-9]+$ ]] && [ "$AM_PORT" -ge 1 ] && [ "$AM_PORT" -le 65535 ] || {
    echo "Port must be between 1 and 65535." >&2
    exit 2
}

stop_port_listeners() {
    command -v lsof >/dev/null || return 0
    local pids=()
    local pid
    while IFS= read -r pid; do pids+=("$pid"); done < <(lsof -tiTCP:"$AM_PORT" -sTCP:LISTEN || true)
    [ "${#pids[@]}" -gt 0 ] || return 0

    echo "Stopping process(es) using port $AM_PORT: ${pids[*]}"
    kill "${pids[@]}"
    for _ in {1..20}; do
        sleep 0.1
        pids=()
        while IFS= read -r pid; do pids+=("$pid"); done < <(lsof -tiTCP:"$AM_PORT" -sTCP:LISTEN || true)
        [ "${#pids[@]}" -gt 0 ] || return 0
    done
    echo "Force-stopping process(es) still using port $AM_PORT: ${pids[*]}"
    kill -KILL "${pids[@]}"
    return 0
}

AM_BUN="$(command -v bun || true)"
if [ -z "$AM_BUN" ] && [ -x "${BUN_INSTALL:-$HOME/.bun}/bin/bun" ]; then
    AM_BUN="${BUN_INSTALL:-$HOME/.bun}/bin/bun"
fi

if [ -n "$AM_BUN" ]; then
    if [ ! -d "$DIR/web/node_modules" ]; then
        echo "Installing frontend dependencies..."
        (cd "$DIR/web" && "$AM_BUN" install --frozen-lockfile)
    fi
    echo "Building Autopilot AM control center..."
    (cd "$DIR/web" && "$AM_BUN" run build)
elif [ ! -f "$DIR/web/dist/index.html" ]; then
    echo "Bun is required for the first frontend build." >&2
    exit 1
fi

stop_port_listeners
echo "Starting Autopilot AM browser app and API server on port $AM_PORT..."
exec "$DIR/.venv/bin/python" "$DIR/code/api_server.py" "$@"
