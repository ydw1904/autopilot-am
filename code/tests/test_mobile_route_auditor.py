import sqlite3

import pytest

from mobile_api import AMClient
from mobile_route_auditor import (audit_budget, audit_lines, audit_routes,
                                  candidates, demand_from_audit,
                                  great_circle_km, sync_world_rows)


def make_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE routes (hub_iata TEXT, dest_iata TEXT, "
               "dest_country TEXT, is_owned INTEGER, eco_demand INTEGER, "
               "bus_demand INTEGER, fir_demand INTEGER, cargo_demand INTEGER, "
               "line_id INTEGER)")
    db.executemany("INSERT INTO routes VALUES (?,?,?,?,?,?,?,?,?)", [
        ("LAX", "JFK", "us", 0, None, None, None, None, None),
        ("LAX", "LHR", "gb", 0, 4000, 800, 200, 500, None),
        ("LAX", "SEA", "us", 1, None, None, None, None, None),
    ])
    return db


class FakeClient:
    def hub_pricing_page(self, hub_id, page):
        return {"freeAudits": 3820, "ressources": {"dollar": 8_630_931_151}}

    def world(self):
        return {"airportList": [{"id": 99, "i": "JFK"}]}

    def external_route_audit(self, airport_id, hub_id):
        assert (airport_id, hub_id) == (99, 123)
        return {"demand": {"eco": 5000, "bus": 900, "first": 250, "cargo": 600}}

    def __init__(self):
        self.audited = []

    def internal_line_audit(self, line_id):
        self.audited.append(line_id)
        return {"demand": {"eco": 7000, "bus": 950, "first": 260, "cargo": 610}}


def test_unpurchased_mobile_audit_updates_only_missing_demand():
    db = make_db()
    rows = candidates(db, "LAX")
    with pytest.raises(ValueError, match="spend cash"):
        audit_routes(db, FakeClient(), "LAX", 123, rows)
    result = audit_routes(db, FakeClient(), "LAX", 123, rows, allow_paid=True)

    assert result == {"updated": ["JFK"], "failed": [], "stopped": None}
    assert audit_budget(FakeClient(), 123) == (3820, 8_630_931_151)
    assert tuple(db.execute(
        "SELECT eco_demand,bus_demand,fir_demand,cargo_demand FROM routes "
        "WHERE dest_iata='JFK'").fetchone()) == (5000, 900, 250, 600)
    assert demand_from_audit({"demand": {
        "eco": "1", "bus": 2, "first": 3, "cargo": 4}}) == (1, 2, 3, 4)

    client = object.__new__(AMClient)
    client._request = lambda method, endpoint: {
        "audit": {"demand": {"eco": 1}}} if (
            method, endpoint) == ("POST", "audit/external/new/99/123") else {}
    assert client.external_route_audit(99, 123)["demand"]["eco"] == 1


def test_internal_audit_skips_lines_with_no_route_row_before_spending():
    db = make_db()
    db.execute("CREATE TABLE routes_demand_snapshot (hub_iata, dest_iata, "
               "line_id, snapshot_at, old_eco, old_bus, old_fir, old_cargo, "
               "new_eco, new_bus, new_fir, new_cargo)")
    lines = [
        {"id": 11, "aTwoName": "LHR", "audit": {"demand": {"eco": 4000}}},
        {"id": 22, "aTwoName": "ZZZ", "audit": {}},   # no routes row: no coupon
    ]
    client = FakeClient()
    result = audit_lines(db, client, "LAX", 123, lines)

    assert result["updated"] == ["LHR"]
    assert client.audited == [11]                      # ZZZ never audited
    assert [f["iata"] for f in result["failed"]] == ["ZZZ"]
    assert tuple(db.execute(
        "SELECT eco_demand, is_owned, line_id FROM routes "
        "WHERE dest_iata='LHR'").fetchone()) == (7000, 1, 11)
    assert db.execute("SELECT old_eco, new_eco FROM routes_demand_snapshot"
                      ).fetchone()[:] == (4000, 7000)


# Real coordinates and the distances the game itself reports for them.
WORLD = {"countryList": [{"id": 63, "c": "fi"}, {"id": 99, "c": "id"},
                         {"id": 206, "c": "us"}],
         "airportList": [
             {"i": "CGK", "ty": 99, "av": True, "cat": 10, "ct": "Jakarta",
              "n": "Soekarno-Hatta", "lt": "-6.125567", "lg": "106.655897"},
             {"i": "KEM", "ty": 63, "av": True, "cat": 7, "ct": "Kemi",
              "n": "Tornio Airport", "lt": "65.781944", "lg": "24.599167"},
             {"i": "SJY", "ty": 63, "av": True, "cat": 3, "ct": "Seinajoki",
              "n": "Seinajoki Airport", "lt": "62.692119", "lg": "22.832328"},
             # `ty` here is California, a region id countryList never carries;
             # only `cty` names the country.
             {"i": "LAX", "ty": 229, "cty": 206, "av": True, "cat": 10,
              "ct": "Los Angeles (CA)", "n": "Los Angeles Airport",
              "lt": "33.943921", "lg": "-118.404293"},
         ]}


def test_world_sync_adds_missing_rows_and_repairs_scraped_country_and_name():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE routes (hub_iata TEXT, dest_iata TEXT, dest_name "
               "TEXT, dest_country TEXT, distance_km INTEGER, dest_category "
               "INTEGER, UNIQUE(hub_iata, dest_iata))")
    # The shape the old HTML scrape left behind: city inside dest_country,
    # dest_name empty. SJY is absent entirely — the gap this sync closes.
    db.execute("INSERT INTO routes VALUES ('CGK','KEM','','FinlandKemi/Tornio',"
               "10271, 7)")

    assert sync_world_rows(db, WORLD, "CGK") == {
        "rows_added": 2, "countries_fixed": 1, "names_filled": 1}
    assert db.execute("SELECT dest_country FROM routes WHERE dest_iata='LAX'"
                      ).fetchone()["dest_country"] == "us"

    kem = db.execute("SELECT * FROM routes WHERE dest_iata='KEM'").fetchone()
    assert (kem["dest_country"], kem["dest_name"]) == ("fi", "Kemi - Tornio Airport")
    sjy = db.execute("SELECT * FROM routes WHERE dest_iata='SJY'").fetchone()
    # 10301 km and category 3 are what the game's own route listing shows.
    assert (sjy["distance_km"], sjy["dest_category"], sjy["dest_country"]) == (10301, 3, "fi")
    assert great_circle_km(WORLD["airportList"][0], WORLD["airportList"][1]) == 10271
    # Idempotent: a second pass is a no-op, so it is safe to run before a sweep.
    assert sync_world_rows(db, WORLD, "CGK") == {
        "rows_added": 0, "countries_fixed": 0, "names_filled": 0}
