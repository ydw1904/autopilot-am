"""Tests for the mobile login-screen state detector.

Hermetic: builds synthetic luma frames, so no device, adb or screenshot fixture
is needed. Run with:

    .venv/bin/python -m pytest code/tests/ -q
    # this file alone:  .venv/bin/python -m pytest code/tests/test_mobile_login.py -q

Real screenshots are deliberately NOT committed as fixtures — the login screen
renders the account's email address. The box means below are the values actually
measured off those screens (2026-08-05, BlueStacks 1920x1080):

    box            expired-dialog   login-screen   promo-overlay
    OK button          181.5            36.6           255.0
    Login button        34.9           177.4           255.0
"""

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mobile_login as ml


def frame(ok_val: float, login_val: float, bg: float = 20.0,
          w: int = 1920, h: int = 1080) -> np.ndarray:
    """A luma frame with the two probe boxes filled to the given levels."""
    f = np.full((h, w), bg, dtype=np.float32)
    for box, val in ((ml.OK_BOX, ok_val), (ml.LOGIN_BOX, login_val)):
        x0, y0, x1, y1 = box
        f[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)] = val
    return f


class TestDetect(unittest.TestCase):
    def test_expired_dialog(self):
        self.assertEqual(ml.detect(frame(181.5, 34.9)), "dialog")

    def test_login_screen(self):
        self.assertEqual(ml.detect(frame(36.6, 177.4)), "login")

    def test_promo_overlay_is_not_a_button(self):
        # The post-login promo panels are white and light BOTH boxes. Guessing
        # here would tap "Play in Tycoon mode" — i.e. start a new airline.
        self.assertEqual(ml.detect(frame(255.0, 255.0)), "unknown")

    def test_nothing_showing(self):
        self.assertEqual(ml.detect(frame(20.0, 20.0)), "unknown")

    def test_resolution_independent(self):
        # Boxes are fractions of the screen, so a differently-sized instance
        # must classify identically.
        for w, h in ((1920, 1080), (1280, 720), (2560, 1440)):
            self.assertEqual(ml.detect(frame(181.5, 34.9, w=w, h=h)), "dialog")
            self.assertEqual(ml.detect(frame(36.6, 177.4, w=w, h=h)), "login")


class TestGrabValidation(unittest.TestCase):
    def test_rejects_short_framebuffer(self):
        """A truncated screencap must raise, not silently misclassify."""
        import struct
        bad = struct.pack("<IIII", 1920, 1080, 1, 0) + b"\x00" * 128
        orig = ml.adb
        ml.adb = lambda *a, **k: bad
        try:
            with self.assertRaises(ml.LoginError):
                ml.grab()
        finally:
            ml.adb = orig


if __name__ == "__main__":
    unittest.main()
