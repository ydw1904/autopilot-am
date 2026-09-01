"""Hangar workbench: the guards that stand between a click and a live write."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

import api_server
import db as dbmod
import mobile_store

PROFILE = {
    "id": 101,
    "name": "FRA-C001-001",
    "hub": {"id": 9480309, "name": "FRA - Frankfurt Airport"},
    "price": 238600000,
    "utilization": 100,
    "wear": 9.7,
    "age": 2,
    "mark": "4/5",
    "seats": {"eco": 287, "business": 85, "first": 0},
    "payload": 16,
    "model": {"id": 14, "name": "A330-300", "seats": {"total": 440}, "payload": 56.0},
    "skins": {"id": 2, "name": "A330-300 - (Manufacturer livery)"},
    "sellPrice": 119300000,
    "minAuctionSellPrice": 130000000,
    "maxAuctionSellPrice": 238000000,
    "binThreshold": 900000000,
}


class FakeClient:
    """Enough AMClient to run the endpoints without touching the game."""

    def __init__(self, profile):
        self.profile = dict(profile)
        self.calls = []
        self.refuse_reconfigure = False

    def aircraft(self, aircraft_id):
        return dict(self.profile)

    def reconfigure(self, aircraft_id, *, name, eco, bus, first, payload):
        self.calls.append(("reconfigure", aircraft_id, name, eco, bus, first, payload))
        if self.refuse_reconfigure:
            return {"message": "aircraft.reconfigure.success"}
        self.profile["name"] = name
        self.profile["seats"] = {"eco": eco, "business": bus, "first": first}
        self.profile["payload"] = payload
        return {"message": "aircraft.reconfigure.success"}

    def assign_hub(self, aircraft_id, hub_id):
        self.calls.append(("assign_hub", aircraft_id, hub_id))
        self.profile["hub"] = {"id": hub_id, "name": "MPM - Maputo"}
        return {"message": "aircraft.hubAssigned"}

    def apply_skin(self, skin_id, aircraft_ids):
        self.calls.append(("apply_skin", skin_id, tuple(aircraft_ids)))
        self.profile["skins"] = {"id": skin_id, "name": "A330-300 - Spirit"}
        return {"message": "skin applied"}

    def sell_for_scrap(self, aircraft_id):
        self.calls.append(("sell_for_scrap", aircraft_id))
        return {"message": "aircraft.sold"}


@pytest.fixture
def hangar(tmp_path, monkeypatch):
    path = str(tmp_path / "hangar_test.db")
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE player_hubs (hub_iata TEXT PRIMARY KEY, hub_id INTEGER NOT NULL)")
    raw.execute("CREATE TABLE aircraft (model TEXT, icao_code TEXT, category INTEGER, "
                "speed_kmh INTEGER, range_km INTEGER, max_pax INTEGER, "
                "max_tonnage REAL, gross_price INTEGER)")
    raw.execute("INSERT INTO player_hubs VALUES ('FRA', 9480309), ('MPM', 9535579)")
    raw.execute("INSERT INTO aircraft VALUES ('A330-300', 'A333', 6, 871, 11750, 440, 56, 238600000)")
    raw.commit()
    raw.close()

    monkeypatch.setattr(dbmod, "DB", path)
    monkeypatch.setattr(dbmod, "_conn", None)
    mobile_store.MobileStore(dbmod.get_db())
    dbmod.upsert_fleet([{"id": 101, "name": "FRA-C001-001", "model": "A330-300",
                         "util": 100.0, "hub": "FRA", "skin_id": 2}])

    client = FakeClient(PROFILE)
    monkeypatch.setattr(api_server, "_hangar_call", lambda fn: fn(client))
    monkeypatch.setattr(api_server, "fetch_missing_skin_images", lambda *a, **k: (0, 0))
    return TestClient(api_server.app), client


def fleet_row(aircraft_id):
    return dbmod.get_db().execute(
        "SELECT name, hub_iata, skin_id FROM fleet WHERE aircraft_id = ?",
        (aircraft_id,)).fetchone()


def test_profile_is_flattened_and_the_cached_fleet_row_follows(hangar):
    api, _ = hangar
    view = api.get("/api/hangar/101").json()

    assert view["hub_iata"] == "FRA"
    assert view["seats"] == {"eco": 287, "bus": 85, "first": 0}
    assert view["sale"]["bin_threshold"] == 900000000
    assert (view["icao_code"], view["category"], view["range_km"]) == ("A333", 6, 11750)
    assert [hub["hub_iata"] for hub in view["hubs"]] == ["FRA", "MPM"]
    assert tuple(fleet_row(101)) == ("FRA-C001-001", "FRA", 2)


def test_renaming_echoes_the_current_seats_so_it_stays_free(hangar):
    api, client = hangar
    view = api.post("/api/hangar/101/name", json={"name": "FRA-C009-001"}).json()

    assert client.calls == [("reconfigure", 101, "FRA-C009-001", 287, 85, 0, 16)]
    assert view["name"] == "FRA-C009-001"
    assert fleet_row(101)["name"] == "FRA-C009-001"


def test_a_silently_refused_configuration_reports_an_error(hangar):
    api, client = hangar
    client.refuse_reconfigure = True

    response = api.post("/api/hangar/101/seats",
                        json={"eco": 200, "bus": 20, "first": 10, "payload": 5})

    assert response.status_code == 502
    assert response.json()["detail"] == "Reconfigure failed: the game kept the previous configuration"


def test_a_hub_move_rewrites_the_cached_hub(hangar):
    api, client = hangar
    view = api.post("/api/hangar/101/hub", json={"hub_iata": "MPM"}).json()

    assert client.calls == [("assign_hub", 101, 9535579)]
    assert view["hub_iata"] == "MPM"
    assert fleet_row(101)["hub_iata"] == "MPM"


def test_an_unknown_hub_is_refused_before_any_write(hangar):
    api, client = hangar
    assert api.post("/api/hangar/101/hub", json={"hub_iata": "XXX"}).status_code == 400
    assert client.calls == []


def test_painting_over_an_awarded_livery_needs_an_explicit_confirmation(hangar):
    api, client = hangar
    client.profile["skins"] = {"id": 4697861, "name": "A330-300 - Challenge Copa"}

    refused = api.post("/api/hangar/101/livery", json={"skin_id": 4661635})
    assert refused.status_code == 409
    assert client.calls == []

    api.post("/api/hangar/101/livery",
             json={"skin_id": 4661635, "confirm_overwrite": True})
    assert client.calls == [("apply_skin", 4661635, (101,))]


def test_a_manufacturer_livery_is_repainted_without_a_confirmation(hangar):
    api, client = hangar
    api.post("/api/hangar/101/livery", json={"skin_id": 4661635})
    assert client.calls == [("apply_skin", 4661635, (101,))]


def test_scrapping_requires_the_typed_name_and_drops_the_fleet_row(hangar):
    api, client = hangar

    mistyped = api.post("/api/hangar/101/scrap", json={"confirm_name": "FRA-C001-002"})
    assert mistyped.status_code == 400
    assert client.calls == []
    assert fleet_row(101) is not None

    scrapped = api.post("/api/hangar/101/scrap", json={"confirm_name": "FRA-C001-001"})
    assert client.calls == [("sell_for_scrap", 101)]
    assert scrapped.json()["scrapped_for"] == 119300000
    assert fleet_row(101) is None
