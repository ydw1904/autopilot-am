#!/usr/bin/env python3
"""
Mobile login driver — press the app's "OK" / "Login" buttons over adb when the
mobile session has expired.

Background: the mobile access_token expires ~daily. Usually relaunching the app
is enough (it auto-logs-in from saved credentials), which is what
`refresh_mobile_session.sh` relies on. But once the *refresh* token goes too,
the app stops auto-logging-in and parks on:

    "Session expired. Please log in again."   [OK]
      → login screen, credentials pre-filled  [Login]

Two taps, and the app posts `oauth/v2/token` and comes back with a fresh
access_token. This module performs those taps.

Why it decides by TOKEN and not by pixels
-----------------------------------------
The obvious design — screenshot, recognise the screen, tap — is unreliable
here, because the game stacks promo overlays after login ("Word of the day"
and friends) whose big white panels make every brightness probe read ~255.
So the authoritative signal is whether the session validates; the pixel check
is only a *guard*, used when we already know we're logged out. In that state
only two screens are possible and they separate cleanly (measured):

    box            expired-dialog   login-screen
    OK button          180.6            36.6
    Login button        34.9           177.4

The guard exists so a blind tap can never land on "Play in Tycoon mode" (which
would start creating a new airline) if the app is showing something unexpected.

Screenshots use `adb exec-out screencap` WITHOUT -p: the raw framebuffer is a
16-byte header (w, h, format, colorspace) then RGBA8888, which numpy reads
directly. Decoding PNG would mean adding Pillow for no gain.

Usage:
    python3 mobile_login.py                # drive the login screen
    python3 mobile_login.py --dry-run      # report the detected state only
"""

from __future__ import annotations

import argparse
import struct
import subprocess
import sys
import time

import numpy as np

DEVICE = "127.0.0.1:5555"

# Button centres and probe boxes as fractions of the screen, measured on the
# BlueStacks 1920x1080 instance. Fractions rather than pixels so a different
# instance resolution still lands on the right control.
OK_BUTTON = (958 / 1920, 793 / 1080)
LOGIN_BUTTON = (1414 / 1920, 698 / 1080)
OK_BOX = (800 / 1920, 770 / 1080, 1120 / 1920, 820 / 1080)
LOGIN_BOX = (1190 / 1920, 670 / 1080, 1640 / 1920, 730 / 1080)

# Midpoint of the measured separation (~35 vs ~180) with room to spare either
# way; the buttons are light-on-dark in both states.
BRIGHT = 120.0

SCREENCAP_HEADER = 16


class LoginError(RuntimeError):
    pass


def adb(*args, device: str = DEVICE, binary: bool = False, timeout: int = 30):
    cmd = ["adb", "-s", device, *args]
    p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if p.returncode != 0:
        raise LoginError(f"adb {' '.join(args)} failed: "
                         f"{p.stderr.decode('utf-8', 'replace').strip()}")
    return p.stdout if binary else p.stdout.decode("utf-8", "replace")


def grab(device: str = DEVICE) -> np.ndarray:
    """Current screen as an (h, w) float luma array."""
    raw = adb("exec-out", "screencap", device=device, binary=True)
    if len(raw) < SCREENCAP_HEADER:
        raise LoginError(f"screencap returned {len(raw)} bytes")
    w, h, fmt = struct.unpack("<III", raw[:12])
    expect = SCREENCAP_HEADER + w * h * 4
    if len(raw) != expect:
        raise LoginError(
            f"screencap size mismatch: {w}x{h} fmt={fmt} wanted {expect}, "
            f"got {len(raw)} — unexpected framebuffer layout")
    px = np.frombuffer(raw[SCREENCAP_HEADER:], dtype=np.uint8).reshape(h, w, 4)
    return (0.299 * px[:, :, 0] + 0.587 * px[:, :, 1]
            + 0.114 * px[:, :, 2]).astype(np.float32)


def _box_mean(luma: np.ndarray, box) -> float:
    h, w = luma.shape
    x0, y0, x1, y1 = box
    return float(luma[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)].mean())


def detect(luma: np.ndarray) -> str:
    """'dialog' | 'login' | 'unknown' — only meaningful when logged out."""
    ok, login = _box_mean(luma, OK_BOX), _box_mean(luma, LOGIN_BOX)
    if ok > BRIGHT and login <= BRIGHT:
        return "dialog"
    if login > BRIGHT and ok <= BRIGHT:
        return "login"
    # Both bright = an overlay is covering things (promo panels are white);
    # both dark = neither screen is up. Either way, don't guess.
    return "unknown"


def tap(where, device: str = DEVICE) -> None:
    luma = grab(device)
    h, w = luma.shape
    x, y = int(where[0] * w), int(where[1] * h)
    adb("shell", "input", "tap", str(x), str(y), device=device)


def drive_login(device: str = DEVICE, rounds: int = 6,
                verbose: bool = True) -> bool:
    """Tap through the expired-session dialog and the login screen.

    Returns True once neither screen is showing any more (i.e. the app moved
    on), False if it was still stuck after `rounds`. Whether the *token* is
    actually good is the caller's call — see refresh_mobile_session.sh.
    """
    def say(*a):
        if verbose:
            print("   ", *a, flush=True)

    for i in range(1, rounds + 1):
        state = detect(grab(device))
        if state == "dialog":
            say(f"[{i}] session-expired dialog → tap OK")
            tap(OK_BUTTON, device)
            time.sleep(2.0)
        elif state == "login":
            say(f"[{i}] login screen → tap Login")
            tap(LOGIN_BUTTON, device)
            # The oauth round-trip plus the first authenticated calls.
            time.sleep(8.0)
        else:
            # The dialog can pop back OVER the login screen while the app
            # retries with the stale token, so 'unknown' right after a tap is
            # normal — give it a beat and re-read before believing it.
            time.sleep(2.0)
            if detect(grab(device)) == "unknown":
                say(f"[{i}] neither screen showing — done")
                return True
    return False


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--device", default=DEVICE)
    p.add_argument("--dry-run", action="store_true",
                   help="Report the detected state and probe values; tap nothing.")
    p.add_argument("--rounds", type=int, default=6)
    args = p.parse_args()

    try:
        subprocess.run(["adb", "connect", args.device],
                       capture_output=True, timeout=15)
        luma = grab(args.device)
    except (LoginError, subprocess.SubprocessError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    state = detect(luma)
    print(f"screen {luma.shape[1]}x{luma.shape[0]}  "
          f"OK_box={_box_mean(luma, OK_BOX):.1f}  "
          f"Login_box={_box_mean(luma, LOGIN_BOX):.1f}  → {state}")
    if args.dry_run:
        return 0
    if state == "unknown":
        print("Neither the dialog nor the login screen is showing — "
              "nothing to press.")
        return 0
    return 0 if drive_login(args.device, args.rounds) else 2


if __name__ == "__main__":
    sys.exit(main())
