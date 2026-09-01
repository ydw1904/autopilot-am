"""db.get_network_snapshot — the joins the Network tab depends on.

The interesting part is that nothing in the schema links an aircraft to a
circuit: the join is the `<HUB>-C<NNN>-<MMM>` name prefix, and route ownership
comes from `routes`, not `circuit_routes`. Both are easy to get subtly wrong.
"""

import db as dbmod
from test_db import conn  # noqa: F401  (pytest fixture)


def _seed(c):
    c.execute("INSERT INTO circuits (name, hub_iata, aircraft_model, status, "
              "waves, waves_bought, waves_scheduled, weekly_rev, eco_seats) "
              "VALUES ('HKG-C001', 'HKG', '747-200B', 'completed', 3, 3, 2, 1000, 300)")
    c.execute("INSERT INTO circuits (name, hub_iata, aircraft_model, status, "
              "waves, waves_bought, waves_scheduled, weekly_rev) "
              "VALUES ('HKG-C002', 'HKG', '747-200B', 'planned', 2, 0, 0, 500)")
    # CPT is owned in `routes`, LOS is not; both belong to C001.
    c.execute("UPDATE routes SET is_owned = 1 WHERE dest_iata = 'CPT'")
    for order, iata in enumerate(("CPT", "LOS"), start=1):
        c.execute("INSERT INTO circuit_routes (circuit_name, dest_iata, "
                  "route_order) VALUES ('HKG-C001', ?, ?)", (iata, order))
    for name, util in (("HKG-C001-001", 90.0), ("HKG-C001-002", 0.0),
                       ("HKG-C0011-001", 50.0), ("HKG-C002-001", 40.0)):
        c.execute("INSERT INTO fleet (aircraft_id, name, model, utilization, "
                  "hub_iata) VALUES (?, ?, '747-200B', ?, 'HKG')",
                  (abs(hash(name)) % 100000, name, util))
    c.commit()


def test_network_snapshot_joins(conn):  # noqa: F811
    _seed(conn)
    snap = dbmod.get_network_snapshot()
    by_name = {c["name"]: c for c in snap["circuits"]}

    c001 = by_name["HKG-C001"]
    # `HKG-C0011-001` starts with the circuit name but is a *different* circuit,
    # so the join must anchor on the '-' separator, not a bare prefix.
    assert c001["aircraft"] == 2
    assert c001["idle_aircraft"] == 1
    assert c001["routes_owned"] == 1
    assert len(c001["routes"]) == 2
    assert [r["is_owned"] for r in c001["routes"]] == [True, False]

    assert by_name["HKG-C002"]["aircraft"] == 1
    assert by_name["HKG-C002"]["routes"] == []

    totals = snap["totals"]
    assert (totals["circuits"], totals["operating"], totals["planned"]) == (2, 1, 1)
    assert totals["operating_weekly_rev"] == 1000
    assert totals["planned_weekly_rev"] == 500
    # One wave bought and never scheduled: capital producing nothing.
    assert totals["unscheduled_waves"] == 1
    assert (totals["routes_owned"], totals["routes_known"]) == (1, 2)

    hub = next(h for h in snap["hubs"] if h["hub_iata"] == "HKG")
    assert (hub["circuits"], hub["operating"], hub["aircraft"]) == (2, 1, 3)


def test_ops_freshness_skips_absent_tables(conn):  # noqa: F811
    labels = {row["table"] for row in dbmod.get_ops_freshness()}
    assert "fleet" in labels and "routes" in labels
    assert "mobile_skins" not in labels     # never created by the base schema
