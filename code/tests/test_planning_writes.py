"""The mobile schedule-write path: URL shapes and the backend dispatch.

`clear_schedule` / `submit_flights` take either an AMClient or a CDP handle,
and the mobile endpoints are the fiddly part — the aircraft id is a PATH
segment on `planning/add/{id}`, and `planning/delete/{id}` is a GET despite
being a write (POST/PUT/DELETE all answer 405). No network: AMClient._request
is stubbed and every call it would make is recorded.
"""

import pytest

import circuit_scheduler as cs
from mobile_api import AMClient, AMError, AMSession


@pytest.fixture
def client(monkeypatch):
    c = AMClient.__new__(AMClient)          # no session, no http client
    calls = []

    def fake_request(method, endpoint, params=None, data=None, allow_empty=False):
        calls.append((method, endpoint, data))
        if endpoint.startswith("planning/add/") and data.get("lineId") == 0:
            raise AMError("network.addPlanning.error")
        return {"status": 1}

    c._request = fake_request
    c.calls = calls
    return c


def test_clear_is_a_get_on_the_aircraft_path(client):
    assert cs.clear_schedule(client, 42) == {"result": True, "message": "cleared"}
    assert client.calls == [("GET", "planning/delete/42", None)]


def test_clear_reports_a_refusal_instead_of_raising(client):
    def boom(*a, **kw):
        raise AMError("nope")
    client._request = boom
    assert cs.clear_schedule(client, 42)["result"] is False


def test_each_flight_is_its_own_add_call(client):
    flights = [{"lineId": 7, "takeOffTime": 0},
               {"lineId": 9, "takeOffTime": 21600}]
    assert cs.submit_flights(client, 42, flights)["result"] is True
    assert client.calls == [
        ("POST", "planning/add/42", {"lineId": 7, "takeOffTime": 0}),
        ("POST", "planning/add/42", {"lineId": 9, "takeOffTime": 21600}),
    ]


def test_a_failed_flight_stops_the_run_and_says_which(client):
    flights = [{"lineId": 7, "takeOffTime": 0},
               {"lineId": 0, "takeOffTime": 900},
               {"lineId": 9, "takeOffTime": 1800}]
    res = cs.submit_flights(client, 42, flights)
    assert res["result"] is False
    assert "2/3" in res["message"]
    assert len(client.calls) == 2          # the third is never attempted


def test_no_flights_is_not_a_call(client):
    assert cs.submit_flights(client, 42, [])["result"] is True
    assert client.calls == []


def test_a_cdp_handle_still_takes_the_web_path(monkeypatch):
    seen = {}

    def fake_call(cdp, payload):
        seen["payload"] = payload
        return {"result": True}

    monkeypatch.setattr(cs, "planning_api_call", fake_call)
    sentinel = object()                     # not an AMClient
    cs.submit_flights(sentinel, 42, [{"lineId": 7, "takeOffTime": 0}])
    assert seen["payload"] == {"aircraftId": 42,
                               "added": [{"lineId": 7, "takeOffTime": 0}]}
    cs.clear_schedule(sentinel, 42)
    assert seen["payload"] == {"aircraftId": 42}


def test_mobile_client_is_none_without_a_session(monkeypatch):
    monkeypatch.setattr(AMSession, "load", classmethod(lambda cls: (_ for _ in ()).throw(AMError("no session"))))
    assert cs._mobile_client() is None
