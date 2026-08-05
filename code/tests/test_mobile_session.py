"""Tests for mobile session self-renewal (OAuth material, grants, rotation).

Hermetic: no network and no session file — token exchanges are faked by
swapping `mobile_api._token_request`. Run with:

    .venv/bin/python -m pytest code/tests/ -q
    # this file alone:  .venv/bin/python -m pytest code/tests/test_mobile_session.py -q
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mobile_api as ma


def a_session(**kw):
    base = dict(base_url="https://www.airlines-manager.com", player_id="1",
                access_token="old", cookies={})
    base.update(kw)
    return ma.AMSession(**base)


GRANT = {"access_token": "new-access", "refresh_token": "new-refresh",
         "expires_in": 10800, "airlineId": 9358830, "version": ma.APP_VERSION,
         "gameServer": "www.airlines-manager.com"}


class TestLoadCompat(unittest.TestCase):
    def test_loads_a_pre_oauth_session_file(self):
        """Session files written before self-renewal existed must still load."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "session.json"
            p.write_text(json.dumps({"base_url": "https://x", "player_id": 42,
                                     "access_token": "t", "cookies": {"a": "b"}}))
            s = ma.AMSession.load(p)
            self.assertEqual(s.player_id, "42")     # coerced to str
            self.assertIsNone(s.refresh_token)
            self.assertFalse(s.can_renew)

    def test_ignores_unknown_fields(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "session.json"
            p.write_text(json.dumps({"player_id": 1, "access_token": "t",
                                     "cookies": {}, "something_new": "?"}))
            self.assertEqual(ma.AMSession.load(p).access_token, "t")

    def test_save_is_owner_only(self):
        """The file holds a live token, a refresh token and maybe the password."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "session.json"
            a_session(password="hunter2").save(p)
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)


class TestCanRenew(unittest.TestCase):
    def test_needs_client_credentials(self):
        self.assertFalse(a_session(refresh_token="r").can_renew)

    def test_refresh_token_is_enough(self):
        self.assertTrue(a_session(client_id="c", client_secret="s",
                                  refresh_token="r").can_renew)

    def test_password_is_enough(self):
        self.assertTrue(a_session(client_id="c", client_secret="s",
                                  username="u", password="p").can_renew)

    def test_username_without_password_is_not(self):
        self.assertFalse(a_session(client_id="c", client_secret="s",
                                   username="u").can_renew)


class TestRenew(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self._orig = ma._token_request
        ma._token_request = self.fake
    def tearDown(self):
        ma._token_request = self._orig

    def fake(self, data, auth_base=ma.AUTH_BASE):
        self.calls.append(data)
        if data["grant_type"] == "refresh_token" and data["refresh_token"] == "dead":
            raise ma.AMAuthError("Invalid refresh token")
        return dict(GRANT)

    def test_prefers_refresh_token(self):
        s = a_session(client_id="c", client_secret="s", refresh_token="good",
                      username="u", password="p")
        self.assertEqual(s.renew(save=False), "refresh_token")
        self.assertEqual([c["grant_type"] for c in self.calls], ["refresh_token"])

    def test_rotates_and_keeps_the_new_refresh_token(self):
        """Refresh tokens are single-use; dropping the new one breaks the chain."""
        s = a_session(client_id="c", client_secret="s", refresh_token="good")
        s.renew(save=False)
        self.assertEqual(s.refresh_token, "new-refresh")
        self.assertEqual(s.access_token, "new-access")

    def test_falls_back_to_password_when_refresh_is_dead(self):
        s = a_session(client_id="c", client_secret="s", refresh_token="dead",
                      username="u", password="p")
        self.assertEqual(s.renew(save=False), "password")
        self.assertEqual([c["grant_type"] for c in self.calls],
                         ["refresh_token", "password"])

    def test_dead_refresh_token_is_cleared(self):
        s = a_session(client_id="c", client_secret="s", refresh_token="dead",
                      username="u", password="p")
        s.renew(save=False)
        self.assertEqual(s.refresh_token, "new-refresh")  # replaced, not stale

    def test_no_password_fallback_raises(self):
        s = a_session(client_id="c", client_secret="s", refresh_token="dead")
        with self.assertRaises(ma.AMAuthError):
            s.renew(save=False)

    def test_without_client_credentials_raises(self):
        with self.assertRaises(ma.AMAuthError):
            a_session(refresh_token="good").renew(save=False)

    def test_absorb_sets_expiry_and_ids(self):
        s = a_session(client_id="c", client_secret="s", refresh_token="good")
        before = time.time()
        s.renew(save=False)
        self.assertGreater(s.expires_at, before + 10000)
        self.assertEqual(s.player_id, "9358830")
        self.assertEqual(s.base_url, "https://www.airlines-manager.com")


class TestVersionGuard(unittest.TestCase):
    """A version-0 grant returns HTTP 200 and a usable-looking token, then
    fails obscurely on bfa/paged/aircraft and the auctions. Catch it early."""

    def _post(self, status, body):
        class FakeResp:
            status_code = status
            def json(self_inner): return body
        class FakeClient:
            def __enter__(self_inner): return self_inner
            def __exit__(self_inner, *a): return False
            def post(self_inner, *a, **k): return FakeResp()
        orig = ma.httpx.Client
        ma.httpx.Client = lambda *a, **k: FakeClient()
        try:
            return ma._token_request({"grant_type": "password"})
        finally:
            ma.httpx.Client = orig

    def test_rejects_version_zero(self):
        with self.assertRaises(ma.AMAuthError) as cm:
            self._post(200, {"access_token": "t", "version": 0})
        self.assertIn("version", str(cm.exception))

    def test_accepts_correct_version(self):
        body = self._post(200, {"access_token": "t", "version": ma.APP_VERSION})
        self.assertEqual(body["access_token"], "t")

    def test_accepts_response_without_version_field(self):
        self.assertEqual(self._post(200, {"access_token": "t"})["access_token"], "t")

    def test_raises_on_refusal(self):
        with self.assertRaises(ma.AMAuthError):
            self._post(400, {"message": "invalid_grant"})


if __name__ == "__main__":
    unittest.main()
