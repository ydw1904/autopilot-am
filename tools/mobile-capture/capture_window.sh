#!/usr/bin/env bash
#
# capture_window.sh
#
# Run a mobile capture inside a BOUNDED exposure window.
#
# BlueStacks binds adb to *:5555 (0.0.0.0) with no setting to change it. On a
# network where that address is publicly routable, the port must not outlive the
# capture. This starts BlueStacks, runs the given command, and kills BlueStacks
# from an EXIT trap — so the port closes on success, on failure, on Ctrl-C, and
# on timeout alike. It prints how long the port was actually open.
#
# Usage:
#   tools/mobile-capture/capture_window.sh [--budget SECONDS] -- <cmd> [args...]
#   tools/mobile-capture/capture_window.sh                       # default: refresh session
#
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEVICE="${AM_DEVICE:-127.0.0.1:5555}"
BUDGET=600
BS_APP="/Applications/BlueStacks.app"

while [ $# -gt 0 ]; do
  case "$1" in
    --budget) BUDGET="$2"; shift 2 ;;
    --) shift; break ;;
    *) break ;;
  esac
done
[ $# -eq 0 ] && set -- "$REPO/tools/mobile-capture/refresh_mobile_session.sh"

log() { printf '  %s\n' "$*"; }
OPENED_AT=""

port_open() { lsof -nP -iTCP:5555 -sTCP:LISTEN >/dev/null 2>&1; }

close_window() {
  echo
  echo "── closing exposure window ─────────────────────────────"
  pkill -f "$BS_APP/Contents/MacOS/BlueStacks" 2>/dev/null
  for _ in $(seq 1 15); do port_open || break; sleep 1; done
  if port_open; then
    echo "  WARNING: 5555 still listening — kill BlueStacks manually:"
    lsof -nP -iTCP:5555 -sTCP:LISTEN | tail -n +2 | sed 's/^/    /'
  else
    log "adb 5555 closed"
  fi
  if [ -n "$OPENED_AT" ]; then
    log "port was open for $(( SECONDS - OPENED_AT ))s"
  fi
}
trap close_window EXIT INT TERM

echo "── network posture ─────────────────────────────────────"
LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
if [ -n "$LAN_IP" ]; then
  PUBLIC="$(python3 -c "import ipaddress;print('yes' if ipaddress.ip_address('$LAN_IP').is_global else 'no')" 2>/dev/null)"
  log "primary address $LAN_IP (publicly routable: ${PUBLIC:-unknown})"
  [ "$PUBLIC" = "yes" ] && log "adb will be world-reachable while this runs — budget ${BUDGET}s"
fi

echo "── start BlueStacks ────────────────────────────────────"
if port_open; then
  log "already running (5555 listening)"
  OPENED_AT=$SECONDS
else
  open -a "$BS_APP" || { echo "could not launch BlueStacks"; exit 1; }
  for _ in $(seq 1 60); do port_open && break; sleep 2; done
  port_open || { echo "adb 5555 never came up"; exit 1; }
  OPENED_AT=$SECONDS
  log "up; adb listening"
fi
adb connect "$DEVICE" >/dev/null 2>&1
adb -s "$DEVICE" wait-for-device
# A listener on 5555 only means BlueStacks is up — Android may still be booting,
# and `adb shell` answers "error: closed" until it isn't. Wait for the real flag.
log "waiting for Android to finish booting…"
for _ in $(seq 1 90); do
  [ "$(adb -s "$DEVICE" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ] && break
  sleep 2
done
if [ "$(adb -s "$DEVICE" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" != "1" ]; then
  echo "Android did not finish booting in time"; exit 1
fi
log "boot_completed=1"

# Deadman switch: SIGKILL cannot be trapped, and if this script is killed
# outright the EXIT trap never runs and the port stays open. Detach an
# independent killer that closes the window no matter what happens here.
setsid bash -c "sleep $(( BUDGET + 60 )); pkill -f '$BS_APP/Contents/MacOS/BlueStacks'" \
  >/dev/null 2>&1 < /dev/null &
DEADMAN=$!
log "deadman armed (fires in $(( BUDGET + 60 ))s even if this script is killed)"

echo "── run: $* ─────────────────────────"
# Hard timeout so a hung child cannot hold the port open indefinitely.
"$@" &
CHILD=$!
( sleep "$BUDGET"; kill -TERM "$CHILD" 2>/dev/null ) & WATCHDOG=$!
wait "$CHILD"; RC=$?
kill "$WATCHDOG" 2>/dev/null
kill "$DEADMAN" 2>/dev/null
exit "$RC"
