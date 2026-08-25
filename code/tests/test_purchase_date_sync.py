"""Background purchase-date backfill: fan-out, cancellation, and auth abort."""

import threading

import mobile_api
import mobile_store
import purchase_date_sync as pds


class _Session:
    pass


def _fake_client(seen, fail_on=(), auth_error_on=()):
    """AMClient stand-in that records the ids it was asked to fetch."""

    class Client:
        def __init__(self, session, store=None):
            self.closed = False

        def aircraft(self, aircraft_id):
            seen.append(aircraft_id)
            if aircraft_id in auth_error_on:
                raise mobile_api.AMAuthError("token dead")
            if aircraft_id in fail_on:
                raise RuntimeError("profile unavailable")
            return {"id": aircraft_id}

        def close(self):
            self.closed = True

    return Client


def _patch(monkeypatch, client):
    monkeypatch.setattr(mobile_api.AMSession, "load", staticmethod(lambda: _Session()))
    monkeypatch.setattr(mobile_api, "AMClient", client)
    monkeypatch.setattr(mobile_store, "MobileStore", lambda db: object())


def test_backfill_fetches_every_aircraft_and_counts_failures(monkeypatch):
    seen = []
    _patch(monkeypatch, _fake_client(seen, fail_on={3}))
    progress = []

    result = pds.backfill([1, 2, 3, 4], workers=2, pause=False,
                          progress=lambda done, failed, total: progress.append((done, failed, total)))

    assert sorted(seen) == [1, 2, 3, 4]
    assert result == {"total": 4, "done": 3, "failed": 1, "stopped": False, "error": None}
    assert progress[-1] == (3, 1, 4)


def test_backfill_with_nothing_missing_makes_no_requests(monkeypatch):
    seen = []
    _patch(monkeypatch, _fake_client(seen))

    result = pds.backfill([], workers=4, pause=False)

    assert seen == []
    assert result["total"] == 0


def test_backfill_stops_when_asked(monkeypatch):
    seen = []
    _patch(monkeypatch, _fake_client(seen))
    stop = threading.Event()
    stop.set()

    result = pds.backfill([1, 2, 3], workers=1, pause=False, should_stop=stop.is_set)

    assert seen == []
    assert result["stopped"] is True
    assert result["done"] == 0


def test_backfill_aborts_the_sweep_on_an_auth_failure(monkeypatch):
    seen = []
    _patch(monkeypatch, _fake_client(seen, auth_error_on={1}))

    result = pds.backfill([1, 2, 3, 4], workers=1, pause=False)

    # The first id kills the session, so nothing after it is attempted.
    assert seen == [1]
    assert result["error"] == "token dead"
    assert result["done"] == 0


def test_backfill_paces_itself_and_stays_single_threaded_by_default(monkeypatch):
    seen = []
    gaps = []
    _patch(monkeypatch, _fake_client(seen))
    monkeypatch.setattr(pds.time, "sleep", lambda seconds: gaps.append(seconds))

    pds.backfill([1, 2, 3])

    # One jittered gap per request, and nothing runs in parallel: the sweep has
    # to look like a player tapping through aircraft cards.
    assert pds.DEFAULT_WORKERS == 1
    assert len(gaps) == 3
    assert all(pds.MIN_PAUSE <= gap <= pds.MAX_PAUSE for gap in gaps)
