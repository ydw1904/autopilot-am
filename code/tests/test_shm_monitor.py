"""Read-only SHM monitor snapshot tests."""

import db as dbmod
import shm_watcher


def test_shm_monitor_is_empty_before_watcher_tables_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB", str(tmp_path / "empty.db"))
    monkeypatch.setattr(dbmod, "_conn", None)
    try:
        snapshot = dbmod.get_shm_monitor_snapshot()
        assert snapshot["status"] == {"observing": False, "last_activity": None}
        assert snapshot["summary"]["active_watches"] == 0
        assert snapshot["watches"] == []
    finally:
        dbmod.close_db()


def test_shm_monitor_reports_current_activity(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB", str(tmp_path / "monitor.db"))
    monkeypatch.setattr(dbmod, "_conn", None)
    try:
        conn = dbmod.get_db()
        conn.executescript(shm_watcher.SCHEMA)
        conn.execute("""
            INSERT INTO shm_watch
                (skin_id, model_id, label, max_price, source)
            VALUES (10, 153, 'Pack Livery', 2000000000, 'shop-pack:auto'),
                   (20, 137, 'Manual Livery', 1000000000, 'manual')
        """)
        conn.execute("""
            INSERT INTO shm_sightings
                (auction_id, skin_id, model_id, skin_name, current_price,
                 bin_price, time_left_s, bids, is_own, last_seen)
            VALUES (99, 10, 153, 'Pack Livery', 500000000,
                    750000000, 3600, 2, 0, datetime('now'))
        """)
        conn.execute("""
            INSERT INTO shm_model_checks
                (model_id, last_checked, checks, truncated)
            VALUES (153, datetime('now'), 4, 0)
        """)
        conn.execute("""
            INSERT INTO shm_buys
                (auction_id, skin_id, model_id, skin_name, bin_price,
                 est_cost, dry_run, confirmed)
            VALUES (99, 10, 153, 'Pack Livery', 750000000,
                    900000000, 1, NULL)
        """)
        conn.execute("""
            INSERT INTO fleet
                (aircraft_id, name, model, utilization, hub_iata, updated_at,
                 skin_id)
            VALUES (1001, 'FRA-PACK-1', 'X777-9', 0, 'FRA', datetime('now'), 10)
        """)
        conn.commit()

        snapshot = dbmod.get_shm_monitor_snapshot()
        assert snapshot["status"]["observing"] is True
        assert snapshot["summary"] == {
                "active_watches": 2,
                "paid_pack_watches": 1,
                "armed_watches": 0,
            "watched_models": 2,
            "matched_sightings": 1,
            "real_buys_today": 0,
            "dry_runs_today": 1,
        }
        # Owned liveries sort last even when they are paid-pack targets.
        assert [w["skin_id"] for w in snapshot["watches"]] == [20, 10]
        owned = snapshot["watches"][1]
        assert owned["is_owned"] is True
        assert owned["owned_count"] == 1
        assert owned["cheapest_seen"] == 750000000
        assert owned["last_price_seen"] == 750000000
        assert snapshot["sightings"][0]["auction_id"] == 99
        assert snapshot["model_checks"][0]["checks"] == 4
        assert snapshot["decisions"][0]["dry_run"] == 1
    finally:
        dbmod.close_db()


def test_shm_watch_price_cap_is_editable(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB", str(tmp_path / "cap.db"))
    monkeypatch.setattr(dbmod, "_conn", None)
    try:
        conn = dbmod.get_db()
        conn.executescript(shm_watcher.SCHEMA)
        conn.execute("INSERT INTO shm_watch (skin_id, label) VALUES (10, 'A')")
        conn.commit()

        assert shm_watcher.set_watch_max_price(conn, 10, 2_000_000_000)
        assert conn.execute(
            "SELECT max_price FROM shm_watch WHERE skin_id=10").fetchone()[0] == 2_000_000_000
        # Clearing the field means "no cap", not a zero cap that buys nothing.
        assert shm_watcher.set_watch_max_price(conn, 10, None)
        assert conn.execute(
            "SELECT max_price FROM shm_watch WHERE skin_id=10").fetchone()[0] is None
        assert not shm_watcher.set_watch_max_price(conn, 999, 1)
    finally:
        dbmod.close_db()
