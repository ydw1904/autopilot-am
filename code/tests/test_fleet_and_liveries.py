"""Unit tests for Fleet Management and Livery Collection data layers."""

import sqlite3
import pytest
import db as dbmod
import mobile_store


@pytest.fixture
def fleet_conn(tmp_path, monkeypatch):
    path = str(tmp_path / "fleet_test.db")
    raw = sqlite3.connect(path)
    # Create base schema
    raw.execute("CREATE TABLE player_hubs (hub_iata TEXT PRIMARY KEY, hub_id INTEGER NOT NULL);")
    raw.execute("""
        CREATE TABLE aircraft (
            model TEXT, manufacturer TEXT, category INTEGER, type TEXT, year INTEGER,
            speed_kmh INTEGER, range_km INTEGER, consumption REAL, wear_speed REAL,
            max_pax INTEGER, max_tonnage REAL, gross_price INTEGER, icao_code TEXT
        );
    """)
    raw.execute("""
        INSERT INTO aircraft (model, category, speed_kmh, range_km, max_pax, max_tonnage, gross_price, icao_code)
        VALUES ('747-200B', 3, 907, 12700, 452, 95.0, 25000000, 'B742'),
               ('A330-300', 6, 871, 11750, 440, 56.0, 238600000, 'A333');
    """)
    raw.execute("INSERT INTO player_hubs VALUES ('FRA', 9480309), ('MPM', 9535579);")
    raw.commit()
    raw.close()

    monkeypatch.setattr(dbmod, "DB", path)
    monkeypatch.setattr(dbmod, "_conn", None)

    c = dbmod.get_db()
    # Initialize mobile schema
    mobile_store.MobileStore(c)

    # Insert sample fleet
    fleet_data = [
        {"id": 101, "name": "FRA-C001-001", "model": "A330-300", "util": 100.0, "hub": "FRA", "skin_id": 2, "skin_img": "a330-300.png"},
        {"id": 102, "name": "FRA-C001-002", "model": "A330-300", "util": 100.0, "hub": "FRA", "skin_id": 2, "skin_img": "a330-300.png"},
        {"id": 103, "name": "MPM-IDLE-01", "model": "747-200B", "util": 0.0, "hub": "MPM", "skin_id": 4638064, "skin_img": "737-400-challenge-turkish-airways.png"},
        {"id": 104, "name": "MPM-C002-001", "model": "747-200B", "util": 85.0, "hub": "MPM", "skin_id": 4638064, "skin_img": "737-400-challenge-turkish-airways.png"},
    ]
    dbmod.upsert_fleet(fleet_data)

    # Insert sample skins into mobile_skins
    c.execute("""
        INSERT INTO mobile_skins (skin_id, model_id, name, picture_path, rarity)
        VALUES (2, 2, 'A330-300 - (Manufacturer livery)', '/common/images/Aircrafts/skins/big/a330-300.png', 3),
               (4638064, 30, '737-400 - Challenge Turkish Airways', '/common/images/Aircrafts/skins/big/737-400-challenge-turkish-airways.png', 4),
               (4621811, 24, 'A321XLR - Challenge Project Blossom', '/common/images/Aircrafts/skins/big/a321xlr-challenge-project-blossom.png', 4);
    """)

    # Insert dummy image blob for skin 4638064
    fake_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRtest"
    c.execute("INSERT INTO mobile_skin_images (skin_id, size, png, byte_len) VALUES (?, ?, ?, ?)",
              (4638064, "big", sqlite3.Binary(fake_png), len(fake_png)))

    c.commit()
    yield c
    dbmod.close_db()


def test_get_fleet_summary_stats(fleet_conn):
    stats = dbmod.get_fleet_summary_stats()
    assert stats["total"] == 4
    assert stats["active"] == 3
    assert stats["idle"] == 1
    assert stats["special_skin_count"] == 2  # plane 103 and 104 with Turkish Airways skin


def test_get_fleet_aircraft_filters(fleet_conn):
    # Filter by hub
    fra_planes = dbmod.get_fleet_aircraft(hubs=["FRA"])
    assert len(fra_planes) == 2
    assert all(p["hub_iata"] == "FRA" for p in fra_planes)

    # Filter by idle
    idle_planes = dbmod.get_fleet_aircraft(min_util=0, max_util=0)
    assert len(idle_planes) == 1
    assert idle_planes[0]["name"] == "MPM-IDLE-01"

    # Filter by special skin
    special_planes = dbmod.get_fleet_aircraft(skin_filter="special")
    assert len(special_planes) == 2
    assert all(p["skin_id"] == 4638064 for p in special_planes)

    # Filter by name query
    c001_planes = dbmod.get_fleet_aircraft(name_query="C001")
    assert len(c001_planes) == 2


def test_get_livery_collection_excludes_manufacturer(fleet_conn):
    collection = dbmod.get_livery_collection(include_manufacturer=False)
    # Manufacturer skin (skin_id 2) should NOT be in the collection
    skin_ids = [s["skin_id"] for s in collection]
    assert 2 not in skin_ids
    assert 4638064 in skin_ids
    assert 4621811 in skin_ids


def test_get_livery_collection_ownership_and_aircraft_names(fleet_conn):
    collection = dbmod.get_livery_collection(include_manufacturer=False)
    turkish = next(s for s in collection if s["skin_id"] == 4638064)
    assert turkish["is_owned"] is True
    assert turkish["owned_count"] == 2
    assert "MPM-IDLE-01" in turkish["aircraft_names"]
    assert "MPM-C002-001" in turkish["aircraft_names"]

    blossom = next(s for s in collection if s["skin_id"] == 4621811)
    assert blossom["is_owned"] is False
    assert blossom["owned_count"] == 0
    assert blossom["aircraft_names"] == []


def test_get_skin_image_bytes(fleet_conn):
    data = dbmod.get_skin_image_bytes(4638064)
    assert data is not None
    assert b"PNG" in data

    missing = dbmod.get_skin_image_bytes(999999)
    assert missing is None
