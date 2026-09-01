"""/api/pricing/{hub}/apply — the guards between a click and a live price write.

`mobile_pricer.price_hub` is exercised by test_mobile_pricing.py; what is
tested here is only what the endpoint adds on top of it.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

import api_server
import db as dbmod


@pytest.fixture
def api(tmp_path, monkeypatch):
    path = str(tmp_path / "pricing_test.db")
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE player_hubs (hub_iata TEXT PRIMARY KEY, hub_id INTEGER NOT NULL)")
    raw.execute("CREATE TABLE circuits (name TEXT PRIMARY KEY, hub_iata TEXT, "
                "aircraft_model TEXT, status TEXT)")
    raw.execute("CREATE TABLE circuit_routes (circuit_name TEXT, dest_iata TEXT, route_order INTEGER)")
    raw.execute("INSERT INTO player_hubs VALUES ('MPM', 9535579)")
    raw.execute("INSERT INTO circuits VALUES ('MPM-C001', 'MPM', 'B742', 'completed')")
    raw.executemany("INSERT INTO circuit_routes VALUES ('MPM-C001', ?, ?)",
                    [("PVG", 1), ("PEK", 2)])
    raw.commit()
    raw.close()

    monkeypatch.setattr(dbmod, "DB", path)
    monkeypatch.setattr(dbmod, "_conn", None)
    monkeypatch.setattr(api_server, "_hangar_call", lambda fn: fn(object()))

    seen = {}

    def fake_price_hub(hub, **kwargs):
        seen.update(hub=hub, **kwargs)
        return {"hub": hub, "hub_id": 9535579, "backend": "mobile",
                "mode": kwargs["mode"], "dry_run": kwargs["dry_run"],
                "applied": 0, "routes": [], "counts": {}}

    import mobile_pricer
    monkeypatch.setattr(mobile_pricer, "price_hub", fake_price_hub)
    yield TestClient(api_server.app), seen
    dbmod.close_db()


def test_a_live_write_must_name_its_routes(api):
    client, seen = api
    refused = client.post("/api/pricing/MPM/apply", json={"dry_run": False})

    assert refused.status_code == 400
    assert "Preview first" in refused.json()["detail"]
    assert not seen               # never reached the pricer

    ok = client.post("/api/pricing/MPM/apply",
                     json={"dry_run": False, "routes": ["pvg"]})
    assert ok.status_code == 200
    assert seen["only"] == {"PVG"} and seen["dry_run"] is False


def test_a_whole_hub_run_is_allowed_only_as_a_preview(api):
    client, seen = api
    assert client.post("/api/pricing/MPM/apply", json={"dry_run": True}).status_code == 200
    assert seen["only"] is None and seen["dry_run"] is True


@pytest.mark.parametrize("pct", [0, 10, 500])
def test_an_out_of_range_multiplier_is_refused(api, pct):
    client, seen = api
    response = client.post("/api/pricing/MPM/apply",
                           json={"mode": "percent", "pct": pct, "dry_run": True})
    assert response.status_code == 422
    assert not seen


def test_an_unknown_mode_is_refused(api):
    client, _ = api
    assert client.post("/api/pricing/MPM/apply",
                       json={"mode": "guess", "dry_run": True}).status_code == 422


def test_circuit_scope_intersects_with_an_explicit_route_list(api):
    client, seen = api
    client.post("/api/pricing/MPM/apply",
                json={"circuit": "MPM-C001", "routes": ["PVG", "HKG"], "dry_run": True})
    # HKG is not on the circuit, so it drops out rather than widening the run.
    assert seen["only"] == {"PVG"}


def test_an_unknown_circuit_is_a_404(api):
    client, seen = api
    response = client.post("/api/pricing/MPM/apply",
                           json={"circuit": "MPM-C999", "dry_run": True})
    assert response.status_code == 404
    assert not seen
