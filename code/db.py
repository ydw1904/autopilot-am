"""Shared SQLite layer for the Airlines Manager tools (circuits, routes, fleet)."""

import sqlite3
import os

DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "db", "am_aircraft.db",
)

_conn = None

EXTRA_CIRCUIT_COLUMNS = [
    ("eco_seats", "INTEGER"),
    ("bus_seats", "INTEGER"),
    ("fir_seats", "INTEGER"),
    ("cargo_seats", "INTEGER"),
    ("waves", "INTEGER"),
    ("daily_rev", "REAL"),
    ("weekly_rev", "REAL"),
    ("investment", "REAL"),  # aircraft cost only (waves * 7 * ac.price)
    ("waves_bought", "INTEGER NOT NULL DEFAULT 0"),
    ("waves_scheduled", "INTEGER NOT NULL DEFAULT 0"),
    ("route_investment", "REAL"),  # SUM of routes.gross_price for circuit's routes
]

def _migrate(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(circuits)").fetchall()}
    needs_route_investment_backfill = "route_investment" not in cols
    for name, sql_type in EXTRA_CIRCUIT_COLUMNS:
        if name not in cols:
            conn.execute(f"ALTER TABLE circuits ADD COLUMN {name} {sql_type}")
    if needs_route_investment_backfill:
        # Sum gross_price from the routes table for each circuit's destinations.
        conn.execute("""
            UPDATE circuits SET route_investment = (
                SELECT COALESCE(SUM(r.gross_price), 0)
                FROM circuit_routes cr
                JOIN routes r ON r.hub_iata = circuits.hub_iata
                            AND r.dest_iata = cr.dest_iata
                WHERE cr.circuit_name = circuits.name
            )
        """)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS circuit_counters ("
        "hub_iata TEXT PRIMARY KEY, last_n INTEGER NOT NULL DEFAULT 0)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS fleet ("
        "aircraft_id INTEGER PRIMARY KEY, "
        "name TEXT, model TEXT, utilization REAL, "
        "hub_iata TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )

    # Check and add is_owned column to routes
    route_cols = {r[1] for r in conn.execute("PRAGMA table_info(routes)").fetchall()}
    if "is_owned" not in route_cols:
        conn.execute("ALTER TABLE routes ADD COLUMN is_owned INTEGER NOT NULL DEFAULT 0")
        conn.execute("""
            UPDATE routes SET is_owned = 1 
            WHERE (hub_iata, dest_iata) IN (
                SELECT c.hub_iata, cr.dest_iata 
                FROM circuit_routes cr 
                JOIN circuits c ON c.name = cr.circuit_name 
                WHERE c.status IN ('bought', 'completed')
            )
        """)
    if "line_id" not in route_cols:
        conn.execute("ALTER TABLE routes ADD COLUMN line_id INTEGER DEFAULT NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_routes_line_id ON routes(line_id) WHERE line_id IS NOT NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_routes_owned ON routes(hub_iata, is_owned)")
    conn.commit()

def get_player_hub_id(hub_iata: str) -> int | None:
    """Resolve a hub IATA to the player's hub_id (from the AM URL)."""
    row = get_db().execute(
        "SELECT hub_id FROM player_hubs WHERE UPPER(hub_iata) = ? LIMIT 1",
        (hub_iata.upper().strip(),)
    ).fetchone()
    return row["hub_id"] if row and row["hub_id"] is not None else None

def load_player_hubs(conn, hub_filter=None):
    """Return [(hub_iata, hub_id), ...] rows, optionally for one hub."""
    sql = "SELECT hub_iata, hub_id FROM player_hubs"
    args = []
    if hub_filter:
        sql += " WHERE hub_iata = ?"
        args.append(hub_filter.upper())
    return conn.execute(sql, args).fetchall()

def mark_route_owned(hub_iata: str, dest_iata: str):
    db = get_db()
    db.execute(
        "UPDATE routes SET is_owned = 1 WHERE hub_iata = ? AND dest_iata = ?",
        (hub_iata.upper(), dest_iata.upper())
    )
    db.commit()

def upsert_line_id(hub_iata: str, dest_iata: str, line_id: int):
    db = get_db()
    db.execute(
        "UPDATE routes SET line_id = ?, is_owned = 1 WHERE hub_iata = ? AND dest_iata = ?",
        (line_id, hub_iata.upper(), dest_iata.upper())
    )
    db.commit()

def get_line_id(hub_iata: str, dest_iata: str) -> int | None:
    db = get_db()
    row = db.execute(
        "SELECT line_id FROM routes WHERE hub_iata = ? AND dest_iata = ?",
        (hub_iata.upper(), dest_iata.upper())
    ).fetchone()
    return row["line_id"] if row and row["line_id"] else None

def get_owned_routes(hub_iata: str) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT dest_iata, line_id, is_owned FROM routes "
        "WHERE hub_iata = ? AND is_owned = 1 ORDER BY dest_iata",
        (hub_iata.upper(),)
    ).fetchall()
    return [dict(r) for r in rows]

def hub_financial_stats(hub_iata: str) -> dict:
    db = get_db()
    hub = hub_iata.upper()
    r = db.execute(
        "SELECT COUNT(*) as total_routes, "
        "COALESCE(SUM(is_owned), 0) as owned_routes, "
        "COALESCE(SUM(CASE WHEN eco_demand > 0 THEN 1 ELSE 0 END), 0) as with_demand, "
        "COALESCE(SUM(CASE WHEN is_owned = 1 AND line_id IS NOT NULL THEN 1 ELSE 0 END), 0) as with_line_id, "
        "COALESCE(SUM(CASE WHEN is_owned = 1 THEN gross_price ELSE 0 END), 0) as route_value, "
        "COALESCE(AVG(distance_km), 0) as avg_dist "
        "FROM routes WHERE hub_iata = ?",
        (hub,)
    ).fetchone()
    base = dict(r)
    c = db.execute(
        "SELECT COUNT(*) as circuits, "
        "COALESCE(SUM(waves), 0) as total_waves, "
        "COALESCE(SUM(weekly_rev), 0) as weekly_rev, "
        "COALESCE(SUM(daily_rev), 0) as daily_rev, "
        "COALESCE(SUM(investment), 0) as aircraft_invested, "
        "COALESCE(SUM(route_investment), 0) as route_invested, "
        "COALESCE(SUM(investment + COALESCE(route_investment, 0)), 0) as total_invested "
        "FROM circuits WHERE hub_iata = ?",
        (hub,)
    ).fetchone()
    base.update(dict(c))
    ac = db.execute(
        "SELECT COUNT(*) as total, "
        "COALESCE(SUM(CASE WHEN utilization = 0 THEN 1 ELSE 0 END), 0) as idle "
        "FROM fleet WHERE hub_iata = ?",
        (hub,)
    ).fetchone()
    base["fleet_total"] = ac["total"] if ac else 0
    base["fleet_idle"] = ac["idle"] if ac else 0
    return base

def get_db():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _migrate(_conn)
    return _conn

def close_db():
    global _conn
    if _conn:
        _conn.close()
        _conn = None

def hub_stats(hub_iata):
    db = get_db()
    row = db.execute(
        "SELECT COUNT(*) as cnt, "
        "COALESCE(SUM(CASE WHEN eco_demand > 0 THEN 1 ELSE 0 END), 0) as with_demand "
        "FROM routes WHERE hub_iata = ?",
        (hub_iata.upper(),)
    ).fetchone()
    return {"total": row["cnt"], "with_demand": row["with_demand"]}

def list_hubs_with_routes():
    db = get_db()
    rows = db.execute(
        "SELECT hub_iata, COUNT(*) as cnt, "
        "MAX(distance_km) as max_dist, AVG(distance_km) as avg_dist "
        "FROM routes WHERE eco_demand > 0 "
        "GROUP BY hub_iata ORDER BY hub_iata"
    ).fetchall()
    return [dict(r) for r in rows]

def list_all_models():
    db = get_db()
    # Unique models from circuits and fleet
    sql = "SELECT aircraft_model as model FROM circuits UNION SELECT model FROM fleet ORDER BY model"
    return [r[0] for r in db.execute(sql).fetchall() if r[0]]

def list_warehouse_models():
    """Models currently present in the warehouse (utilization == 0)."""
    db = get_db()
    rows = db.execute(
        "SELECT DISTINCT model FROM fleet WHERE utilization = 0 ORDER BY model"
    ).fetchall()
    return [r[0] for r in rows if r[0]]

def top_routes(hub_iatas, limit=100):
    db = get_db()
    if isinstance(hub_iatas, str):
        hub_iatas = [hub_iatas]
    hub_iatas = [h.upper() for h in hub_iatas]
    
    placeholders = ",".join("?" * len(hub_iatas))
    rows = db.execute(
        f"SELECT hub_iata, dest_iata, dest_name, distance_km, dest_category, "
        f"eco_demand, bus_demand, fir_demand, cargo_demand, gross_price, is_owned, line_id "
        f"FROM routes WHERE hub_iata IN ({placeholders}) AND eco_demand > 0 "
        f"ORDER BY eco_demand DESC LIMIT ?",
        (*hub_iatas, limit)
    ).fetchall()
    return [dict(r) for r in rows]

def saved_circuits(statuses=None, hubs=None, models=None, model_query=None):
    db = get_db()
    sql = (
        "SELECT c.name, c.hub_iata, c.aircraft_model, "
        "a.model AS aircraft_name, a.icao_code AS aircraft_icao, "
        "c.total_hours, c.waves, "
        "c.eco_seats, c.bus_seats, c.fir_seats, c.cargo_seats, "
        "c.daily_rev, c.weekly_rev, c.investment, c.route_investment, "
        "c.status, c.created_at, c.waves_bought, c.waves_scheduled "
        "FROM circuits c "
        "LEFT JOIN aircraft a ON a.icao_code = c.aircraft_model"
    )
    where, args = [], []
    if statuses:
        if isinstance(statuses, str): statuses = [statuses]
        where.append(f"c.status IN ({','.join('?'*len(statuses))})")
        args.extend(statuses)
    if hubs:
        if isinstance(hubs, str): hubs = [hubs]
        hubs = [h.upper() for h in hubs]
        where.append(f"c.hub_iata IN ({','.join('?'*len(hubs))})")
        args.extend(hubs)
    if models:
        if isinstance(models, str): models = [models]
        where.append(f"c.aircraft_model IN ({','.join('?'*len(models))})")
        args.extend(models)
    if model_query:
        q = model_query.strip().lower()
        if q:
            where.append("(LOWER(c.aircraft_model) LIKE ? OR LOWER(a.model) LIKE ?)")
            args.extend([f"%{q}%", f"%{q}%"])

    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY c.hub_iata, c.name"
    return [dict(r) for r in db.execute(sql, args).fetchall()]


def next_circuit_name(hub_iata: str) -> str:
    """Next slot is max(existing C-number for hub) + 1.
    Restarts at 1 once all circuits for the hub are deleted."""
    db = get_db()
    hub = hub_iata.upper()
    prefix = f"{hub}-C"
    rows = db.execute(
        "SELECT name FROM circuits WHERE hub_iata = ? AND name LIKE ?",
        (hub, f"{prefix}%"),
    ).fetchall()
    max_n = 0
    for (name,) in rows:
        try:
            n = int(name.rsplit("C", 1)[-1])
        except ValueError:
            continue
        if n > max_n:
            max_n = n
    return f"{prefix}{max_n + 1:03d}"


def save_circuit_full(circuit: dict, custom_name: str | None = None) -> str:
    """circuit dict from PlannerScreen._plan(). Returns saved name.

    If custom_name is given, it's used verbatim (uppercased). Raises ValueError
    if a circuit with that name already exists.
    """
    db = get_db()
    hub = circuit["hub"]
    ac = circuit["ac"]
    cfg = circuit.get("cfg") or {}
    waves = circuit.get("waves") or 0
    daily = circuit.get("daily_rev") or 0
    weekly = circuit.get("weekly_rev") or 0
    investment = waves * 7 * ac["price"] if waves else 0
    routes = circuit["routes"]

    if custom_name:
        name = custom_name.upper()
        if db.execute("SELECT 1 FROM circuits WHERE name=?", (name,)).fetchone():
            raise ValueError(f"circuit name {name!r} already exists")
    else:
        name = next_circuit_name(hub)

    # Route investment = sum of gross_price for each destination on this circuit.
    iatas = [r["iata"] for r in routes]
    placeholders = ",".join("?" * len(iatas))
    if iatas:
        row = db.execute(
            f"SELECT COALESCE(SUM(gross_price), 0) FROM routes "
            f"WHERE hub_iata = ? AND dest_iata IN ({placeholders})",
            (hub.upper(), *iatas),
        ).fetchone()
        route_investment = row[0] or 0
    else:
        route_investment = 0
    tot = {k: sum(r[f"{k}_d"] for r in routes) for k in ("eco", "bus", "fir", "cargo")}
    total_hours = circuit.get("total_time", 0)

    db.execute(
        """
        INSERT INTO circuits (
            name, hub_iata, aircraft_model, score, total_hours,
            total_eco, total_bus, total_fir, total_cargo, variance, status,
            eco_seats, bus_seats, fir_seats, cargo_seats,
            waves, daily_rev, weekly_rev, investment, route_investment
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned',
                  ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name, hub.upper(), ac["alias"], daily, total_hours,
            tot["eco"], tot["bus"], tot["fir"], tot["cargo"], 0,
            cfg.get("eco"), cfg.get("bus"), cfg.get("fir"), cfg.get("cargo"),
            waves, daily, weekly, investment, route_investment,
        ),
    )

    db.execute("DELETE FROM circuit_routes WHERE circuit_name = ?", (name,))
    for order, r in enumerate(sorted(routes, key=lambda x: -x["dist"]), 1):
        db.execute(
            """
            INSERT INTO circuit_routes (
                circuit_name, dest_iata, dest_name, distance_km,
                eco_demand, bus_demand, fir_demand, cargo_demand,
                flight_time_rt, route_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name, r["iata"], r.get("name", ""), r["dist"],
                r["eco_d"], r["bus_d"], r["fir_d"], r["cargo_d"],
                r["ft"], order,
            ),
        )

    db.commit()
    return name


def locked_route_iatas(hub: str | None = None, statuses=("completed",)) -> set[str]:
    """IATAs of destinations belonging to circuits in the given statuses.
    Used to auto-exclude already-locked routes from new planning runs."""
    db = get_db()
    placeholders = ",".join("?" * len(statuses))
    sql = (
        f"SELECT DISTINCT cr.dest_iata FROM circuit_routes cr "
        f"JOIN circuits c ON c.name = cr.circuit_name "
        f"WHERE c.status IN ({placeholders})"
    )
    args = list(statuses)
    if hub:
        sql += " AND c.hub_iata = ?"
        args.append(hub.upper())
    return {r[0].upper() for r in db.execute(sql, args).fetchall() if r[0]}


def update_circuit_status(name: str, status: str) -> None:
    db = get_db()
    db.execute(
        "UPDATE circuits SET status=?, updated_at=CURRENT_TIMESTAMP WHERE name=?",
        (status, name),
    )
    db.commit()


def update_circuit_progress(name: str, *, waves_bought: int | None = None,
                            waves_scheduled: int | None = None) -> dict:
    """Update progress counters and re-derive status. Returns the new row state.

    Status rules:
      waves_bought == 0                              -> planned
      0 <= waves_scheduled < planned waves           -> bought
      waves_scheduled >= planned waves               -> completed
    """
    db = get_db()
    row = db.execute(
        "SELECT waves, waves_bought, waves_scheduled FROM circuits WHERE name=?",
        (name,),
    ).fetchone()
    if not row:
        return {}
    planned = row["waves"] or 0
    new_bought = waves_bought if waves_bought is not None else (row["waves_bought"] or 0)
    new_scheduled = waves_scheduled if waves_scheduled is not None else (row["waves_scheduled"] or 0)

    if new_bought <= 0:
        status = "planned"
    elif planned > 0 and new_scheduled >= planned:
        status = "completed"
    else:
        status = "bought"

    db.execute(
        "UPDATE circuits SET waves_bought=?, waves_scheduled=?, status=?, "
        "updated_at=CURRENT_TIMESTAMP WHERE name=?",
        (new_bought, new_scheduled, status, name),
    )
    db.commit()
    return {"waves_bought": new_bought, "waves_scheduled": new_scheduled, "status": status}


def delete_saved_circuit(name: str) -> None:
    db = get_db()
    db.execute("DELETE FROM circuit_routes WHERE circuit_name = ?", (name,))
    db.execute("DELETE FROM circuits WHERE name = ?", (name,))
    db.commit()


def circuit_exists(name: str) -> bool:
    db = get_db()
    row = db.execute("SELECT 1 FROM circuits WHERE name = ?", (name,)).fetchone()
    return row is not None


def rename_circuit_in_db(old_name: str, new_name: str) -> None:
    """Rename a circuit and all its route rows. Caller must ensure new_name is free."""
    db = get_db()
    db.execute("UPDATE circuit_routes SET circuit_name = ? WHERE circuit_name = ?",
               (new_name, old_name))
    db.execute("UPDATE circuits SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE name = ?",
               (new_name, old_name))
    db.commit()


def load_saved_circuit(name: str) -> dict | None:
    """Load a saved circuit back into the in-memory shape used by Circuits tab."""
    db = get_db()
    c = db.execute("SELECT * FROM circuits WHERE name = ?", (name,)).fetchone()
    if not c:
        return None
    rows = db.execute(
        "SELECT cr.*, r.is_owned FROM circuit_routes cr "
        "LEFT JOIN routes r ON r.hub_iata = ? AND r.dest_iata = cr.dest_iata "
        "WHERE cr.circuit_name = ? ORDER BY cr.route_order",
        (c["hub_iata"], name),
    ).fetchall()
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
    from circuit_planner import load_aircraft
    ac = load_aircraft(db, c["aircraft_model"]) or {
        "alias": c["aircraft_model"], "model": c["aircraft_model"],
        "pax": 0, "tonnage": 0, "price": 0, "speed": 0, "range": 0,
    }
    routes = [
        {
            "iata": r["dest_iata"], "name": r["dest_name"] or "",
            "dist": r["distance_km"], "ft": r["flight_time_rt"],
            "eco_d": r["eco_demand"], "bus_d": r["bus_demand"],
            "fir_d": r["fir_demand"], "cargo_d": r["cargo_demand"],
            "is_owned": r["is_owned"] or 0,
        }
        for r in rows
    ]
    cfg = None
    if c["eco_seats"] is not None:
        cfg = {
            "eco": c["eco_seats"] or 0, "bus": c["bus_seats"] or 0,
            "fir": c["fir_seats"] or 0, "cargo": c["cargo_seats"] or 0,
        }
    return {
        "num": int(c["name"].rsplit("C", 1)[-1]) if "C" in c["name"] else 1,
        "name": c["name"], "hub": c["hub_iata"], "ac": ac, "routes": routes,
        "total_time": c["total_hours"] or 0,
        "cfg": cfg, "waves": c["waves"] or 0,
        "daily_rev": c["daily_rev"] or 0,
        "weekly_rev": c["weekly_rev"] or 0,
        "breakdown": None, "p1_score": c["score"] or 0,
        "status": c["status"],
        "investment": c["investment"] or 0,           # aircraft cost
        "route_investment": c["route_investment"] or 0,  # route purchase cost
    }


def upsert_fleet(aircraft_list: list[dict]):
    """Update or insert fleet data from a list of dicts:
    [{id, name, model, util, hub}, ...]
    """
    db = get_db()
    for ac in aircraft_list:
        db.execute(
            "INSERT INTO fleet (aircraft_id, name, model, utilization, hub_iata, updated_at) "
            "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(aircraft_id) DO UPDATE SET "
            "name=excluded.name, model=excluded.model, "
            "utilization=excluded.utilization, hub_iata=excluded.hub_iata, "
            "updated_at=CURRENT_TIMESTAMP",
            (ac["id"], ac["name"], ac["model"], ac["util"], ac["hub"]),
        )
    db.commit()


def get_stored_aircraft(min_util=0, max_util=0, hubs=None, models=None, name_query=None, model_query=None):
    """Return aircraft from the fleet table within the utilization range, optionally filtered by hub, model, and case-insensitive name substring."""
    db = get_db()
    sql = (
        "SELECT f.aircraft_id, f.name, f.model, f.utilization, f.hub_iata, f.updated_at, "
        "a.icao_code AS icao_code "
        "FROM fleet f LEFT JOIN aircraft a ON a.model = f.model "
        "WHERE f.utilization >= ? AND f.utilization <= ?"
    )
    args = [min_util, max_util]

    if hubs:
        if isinstance(hubs, str): hubs = [hubs]
        hubs = [h.upper() for h in hubs]
        sql += f" AND f.hub_iata IN ({','.join('?'*len(hubs))})"
        args.extend(hubs)
    if models:
        if isinstance(models, str): models = [models]
        sql += f" AND f.model IN ({','.join('?'*len(models))})"
        args.extend(models)
    if model_query:
        q = model_query.strip().lower()
        if q:
            sql += " AND (LOWER(f.model) LIKE ? OR LOWER(a.icao_code) LIKE ?)"
            args.extend([f"%{q}%", f"%{q}%"])
    if name_query:
        q = name_query.strip()
        if q:
            sql += " AND LOWER(f.name) LIKE ?"
            args.append(f"%{q.lower()}%")

    sql += " ORDER BY f.model, f.name"
    rows = db.execute(sql, args).fetchall()
    return [dict(r) for r in rows]
