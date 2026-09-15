"""/api/route/{hub}/{dest} — the DB row merged with the live `line/{id}` read."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

import api_server
import db as dbmod

LINE = {
    "id": "59721683", "name": "FRA/PVG", "distance": 8860,
    "purchasePrice": 13961052, "sellingPrice": 11866894,
    "purchaseDate": {"date": "2024-11-03 23:01:21.000000", "timezone": "UTC"},
    "lockedUntil": {"date": "2024-11-20 11:04:57.000000", "timezone": "UTC"},
    "isFrozen": False,
    "incidents": {"incidentNb": 2, "incidentNoFlightNb": 1},
    "price": {"eco": 3089, "bus": 4562, "first": 6343, "cargo": 5036},
    "demand": {"eco": 5266, "bus": 701, "first": 295, "cargo": 1159},
    "remainingDemand": {"eco": 1764, "bus": 239, "first": 99, "cargo": 1031},
    "audit": {"profile": {"date": {"date": "2026-02-10 11:20:36.000000"},
                          "reliability": 100,
                          "price": {"eco": 3201, "bus": 4257, "first": 7362, "cargo": 10195},
                          "demand": {"eco": 4921, "bus": 845, "first": 266, "cargo": 992}}},
}


@pytest.fixture
def api(tmp_path, monkeypatch):
    path = str(tmp_path / "route_test.db")
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE routes (hub_iata TEXT, dest_iata TEXT, dest_name TEXT, "
                "dest_country TEXT, distance_km INTEGER, dest_category INTEGER, "
                "stars INTEGER, gross_price INTEGER, line_id INTEGER, is_owned INTEGER, "
                "eco_demand INTEGER, bus_demand INTEGER, fir_demand INTEGER, cargo_demand INTEGER)")
    raw.executemany("INSERT INTO routes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("FRA", "PVG", "Shanghai", "cn", 8860, 10, None, None, 59721683, 1, 4921, 845, 266, 992),
        ("FRA", "NEW", "Nowhere", "cn", 100, 1, None, None, None, 0, 1, 0, 0, 0),
    ])
    raw.commit()
    raw.close()
    monkeypatch.setattr(dbmod, "DB", path)
    monkeypatch.setattr(dbmod, "_conn", None)
    yield TestClient(api_server.app)
    dbmod.close_db()


def _stub_client(monkeypatch, line=LINE, boom=None):
    import mobile_api

    class Fake:
        def __init__(self, *a, **kw): pass
        def line(self, line_id):
            if boom:
                raise RuntimeError(boom)
            assert line_id == 59721683
            return line
        def close(self): pass

    monkeypatch.setattr(mobile_api, "AMClient", Fake)
    monkeypatch.setattr(mobile_api.AMSession, "load", classmethod(lambda cls, *a, **kw: None))


def test_the_live_line_read_is_flattened_onto_the_cached_row(api, monkeypatch):
    _stub_client(monkeypatch)
    body = api.get("/api/route/fra/pvg").json()

    assert body["dest_name"] == "Shanghai" and body["is_owned"] is True
    assert body["error"] is None
    line = body["line"]
    # Timestamps come back as {date, timezone…} wrappers; only the date is kept.
    assert line["purchased_at"] == "2024-11-03 23:01:21.000000"
    assert line["locked_until"].startswith("2024-11-20")
    assert line["audit"]["date"].startswith("2026-02-10")
    assert line["incidents"] == 2 and line["incidents_grounded"] == 1
    assert line["price"]["cargo"] == 5036 and line["remaining"]["eco"] == 1764
    assert line["audit"]["price"] == {"eco": 3201, "bus": 4257, "first": 7362, "cargo": 10195}


def test_a_dead_mobile_session_still_answers_with_the_cached_row(api, monkeypatch):
    _stub_client(monkeypatch, boom="token expired")
    body = api.get("/api/route/FRA/PVG").json()

    assert body["line"] is None and "token expired" in body["error"]
    assert body["distance_km"] == 8860


def test_an_unbought_route_answers_without_calling_the_game(api, monkeypatch):
    _stub_client(monkeypatch, boom="must not be called")
    body = api.get("/api/route/FRA/NEW").json()

    assert body["line"] is None and "No line id" in body["error"]
    assert api.get("/api/route/FRA/ZZZ").status_code == 404
