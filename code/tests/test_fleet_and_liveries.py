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
        INSERT INTO mobile_skins (skin_id, model_id, name, picture_path, source)
        VALUES (2, 2, 'A330-300 - (Manufacturer livery)', '/common/images/Aircrafts/skins/big/a330-300.png', 'manufacturer'),
               (4638064, 30, '737-400 - Challenge Turkish Airways', '/common/images/Aircrafts/skins/big/737-400-challenge-turkish-airways.png', 'playrion'),
               (4621811, 24, 'A321XLR - Challenge Project Blossom', '/common/images/Aircrafts/skins/big/a321xlr-challenge-project-blossom.png', 'playrion'),
               (4245917, 19, 'LH-A388', '/common/images/painterPublic/skins/big/p-9358830-1-673deb3894c28.png', 'market');
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


def test_upsert_fleet_prunes_hubs_not_seen_in_the_new_sync(fleet_conn):
    # A hub-scoped sync that no longer sees aircraft 103 (sold/scrapped in
    # game) must drop it, while leaving the untouched FRA hub alone.
    dbmod.upsert_fleet(
        [{"id": 104, "name": "MPM-C002-001", "model": "747-200B", "util": 85.0,
          "hub": "MPM", "skin_id": 4638064, "skin_img": "737-400-challenge-turkish-airways.png"}],
        prune_hubs=["MPM"],
    )
    ids = {p["aircraft_id"] for p in dbmod.get_fleet_aircraft()}
    assert ids == {101, 102, 104}

    # A full sync across all synced hubs that returns no aircraft at all
    # clears every one of those hubs rather than leaving stale rows behind.
    dbmod.upsert_fleet([], prune_hubs=["FRA", "MPM"])
    assert dbmod.get_fleet_aircraft() == []


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


def test_get_fleet_aircraft_page_reports_total_and_searches_across_fields(fleet_conn):
    page = dbmod.get_fleet_aircraft_page(query="A330", limit=1, offset=0)
    assert page["total"] == 2
    assert page["limit"] == 1
    assert len(page["items"]) == 1

    hub_page = dbmod.get_fleet_aircraft_page(query="MPM", limit=50)
    assert hub_page["total"] == 2
    assert {row["hub_iata"] for row in hub_page["items"]} == {"MPM"}


def test_aircraft_custom_tags_are_many_to_many_searchable_and_sync_safe(fleet_conn):
    updated = dbmod.update_aircraft_tags(
        [101, 103], add=["SHM sale", " Storage "]
    )
    assert updated == {
        101: ["SHM sale", "Storage"],
        103: ["SHM sale", "Storage"],
    }

    # A third independent tag and a case-insensitive duplicate both keep the
    # relationship many-to-many without duplicating a chip.
    dbmod.update_aircraft_tags([101], add=["Collector", "shm SALE"])
    plane = next(row for row in dbmod.get_fleet_aircraft()
                 if row["aircraft_id"] == 101)
    assert plane["tags"] == ["Collector", "SHM sale", "Storage"]

    assert {row["aircraft_id"] for row in dbmod.get_fleet_aircraft(tag="storage")} == {101, 103}
    assert dbmod.get_fleet_aircraft_page(query="collector")["total"] == 1
    assert dbmod.get_aircraft_tag_counts() == [
        {"tag": "Collector", "count": 1},
        {"tag": "SHM sale", "count": 2},
        {"tag": "Storage", "count": 2},
    ]

    # A normal sync updates game-owned fields without replacing local tags.
    dbmod.upsert_fleet([
        {"id": 101, "name": "FRA-C001-001", "model": "A330-300", "util": 90.0,
         "hub": "FRA", "skin_id": 2, "skin_img": "a330-300.png"},
    ])
    synced = next(row for row in dbmod.get_fleet_aircraft()
                  if row["aircraft_id"] == 101)
    assert synced["tags"] == ["Collector", "SHM sale", "Storage"]

    dbmod.update_aircraft_tags([101], remove=["storage"])
    after_remove = next(row for row in dbmod.get_fleet_aircraft()
                        if row["aircraft_id"] == 101)
    assert after_remove["tags"] == ["Collector", "SHM sale"]


def test_aircraft_tags_are_pruned_when_the_aircraft_leaves_the_synced_fleet(fleet_conn):
    dbmod.update_aircraft_tags([103], add=["Sell"])
    dbmod.upsert_fleet(
        [{"id": 104, "name": "MPM-C002-001", "model": "747-200B", "util": 85.0,
          "hub": "MPM", "skin_id": 4638064,
          "skin_img": "737-400-challenge-turkish-airways.png"}],
        prune_hubs=["MPM"],
    )
    assert fleet_conn.execute(
        "SELECT COUNT(*) FROM aircraft_tags WHERE aircraft_id = 103"
    ).fetchone()[0] == 0


def test_aircraft_profile_purchase_date_is_cached_and_joined_into_fleet(fleet_conn):
    store = mobile_store.MobileStore(fleet_conn)
    store.observe_aircraft_profile({
        "id": 101,
        "name": "FRA-C001-001",
        "model": {"id": 14, "name": "A330-300"},
        "hub": {"id": 9480309, "name": "Frankfurt"},
        "seats": {"eco": 199, "business": 110, "first": 47},
        "payload": 27,
        "price": 238600000,
        "purchasedAt": {
            "date": "2026-04-27 14:57:35.000000",
            "timezone": "UTC",
        },
    })
    store.commit()

    aircraft = next(item for item in dbmod.get_fleet_aircraft()
                    if item["aircraft_id"] == 101)
    assert aircraft["purchased_at"] == "2026-04-27 14:57:35.000000"


def test_command_center_snapshot_surfaces_work_and_honest_freshness(fleet_conn):
    fleet_conn.execute(
        "CREATE TABLE circuits (hub_iata TEXT, status TEXT, weekly_rev REAL)"
    )
    fleet_conn.executemany(
        "INSERT INTO circuits VALUES (?, ?, ?)",
        [("FRA", "planned", 5000), ("FRA", "completed", 2000)],
    )
    fleet_conn.execute(
        "CREATE TABLE routes (is_owned INTEGER NOT NULL DEFAULT 0)"
    )
    fleet_conn.executemany("INSERT INTO routes VALUES (?)", [(1,), (0,), (0,)])
    fleet_conn.execute(
        "UPDATE fleet SET updated_at = '2026-08-20 00:00:00' WHERE aircraft_id = 103"
    )
    fleet_conn.execute(
        "UPDATE fleet SET updated_at = '2026-08-23 00:00:00' WHERE aircraft_id != 103"
    )
    fleet_conn.commit()

    snapshot = dbmod.get_command_center_snapshot(
        browser_connected=False, mobile_configured=True
    )
    assert snapshot["portfolio"]["planned_weekly_rev"] == 5000
    assert snapshot["portfolio"]["operating_weekly_rev"] == 2000
    assert snapshot["data_health"]["stale_aircraft"] == 1
    assert snapshot["data_health"]["routes"] == 3
    assert snapshot["data_health"]["owned_routes"] == 1
    assert snapshot["hubs"][0]["circuits"] == 2
    assert {alert["id"] for alert in snapshot["alerts"]} >= {"browser", "idle", "planned", "stale"}
    browser_alert = next(alert for alert in snapshot["alerts"] if alert["id"] == "browser")
    assert browser_alert["tone"] == "warning"
    assert "Mobile fleet sync" in browser_alert["detail"]
    assert snapshot["status"]["mobile_configured"] is True


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


def test_get_livery_collection_user_created_flag_and_filter(fleet_conn):
    # By default the player-designed market livery is present and flagged.
    with_market = dbmod.get_livery_collection(include_manufacturer=False)
    lh = next(s for s in with_market if s["skin_id"] == 4245917)
    assert lh["is_user_created"] is True
    official = next(s for s in with_market if s["skin_id"] == 4638064)
    assert official["is_user_created"] is False

    # Opting out drops market liveries but keeps official ones.
    without_market = dbmod.get_livery_collection(
        include_manufacturer=False, include_user_created=False
    )
    skin_ids = [s["skin_id"] for s in without_market]
    assert 4245917 not in skin_ids
    assert 4638064 in skin_ids
    assert 4621811 in skin_ids


def test_livery_tags_name_each_feed_and_leave_factory_paint_out(fleet_conn):
    # A livery handed out by two feeds at once (a challenge ladder AND a
    # travel-card offer) earns a chip for each, while an offer whose aircraft
    # wears the model's own paint contributes no livery at all — its skin is a
    # manufacturer one and the album never lists those.
    store = mobile_store.MobileStore(fleet_conn)
    store.record_challenge({
        "id": 900, "title": "Turkish Airways Challenge!",
        "challengeType": "km.lines",
        "objectives": [{
            "id": 1, "goal": 0,
            "rewards": [{"id": 11, "effectType": "aircraft", "rarity": 4,
                         "label": "737-400 - Challenge Turkish Airways",
                         "aircraftModelId": 30,
                         "skin": {"id": 4638064,
                                  "name": "737-400 - Challenge Turkish Airways"}}],
        }],
    })
    store.record_shop_offer({
        "id": 7001, "title": "737-400 - Challenge Turkish Airways",
        "template": "aircraft", "purchaseCost": 25000, "purchaseCurrency": "tc",
        "content": [{"effectType": "aircraft",
                     "label": "737-400 - Challenge Turkish Airways",
                     "skin": {"id": 4638064, "type": 1}}],
    })
    store.record_shop_offer({
        "id": 7002, "title": "Commander Pack", "template": "pack",
        "purchaseCost": 9.99, "purchaseCurrency": "realMoney",
        "content": [{"effectType": "aircraft",
                     "label": "A330-300 - (Manufacturer livery)",
                     "skin": {"id": 2, "type": 0}}],
    })
    store.commit()

    album = {s["skin_id"]: s for s in dbmod.get_livery_collection()}
    assert 2 not in album, "a pack's factory paint is not a collectable livery"

    kinds = {t["kind"]: t for t in album[4638064]["tags"]}
    assert set(kinds) == {"challenge", "shop_tc"}
    assert kinds["challenge"]["label"] == "Turkish Airways"
    assert "25,000 travel cards" in kinds["shop_tc"]["title"]

    # The plane-only pack is still recorded — it just tags a livery the album
    # filters out, so nothing claims that pack sells a paint scheme.
    manufacturer_tags = dbmod.get_livery_tags()[2]
    assert [t["kind"] for t in manufacturer_tags] == ["shop_pack"]


def test_get_skin_image_bytes(fleet_conn):
    data = dbmod.get_skin_image_bytes(4638064)
    assert data is not None
    assert b"PNG" in data

    missing = dbmod.get_skin_image_bytes(999999)
    assert missing is None


def test_haul_classification_and_filters(fleet_conn):
    # A freighter, so the cargo tab has something that is also a haul plane.
    fleet_conn.execute(
        "INSERT INTO aircraft (model, category, range_km, max_pax, max_tonnage, type, icao_code) "
        "VALUES ('747-400F', 8, 8230, 0, 124.0, 'Cargo', 'B744')"
    )
    dbmod.upsert_fleet([
        {"id": 105, "name": "FRA-CARGO-01", "model": "747-400F", "util": 50.0,
         "hub": "FRA", "skin_id": None, "skin_img": None},
    ])

    by_name = {p["name"]: p for p in dbmod.get_fleet_aircraft()}
    assert by_name["FRA-C001-001"]["haul"] == "medium"   # A330-300, cat 6
    assert by_name["MPM-IDLE-01"]["haul"] == "short"     # 747-200B, cat 3 in fixture
    assert by_name["FRA-CARGO-01"]["haul"] == "long"     # cat 8
    assert by_name["FRA-CARGO-01"]["is_cargo"] == 1
    assert by_name["FRA-C001-001"]["is_cargo"] == 0

    assert len(dbmod.get_fleet_aircraft(haul="all")) == 5
    assert len(dbmod.get_fleet_aircraft(haul="short")) == 2
    assert len(dbmod.get_fleet_aircraft(haul="medium")) == 2
    assert len(dbmod.get_fleet_aircraft(haul="long")) == 1
    cargo = dbmod.get_fleet_aircraft(haul="cargo")
    assert [p["name"] for p in cargo] == ["FRA-CARGO-01"]

    # Haul stacks with the other filters rather than replacing them.
    assert len(dbmod.get_fleet_aircraft(haul="short", hubs=["MPM"])) == 2
    assert len(dbmod.get_fleet_aircraft(haul="short", hubs=["FRA"])) == 0

    hauls = dbmod.get_fleet_summary_stats()["hauls"]
    assert hauls == {"all": 5, "short": 2, "medium": 2, "long": 1, "cargo": 1, "unknown": 0}


def test_get_fleet_aircraft_unknown_model_has_no_haul(fleet_conn):
    dbmod.upsert_fleet([
        {"id": 106, "name": "FRA-MYSTERY-01", "model": "Not-In-Specs", "util": 0.0,
         "hub": "FRA", "skin_id": None, "skin_img": None},
    ])
    mystery = next(p for p in dbmod.get_fleet_aircraft() if p["name"] == "FRA-MYSTERY-01")
    assert mystery["haul"] is None
    assert mystery["is_cargo"] == 0
    # Only reachable from the "all" tab.
    assert dbmod.get_fleet_summary_stats()["hauls"]["unknown"] == 1
    for tab in ("short", "medium", "long", "cargo"):
        assert all(p["name"] != "FRA-MYSTERY-01" for p in dbmod.get_fleet_aircraft(haul=tab))


def test_daily_fleet_liveries(fleet_conn):
    picks = dbmod.get_daily_fleet_liveries(count=3, day="2026-08-23")
    # Only the Turkish livery is both special and actually flown by the fleet.
    assert [p["skin_id"] for p in picks] == [4638064]
    item = picks[0]
    assert item["fleet_count"] == 2
    assert item["day"] == "2026-08-23"
    assert item["sample_aircraft"]["name"] == "MPM-C002-001"
    assert item["hubs"] == [{"hub_iata": "MPM", "count": 2}]

    # Same day in, same order out; the day is the only thing that shuffles it.
    assert dbmod.get_daily_fleet_liveries(count=3, day="2026-08-23") == picks


def test_daily_fleet_liveries_list_every_hub_flying_the_livery(fleet_conn):
    # The showcase card names hubs, not the sample aircraft's hub, so a livery
    # split across bases has to report all of them, busiest first.
    dbmod.upsert_fleet([
        {"id": 103, "name": "MPM-IDLE-01", "model": "747-200B", "util": 0.0, "hub": "MPM",
         "skin_id": 4638064, "skin_img": "737-400-challenge-turkish-airways.png"},
        {"id": 104, "name": "MPM-C002-001", "model": "747-200B", "util": 85.0, "hub": "MPM",
         "skin_id": 4638064, "skin_img": "737-400-challenge-turkish-airways.png"},
        {"id": 105, "name": "GRU-TK-01", "model": "747-200B", "util": 40.0, "hub": "GRU",
         "skin_id": 4638064, "skin_img": "737-400-challenge-turkish-airways.png"},
        {"id": 106, "name": "GIG-TK-01", "model": "747-200B", "util": 40.0, "hub": "GIG",
         "skin_id": 4638064, "skin_img": "737-400-challenge-turkish-airways.png"},
        {"id": 107, "name": "GIG-TK-02", "model": "747-200B", "util": 40.0, "hub": "GIG",
         "skin_id": 4638064, "skin_img": "737-400-challenge-turkish-airways.png"},
    ])

    item = dbmod.get_daily_fleet_liveries(count=3, day="2026-08-23")[0]
    assert item["fleet_count"] == 5
    assert item["hubs"] == [
        {"hub_iata": "GIG", "count": 2},
        {"hub_iata": "MPM", "count": 2},
        {"hub_iata": "GRU", "count": 1},
    ]
    assert sum(hub["count"] for hub in item["hubs"]) == item["fleet_count"]


def test_daily_fleet_liveries_rotate_by_day(fleet_conn):
    fleet_conn.execute("""
        INSERT INTO mobile_skins (skin_id, model_id, name, picture_path)
        VALUES (5000001, 2, 'A330-300 - Retro One', '/skins/big/a330-300-retro-one.png'),
               (5000002, 2, 'A330-300 - Retro Two', '/skins/big/a330-300-retro-two.png'),
               (5000003, 2, 'A330-300 - Retro Three', '/skins/big/a330-300-retro-three.png')
    """)
    dbmod.upsert_fleet([
        {"id": 201, "name": "FRA-R1", "model": "A330-300", "util": 10.0, "hub": "FRA",
         "skin_id": 5000001, "skin_img": "a330-300-retro-one.png"},
        {"id": 202, "name": "FRA-R2", "model": "A330-300", "util": 10.0, "hub": "FRA",
         "skin_id": 5000002, "skin_img": "a330-300-retro-two.png"},
        {"id": 203, "name": "FRA-R3", "model": "A330-300", "util": 10.0, "hub": "FRA",
         "skin_id": 5000003, "skin_img": "a330-300-retro-three.png"},
    ])

    days = {d: [p["skin_id"] for p in dbmod.get_daily_fleet_liveries(3, day=d)]
            for d in ("2026-08-23", "2026-08-24", "2026-08-25")}
    for picked in days.values():
        assert len(picked) == 3
        assert len(set(picked)) == 3
    assert len({tuple(v) for v in days.values()}) > 1, "the pick should change across days"


def test_hub_country_codes(fleet_conn):
    # No hubs catalog in this fixture DB: the fallback map still answers, and
    # the missing table must not raise.
    assert dbmod.get_hub_country_code("FRA") == "de"
    assert dbmod.get_hub_country_code("mpm") == "mz"
    assert dbmod.get_hub_country_code("ZZZ") is None

    stats = dbmod.get_fleet_summary_stats()
    assert {h["hub_iata"]: h["country_code"] for h in stats["hubs"]} == {"FRA": "de", "MPM": "mz"}


def test_hub_country_code_prefers_the_hubs_catalog(fleet_conn):
    fleet_conn.execute(
        "CREATE TABLE hubs (hub_id INTEGER PRIMARY KEY, iata TEXT, name TEXT, "
        "country_code TEXT, category INTEGER, categories_accepted TEXT, price INTEGER, airport_tax INTEGER)"
    )
    fleet_conn.execute(
        "INSERT INTO hubs (hub_id, iata, name, country_code) VALUES (1, 'MPM', 'Maputo', 'MZ'), "
        "(2, 'XXX', 'Somewhere', 'pt')"
    )
    assert dbmod.get_hub_country_code("XXX") == "pt"   # catalog-only hub
    assert dbmod.get_hub_country_code("MPM") == "mz"   # catalog wins, normalized
    assert dbmod.get_hub_country_code("FRA") == "de"   # not in catalog -> fallback
