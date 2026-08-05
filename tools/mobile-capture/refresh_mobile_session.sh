#!/usr/bin/env bash
#
# refresh_mobile_session.sh
#
# One-shot refresh of the mobile API access_token (~/.airlines_manager/session.json).
# The token expires roughly daily; re-run this whenever the mobile_* MCP tools
# start returning auth errors.
#
# What it does:
#   1. picks a proxy address the emulator can actually reach
#   2. starts mitmdump + capture_am.py
#   3. points the device at it, relaunches the game (it auto-logs-in)
#   4. waits for a fresh access_token to show up in the capture
#   5. imports it into ~/.airlines_manager/session.json and validates
#   6. ALWAYS clears the device proxy and stops mitmdump on exit
#
# Step 6 matters: a device http_proxy pointing at a proxy that is not running
# makes BlueStacks look like it has "no internet". Never leave one set.
#
# Usage:  ./tools/mobile-capture/refresh_mobile_session.sh
#
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEVICE="${AM_DEVICE:-127.0.0.1:5555}"
PORT="${AM_PROXY_PORT:-8080}"
PKG="com.Playrion.AirlinesManager2"
CAPTURE="$REPO/tools/mobile-capture/captures/am_api.jsonl"
VENV_PY="$REPO/.venv/bin/python"
MITM_PID=""

log() { printf '  %s\n' "$*"; }

cleanup() {
  echo
  echo "── cleanup ─────────────────────────────────────────────"
  adb -s "$DEVICE" shell settings put global http_proxy :0 >/dev/null 2>&1 \
    && log "device proxy cleared (BlueStacks internet restored)"
  if [ -n "$MITM_PID" ] && kill -0 "$MITM_PID" 2>/dev/null; then
    kill "$MITM_PID" 2>/dev/null; wait "$MITM_PID" 2>/dev/null
    log "mitmdump stopped"
  fi
}
trap cleanup EXIT INT TERM

[ -x "$VENV_PY" ] || { echo "venv missing: $VENV_PY"; exit 1; }

# ── 0. try to renew over HTTP first ─────────────────────────────────────
# Once a session has been bootstrapped, it holds the app's OAuth client
# credentials and can mint a new access_token with one POST — no emulator, no
# proxy, no capture. That is the normal path; everything below is the
# bootstrap, needed only for a brand-new session or if the credentials change.
echo "── 0. renew over HTTP ──────────────────────────────────"
if "$VENV_PY" -c "
import sys, logging
logging.disable(logging.INFO)
sys.path.insert(0, '$REPO/code')
import mcp_server
r = mcp_server.mobile_session_renew()
if not r.get('ok'):
    print('  ' + str(r.get('error'))[:120]); raise SystemExit(1)
print('  grant     :', r['grant'])
print('  player_id :', r['player_id'])
print('  expires_in:', r['expires_in_min'], 'min')
print('  balance   :', f\"{r['balance']:,}\")
" 2>/dev/null; then
  echo
  echo "Session renewed over HTTP -> ~/.airlines_manager/session.json"
  echo "(BlueStacks and mitmproxy were not needed.)"
  trap - EXIT INT TERM     # nothing was started, so nothing to clean up
  exit 0
fi
log "HTTP renewal unavailable — falling back to the capture bootstrap"
echo

command -v mitmdump >/dev/null || { echo "mitmdump not found (brew install mitmproxy)"; exit 1; }

echo "── 1. connect device ───────────────────────────────────"
adb connect "$DEVICE" >/dev/null 2>&1
adb -s "$DEVICE" wait-for-device || { echo "device not reachable"; exit 1; }
log "adb: $DEVICE"

echo "── 2. pick a reachable proxy address ───────────────────"
# BlueStacks Air runs slirp NAT: 10.0.2.2 is the host, forwarded to host
# loopback. That is preferred — it works even when the LAN blocks
# client-to-client traffic, and it lets mitmdump bind to localhost only
# instead of exposing an open proxy on the network.
LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
PROXY_HOST=""
BIND_HOST=""
probe() {  # $1 = address to reach the host on
  mitmdump --listen-host "$2" -p "$PORT" >/dev/null 2>&1 &
  local p=$!; sleep 3
  local out
  out="$(adb -s "$DEVICE" shell "http_proxy=http://$1:$PORT /system/xbin/wget -O /dev/null -T 6 http://example.com/ 2>&1")"
  kill "$p" 2>/dev/null; wait "$p" 2>/dev/null
  grep -q "saved" <<<"$out"
}
if probe 10.0.2.2 127.0.0.1; then
  PROXY_HOST="10.0.2.2"; BIND_HOST="127.0.0.1"
  log "using 10.0.2.2 (slirp NAT -> host loopback), proxy bound to localhost"
elif [ -n "$LAN_IP" ] && probe "$LAN_IP" 0.0.0.0; then
  PROXY_HOST="$LAN_IP"; BIND_HOST="0.0.0.0"
  log "using LAN IP $LAN_IP (WARNING: proxy is exposed on the network)"
else
  echo "No reachable proxy address. Check macOS firewall / Local Network permission."
  exit 1
fi

echo "── 3. start capture ────────────────────────────────────"
mkdir -p "$(dirname "$CAPTURE")"
# Rotate: the capture holds a live bearer token, so don't accumulate old ones.
: > "$CAPTURE"
mitmdump -s "$REPO/tools/mobile-capture/capture_am.py" \
         --listen-host "$BIND_HOST" -p "$PORT" > /tmp/am_mitm.log 2>&1 &
MITM_PID=$!
sleep 4
kill -0 "$MITM_PID" 2>/dev/null || { echo "mitmdump failed to start; see /tmp/am_mitm.log"; exit 1; }
log "mitmdump pid $MITM_PID on $BIND_HOST:$PORT -> $CAPTURE"

echo "── 4. point device at proxy, relaunch game ─────────────"
adb -s "$DEVICE" shell "settings put global http_proxy ${PROXY_HOST}:${PORT}"
adb -s "$DEVICE" shell "am force-stop $PKG"
adb -s "$DEVICE" shell "monkey -p $PKG -c android.intent.category.LAUNCHER 1" >/dev/null 2>&1
log "game relaunched; it auto-logs-in with the saved credentials"

echo "── 5. acquire a live session ───────────────────────────"
# A token in the capture is NOT proof of a live session. When the refresh token
# has expired too, the app stops auto-logging-in and instead REPLAYS the stale
# access_token — the capture fills with "access_token=" requests that all answer
# invalid_grant (errorCode 11), and the old grep-for-a-token check passed while
# nothing worked. So the test is a real validated import.
import_session() {
  "$VENV_PY" -c "
import sys, logging
logging.disable(logging.INFO)          # httpx logs full URLs incl. the token
sys.path.insert(0, '$REPO/code')
import mcp_server
r = mcp_server.mobile_session_import(capture_path='$CAPTURE')
if not r.get('ok'):
    raise SystemExit(1)
print('  player_id :', r['player_id'])
print('  token_tail:', r['token_tail'])
print('  balance   :', f\"{r['balance']:,}\")
" 2>/dev/null
}

wait_for_session() {   # $1 = seconds to keep trying
  local deadline=$(( SECONDS + $1 ))
  while [ "$SECONDS" -lt "$deadline" ]; do
    if grep -q "access_token=" "$CAPTURE" 2>/dev/null && import_session; then
      return 0
    fi
    sleep 3
  done
  return 1
}

if wait_for_session 45; then
  log "session live (the app auto-logged-in)"
else
  log "no live session yet — the refresh token is gone too, so the app is"
  log "parked on 'Session expired'. Pressing OK / Login over adb …"
  "$VENV_PY" "$REPO/code/mobile_login.py" --device "$DEVICE" || true
  if wait_for_session 60; then
    log "session live (after driving the login screen)"
  else
    echo
    echo "Could not get a live session."
    echo "Check what the app is actually showing:"
    echo "  $VENV_PY $REPO/code/mobile_login.py --dry-run"
    exit 1
  fi
fi

echo
echo "Session refreshed -> ~/.airlines_manager/session.json"
