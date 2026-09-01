"""A re-observed fleet row must refresh cargo, not just seats."""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mobile_store  # noqa: E402


def test_observe_fleet_item_updates_payload():
    store = mobile_store.MobileStore(sqlite3.connect(":memory:"))
    row = {"id": 1, "as_id": 2, "al_id": 3, "n": "CGK-C011-059",
           "h_id": 9, "se": 569, "sb": 12, "sf": 1, "sp": 9}
    store.observe_fleet_item(row)
    store.observe_fleet_item({**row, "se": 373, "sb": 8, "sf": 0, "sp": 28})

    got = store.conn.execute(
        "SELECT seats_eco, payload_t FROM mobile_aircraft WHERE aircraft_id=1"
    ).fetchone()
    assert got == (373, 28)
