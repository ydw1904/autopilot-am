"""db.py — schema migration, circuit naming/progress, and route round-trips.

Hermetic: every test runs against a temp SQLite file created with the *pre-
migration* base schema (mirroring `sqlite3 db/am_aircraft.db ".schema ..."`
minus the columns db._migrate is responsible for adding), so get_db() exercises
the real migration path.
"""

import sqlite3

import pytest

import aircraft_aliases as aa
import db as dbmod

# Base schema as it exists *before* db._migrate runs: no eco_seats/waves/... on
# circuits, no is_owned/line_id on routes, no circuit_counters/fleet tables.
BASE_SCHEMA = """
CREATE TABLE circuits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    hub_iata TEXT NOT NULL,
    aircraft_model TEXT NOT NULL,
    score REAL,
    total_hours REAL,
    total_eco INTEGER DEFAULT 0,
    total_bus INTEGER DEFAULT 0,
    total_fir INTEGER DEFAULT 0,
    total_cargo INTEGER DEFAULT 0,
    variance REAL,
    status TEXT DEFAULT 'planned',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE circuit_routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    circuit_name TEXT NOT NULL REFERENCES circuits(name),
    dest_iata TEXT NOT NULL,
    dest_name TEXT,
    distance_km INTEGER,
    eco_demand INTEGER,
    bus_demand INTEGER,
    fir_demand INTEGER,
    cargo_demand INTEGER,
    flight_time_rt REAL,
    route_order INTEGER NOT NULL,
    UNIQUE(circuit_name, dest_iata)
);
CREATE TABLE routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hub_iata TEXT NOT NULL,
    dest_iata TEXT NOT NULL,
    dest_name TEXT,
    dest_country TEXT,
    distance_km INTEGER NOT NULL,
    dest_category INTEGER,
    stars INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    eco_demand INTEGER,
    bus_demand INTEGER,
    fir_demand INTEGER,
    cargo_demand INTEGER,
    gross_price INTEGER,
    UNIQUE(hub_iata, dest_iata)
);
CREATE TABLE player_hubs (hub_iata TEXT PRIMARY KEY, hub_id INTEGER NOT NULL);
CREATE TABLE aircraft (
    model TEXT, manufacturer TEXT, category INTEGER, type TEXT, year INTEGER,
    speed_kmh INTEGER, range_km INTEGER, consumption REAL, wear_speed REAL,
    max_pax INTEGER, max_tonnage REAL, gross_price INTEGER, icao_code TEXT
);
"""


@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = str(tmp_path / "test.db")
    raw = sqlite3.connect(path)
    raw.executescript(BASE_SCHEMA)
    raw.execute(
        "INSERT INTO aircraft (model, category, speed_kmh, range_km, max_pax, "
        "max_tonnage, gross_price, icao_code) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("747-200B", 3, 907, 12700, 452, 95.0, 25_000_000, "B742"),
    )
    raw.execute(
        "INSERT INTO routes (hub_iata, dest_iata, dest_name, dest_country, "
        "distance_km, dest_category, gross_price) VALUES "
        "('HKG', 'CPT', 'Cape Town', 'za', 11840, 3, 1000000)"
    )
    raw.execute(
        "INSERT INTO routes (hub_iata, dest_iata, dest_name, dest_country, "
        "distance_km, dest_category, gross_price) VALUES "
        "('HKG', 'LOS', 'Lagos', 'ng', 11817, 3, 2000000)"
    )
    raw.commit()
    raw.close()

    monkeypatch.setattr(dbmod, "DB", path)
    monkeypatch.setattr(dbmod, "_conn", None)
    # aircraft_aliases reads db.DB at call time, so patching dbmod above covers
    # it; it does cache its index per path, hence the reset.
    aa.reset_cache()

    c = dbmod.get_db()   # runs _migrate on the base schema
    yield c
    dbmod.close_db()
    aa.reset_cache()


# ── migration ───────────────────────────────────────────────────────────────

def test_migrate_adds_circuit_columns_and_tables(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(circuits)")}
    for name, _type in dbmod.EXTRA_CIRCUIT_COLUMNS:
        assert name in cols, f"_migrate did not add circuits.{name}"

    route_cols = {r[1] for r in conn.execute("PRAGMA table_info(routes)")}
    assert "is_owned" in route_cols
    assert "line_id" in route_cols

    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "circuit_counters" in tables
    assert "fleet" in tables


def test_get_db_returns_row_factory_singleton(conn):
    assert dbmod.get_db() is conn
    row = conn.execute("SELECT hub_iata, dest_iata FROM routes LIMIT 1").fetchone()
    assert row["dest_iata"] == "CPT"     # by name
    assert row[0] == "HKG"               # and still by index


# ── next_circuit_name ───────────────────────────────────────────────────────

def test_next_circuit_name_on_empty_hub_is_c001(conn):
    assert dbmod.next_circuit_name("HKG") == "HKG-C001"


def test_next_circuit_name_is_max_plus_one_not_count(conn):
    for name in ("HKG-C001", "HKG-C007"):
        conn.execute(
            "INSERT INTO circuits (name, hub_iata, aircraft_model) VALUES (?, 'HKG', 'B742')",
            (name,),
        )
    conn.commit()

    assert dbmod.next_circuit_name("HKG") == "HKG-C008"


def test_next_circuit_name_is_per_hub(conn):
    conn.execute("INSERT INTO circuits (name, hub_iata, aircraft_model) "
                 "VALUES ('HKG-C005', 'HKG', 'B742')")
    conn.commit()

    assert dbmod.next_circuit_name("MPM") == "MPM-C001"
    assert dbmod.next_circuit_name("hkg") == "HKG-C006"   # case-insensitive hub


# ── update_circuit_progress ─────────────────────────────────────────────────

@pytest.fixture
def circuit(conn):
    conn.execute(
        "INSERT INTO circuits (name, hub_iata, aircraft_model, waves, status) "
        "VALUES ('HKG-C001', 'HKG', 'B742', 5, 'planned')"
    )
    conn.commit()
    return "HKG-C001"


@pytest.mark.parametrize("bought, scheduled, expected", [
    (0, 0, "planned"),      # nothing bought yet
    (5, 0, "bought"),       # bought but unscheduled
    (5, 3, "bought"),       # partially scheduled
    (5, 5, "completed"),    # fully scheduled
    (5, 6, "completed"),    # over-scheduled still counts as done
    (2, 5, "completed"),    # scheduled >= planned wins over partial buy
])
def test_update_circuit_progress_status_rules(conn, circuit, bought, scheduled, expected):
    out = dbmod.update_circuit_progress(
        circuit, waves_bought=bought, waves_scheduled=scheduled)

    assert out["status"] == expected
    assert out["waves_bought"] == bought
    assert out["waves_scheduled"] == scheduled
    row = conn.execute("SELECT status FROM circuits WHERE name=?", (circuit,)).fetchone()
    assert row["status"] == expected


def test_update_circuit_progress_keeps_unspecified_counter(conn, circuit):
    dbmod.update_circuit_progress(circuit, waves_bought=5, waves_scheduled=2)
    out = dbmod.update_circuit_progress(circuit, waves_scheduled=5)

    assert out["waves_bought"] == 5        # not reset to 0
    assert out["status"] == "completed"


def test_update_circuit_progress_unknown_circuit_returns_empty(conn):
    assert dbmod.update_circuit_progress("NOPE-C999", waves_bought=1) == {}


# ── route ownership / line_id ───────────────────────────────────────────────

def test_mark_route_owned(conn):
    assert conn.execute(
        "SELECT is_owned FROM routes WHERE hub_iata='HKG' AND dest_iata='CPT'"
    ).fetchone()["is_owned"] == 0

    dbmod.mark_route_owned("hkg", "cpt")   # lowercase input is normalized

    assert conn.execute(
        "SELECT is_owned FROM routes WHERE hub_iata='HKG' AND dest_iata='CPT'"
    ).fetchone()["is_owned"] == 1


def test_line_id_round_trip(conn):
    assert dbmod.get_line_id("HKG", "CPT") is None

    dbmod.upsert_line_id("HKG", "CPT", 424242)

    assert dbmod.get_line_id("HKG", "CPT") == 424242
    # upsert also flags the route owned
    assert conn.execute(
        "SELECT is_owned FROM routes WHERE hub_iata='HKG' AND dest_iata='CPT'"
    ).fetchone()["is_owned"] == 1


def test_get_line_id_unknown_route_is_none(conn):
    assert dbmod.get_line_id("HKG", "ZZZ") is None


def test_get_owned_routes_lists_only_owned(conn):
    dbmod.upsert_line_id("HKG", "LOS", 111)

    owned = dbmod.get_owned_routes("HKG")

    assert [r["dest_iata"] for r in owned] == ["LOS"]
    assert owned[0]["line_id"] == 111


def test_get_dest_country(conn):
    assert dbmod.get_dest_country("HKG", "CPT") == "za"
    assert dbmod.get_dest_country("hkg", "los") == "ng"   # normalizes case
    assert dbmod.get_dest_country("HKG", "ZZZ") is None


# ── rename ──────────────────────────────────────────────────────────────────

def test_rename_circuit_in_db_moves_circuit_and_its_routes(conn, circuit):
    conn.execute(
        "INSERT INTO circuit_routes (circuit_name, dest_iata, route_order) "
        "VALUES (?, 'CPT', 1)", (circuit,))
    conn.commit()

    dbmod.rename_circuit_in_db(circuit, "HKG-C042")

    assert dbmod.circuit_exists("HKG-C042")
    assert not dbmod.circuit_exists(circuit)
    routes = conn.execute(
        "SELECT circuit_name FROM circuit_routes WHERE dest_iata='CPT'").fetchall()
    assert [r["circuit_name"] for r in routes] == ["HKG-C042"]


# ── save / load round trip ──────────────────────────────────────────────────

def _sample_circuit():
    return {
        "hub": "HKG",
        "ac": {"alias": "B742", "price": 25_000_000},
        "cfg": {"eco": 300, "bus": 40, "fir": 10, "cargo": 5},
        "waves": 2,
        "daily_rev": 1_500_000,
        "weekly_rev": 10_500_000,
        "total_time": 56.5,
        "routes": [
            {"iata": "CPT", "name": "Cape Town", "dist": 11840, "ft": 28.25,
             "eco_d": 7106, "bus_d": 562, "fir_d": 130, "cargo_d": 835},
            {"iata": "LOS", "name": "Lagos", "dist": 11817, "ft": 28.25,
             "eco_d": 7713, "bus_d": 382, "fir_d": 55, "cargo_d": 811},
        ],
    }


def test_save_circuit_full_autonames_and_totals(conn):
    name = dbmod.save_circuit_full(_sample_circuit())

    assert name == "HKG-C001"
    row = conn.execute("SELECT * FROM circuits WHERE name=?", (name,)).fetchone()
    assert row["status"] == "planned"
    assert row["total_eco"] == 7106 + 7713
    assert row["total_cargo"] == 835 + 811
    assert row["waves"] == 2
    # investment = waves * 7 * aircraft price
    assert row["investment"] == 2 * 7 * 25_000_000
    # route_investment = sum of routes.gross_price for CPT + LOS
    assert row["route_investment"] == 3_000_000


def test_save_circuit_full_custom_name_must_be_unique(conn):
    dbmod.save_circuit_full(_sample_circuit(), custom_name="HKG-C900")

    assert dbmod.circuit_exists("HKG-C900")
    with pytest.raises(ValueError, match="already exists"):
        dbmod.save_circuit_full(_sample_circuit(), custom_name="HKG-C900")


def test_save_then_load_round_trip(conn):
    name = dbmod.save_circuit_full(_sample_circuit())

    loaded = dbmod.load_saved_circuit(name)

    assert loaded["name"] == name
    assert loaded["hub"] == "HKG"
    assert loaded["num"] == 1
    assert loaded["waves"] == 2
    assert loaded["status"] == "planned"
    assert loaded["total_time"] == 56.5
    assert loaded["daily_rev"] == 1_500_000
    assert loaded["cfg"] == {"eco": 300, "bus": 40, "fir": 10, "cargo": 5}
    # the B742 alias resolves back to the canonical model via the aircraft table
    assert loaded["ac"]["alias"] == "B742"
    assert loaded["ac"]["model"] == "747-200B"
    assert loaded["ac"]["pax"] == 452
    # routes come back ordered by route_order (longest distance first)
    assert [r["iata"] for r in loaded["routes"]] == ["CPT", "LOS"]
    assert loaded["routes"][0]["eco_d"] == 7106
    assert loaded["routes"][0]["dist"] == 11840


def test_load_saved_circuit_unknown_name_is_none(conn):
    assert dbmod.load_saved_circuit("NOPE-C999") is None


def test_delete_saved_circuit_removes_routes_too(conn):
    name = dbmod.save_circuit_full(_sample_circuit())

    dbmod.delete_saved_circuit(name)

    assert not dbmod.circuit_exists(name)
    assert conn.execute(
        "SELECT COUNT(*) c FROM circuit_routes WHERE circuit_name=?", (name,)
    ).fetchone()["c"] == 0
