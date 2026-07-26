#!/usr/bin/env bash
#
# bluestacks_mitm_setup.sh
#
# Connect the Airlines Manager mobile app (com.Playrion.AirlinesManager2)
# running inside BlueStacks Air to a local mitmproxy so its HTTPS API traffic
# (tycoon-ppd.airlines-manager.com etc.) can be captured as clean JSON.
#
# This automates the mechanical parts. The two steps that need a human are
# called out inline: (A) enabling Root in the BlueStacks UI, (B) trusting the
# proxy's TLS cert. Run this AFTER starting mitmproxy in another terminal.
#
# Usage:
#   1. Terminal 1:  mitmweb --listen-host 0.0.0.0 -p 8080   (leave running)
#   2. Terminal 2:  ./tools/bluestacks_mitm_setup.sh
#
set -euo pipefail

DEVICE="127.0.0.1:5555"
PROXY_PORT="8080"
PKG="com.Playrion.AirlinesManager2"
MITM_CA="$HOME/.mitmproxy/mitmproxy-ca-cert.pem"

# ── host IP the emulator must reach the proxy on ────────────────────────────
HOST_IP="$(ipconfig getifaddr en0 || true)"
[ -z "$HOST_IP" ] && HOST_IP="$(ipconfig getifaddr en1 || true)"
if [ -z "$HOST_IP" ]; then
  echo "Could not auto-detect host IP. Set HOST_IP manually." >&2; exit 1
fi
echo "Host IP (proxy target for the emulator): $HOST_IP:$PROXY_PORT"

# ── connect adb ─────────────────────────────────────────────────────────────
adb connect "$DEVICE" >/dev/null
adb -s "$DEVICE" wait-for-device
echo "adb connected to $DEVICE"

# ── require the mitm CA ─────────────────────────────────────────────────────
if [ ! -f "$MITM_CA" ]; then
  echo "mitmproxy CA not found at $MITM_CA — start mitmproxy once to generate it." >&2
  exit 1
fi
# Android system trust store filename = old-style subject hash + .0
CA_HASH="$(openssl x509 -inform PEM -subject_hash_old -in "$MITM_CA" -noout)"
CA_SYS="/data/local/tmp/${CA_HASH}.0"
adb -s "$DEVICE" push "$MITM_CA" "$CA_SYS" >/dev/null
echo "Pushed CA to $CA_SYS  (system name ${CA_HASH}.0)"

# ── check for root (needed to write the CA into the system trust store) ─────
# NOTE: `adb root` on BlueStacks Air often reports success but adbd stays uid
# 2000. Real root comes from the in-app "Root" toggle, which grants `su`.
HAVE_SU=0
if adb -s "$DEVICE" shell 'command -v su >/dev/null 2>&1 && echo yes' | grep -q yes; then
  HAVE_SU=1
fi

if [ "$HAVE_SU" -eq 0 ]; then
  cat <<'EOF'

────────────────────────────────────────────────────────────────────────────
 MANUAL STEP A — enable Root in BlueStacks (one time)
────────────────────────────────────────────────────────────────────────────
 No `su` in the emulator yet. The mobile app is Unity/il2cpp with NO
 network_security_config, so it ONLY trusts the SYSTEM CA store. Getting the
 mitm cert into that store needs root.

   1. BlueStacks Air  →  gear (Settings)  →  Advanced
   2. Turn ON  "Root access"  (and "Android Debug Bridge" if listed)
   3. Save  →  Restart the instance when prompted
   4. Re-run this script.

 If your BlueStacks build exposes no Root toggle, use the APK-repackage route
 in tools/bluestacks_mitm_runbook.md instead (no root required).
────────────────────────────────────────────────────────────────────────────
EOF
  # Still set the proxy so the non-TLS bootstrap is visible in mitm.
  adb -s "$DEVICE" shell "settings put global http_proxy ${HOST_IP}:${PROXY_PORT}"
  echo "Device proxy set to ${HOST_IP}:${PROXY_PORT} (TLS will fail until CA trusted)."
  exit 0
fi

# ── install CA into the system store via su + tmpfs overlay ─────────────────
# /system is read-only and not remountable, so overlay the cacerts dir with a
# tmpfs copy that includes our cert (survives until reboot).
echo "Root available — installing CA into system trust store…"
adb -s "$DEVICE" shell "su -c '
  set -e
  D=/system/etc/security/cacerts
  cp -f /apex/com.android.conscrypt/cacerts/* \$D 2>/dev/null || true
  mount -t tmpfs tmpfs \$D 2>/dev/null || true
  cp \$D/../cacerts_backup/* \$D 2>/dev/null || true
  # repopulate with existing system certs then add ours
  for f in /system/etc/security/cacerts.bak/*; do :; done 2>/dev/null || true
  cp ${CA_SYS} \$D/${CA_HASH}.0
  chmod 644 \$D/${CA_HASH}.0
  chown root:root \$D/${CA_HASH}.0
  ls -l \$D/${CA_HASH}.0
'" || {
    echo "tmpfs overlay path failed — see runbook for the full magisk-style install." >&2
}

# ── point the emulator at the proxy ─────────────────────────────────────────
adb -s "$DEVICE" shell "settings put global http_proxy ${HOST_IP}:${PROXY_PORT}"
echo "Device proxy = ${HOST_IP}:${PROXY_PORT}"

# ── restart the app so it picks up proxy + trust ────────────────────────────
adb -s "$DEVICE" shell "am force-stop ${PKG}"
adb -s "$DEVICE" shell "monkey -p ${PKG} -c android.intent.category.LAUNCHER 1" >/dev/null 2>&1 || true
echo
echo "Done. Open mitmweb (http://127.0.0.1:8081) and filter for:"
echo "    ~d airlines-manager.com"
echo "Play the mobile app; audit/demand calls should appear as JSON."
echo
echo "To UNSET the proxy later:"
echo "    adb -s ${DEVICE} shell settings put global http_proxy :0"
