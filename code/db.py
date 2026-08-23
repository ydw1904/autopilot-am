"""Shared SQLite layer for the Airlines Manager tools (circuits, routes, fleet)."""

import datetime
import hashlib
import sqlite3
import os
import threading

# Imported as a module, not `from aircraft_aliases import resolve`: that module
# reads db.DB for its default path, so the two import each other. Module objects
# tolerate the cycle in either import order; resolve is looked up at call time.
import aircraft_aliases

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

# The livery: `skin_img` is the picture filename the planning payload carries for
# every aircraft, `skin_id` the numeric id it resolves to via `mobile_skins`.
EXTRA_FLEET_COLUMNS = [
    ("skin_id", "INTEGER"),
    ("skin_img", "TEXT"),
]

def _migrate(conn):
    # Tolerate a fresh/empty DB: the base tables are created by other scripts,
    # so skip column migrations for any table that doesn't exist yet.
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "circuits" in tables:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(circuits)").fetchall()}
        needs_route_investment_backfill = "route_investment" not in cols
        for name, sql_type in EXTRA_CIRCUIT_COLUMNS:
            if name not in cols:
                conn.execute(f"ALTER TABLE circuits ADD COLUMN {name} {sql_type}")
        if needs_route_investment_backfill and "circuit_routes" in tables and "routes" in tables:
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
    fleet_cols = {r[1] for r in conn.execute("PRAGMA table_info(fleet)").fetchall()}
    for name, sql_type in EXTRA_FLEET_COLUMNS:
        if name not in fleet_cols:
            conn.execute(f"ALTER TABLE fleet ADD COLUMN {name} {sql_type}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fleet_skin ON fleet(skin_id)")

    if "routes" not in tables:
        conn.commit()
        return
    # Check and add is_owned column to routes
    route_cols = {r[1] for r in conn.execute("PRAGMA table_info(routes)").fetchall()}
    if "is_owned" not in route_cols:
        conn.execute("ALTER TABLE routes ADD COLUMN is_owned INTEGER NOT NULL DEFAULT 0")
        if "circuits" in tables and "circuit_routes" in tables:
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

def get_dest_country(hub_iata: str, dest_iata: str) -> str | None:
    """Country slug (lowercase) for a route, or None."""
    if not os.path.exists(DB):
        return None
    row = get_db().execute(
        "SELECT dest_country FROM routes "
        "WHERE UPPER(hub_iata)=? AND UPPER(dest_iata)=? LIMIT 1",
        (hub_iata.upper().strip(), dest_iata.upper().strip()),
    ).fetchone()
    return row[0].lower() if row and row[0] else None

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

class _LockedConn:
    """Thread-safe facade over the shared sqlite3 connection.

    The MCP server runs tools on a thread pool, and a sqlite3 connection is
    not safe for concurrent use even with check_same_thread=False. Each call
    is serialized. Multi-statement transactions can still interleave between
    calls — write paths here commit immediately, so keep it that way.
    """

    def __init__(self, raw):
        self._raw = raw
        self._lock = threading.RLock()

    def execute(self, *args, **kwargs):
        with self._lock:
            return self._raw.execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        with self._lock:
            return self._raw.executemany(*args, **kwargs)

    def commit(self):
        with self._lock:
            self._raw.commit()

    def close(self):
        with self._lock:
            self._raw.close()

    def __getattr__(self, name):
        return getattr(self._raw, name)


def get_db():
    global _conn
    if _conn is None:
        raw = sqlite3.connect(DB, check_same_thread=False)
        raw.row_factory = sqlite3.Row
        _migrate(raw)
        _conn = _LockedConn(raw)
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
    db.executemany(
        """
        INSERT INTO circuit_routes (
            circuit_name, dest_iata, dest_name, distance_km,
            eco_demand, bus_demand, fir_demand, cargo_demand,
            flight_time_rt, route_order
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                name, r["iata"], r.get("name", ""), r["dist"],
                r["eco_d"], r["bus_d"], r["fir_d"], r["cargo_d"],
                r["ft"], order,
            )
            for order, r in enumerate(sorted(routes, key=lambda x: -x["dist"]), 1)
        ],
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


def load_aircraft(db, name):
    """Look up an aircraft by alias/ICAO/model name. Returns a dict, or None.

    Lives here rather than in circuit_planner because it is a plain DB lookup:
    load_saved_circuit needs it, and the data layer must not import the planner.
    circuit_planner re-exports it, so `from circuit_planner import load_aircraft`
    still works.
    """
    r = aircraft_aliases.resolve(name)
    resolved = r.model if r.status == "ok" else name  # keep LIKE fallback for partials
    row = db.execute(
        "SELECT model, category, speed_kmh, range_km, max_pax, max_tonnage, gross_price "
        "FROM aircraft WHERE model=? OR model LIKE ?",
        (resolved, f"%{resolved}%")
    ).fetchone()
    if not row:
        return None
    return {
        "alias": name.upper(), "model": row[0], "cat": row[1],
        "speed": row[2], "range": row[3], "pax": row[4], "tonnage": row[5],
        "price": row[6] or 0,
    }


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
    try:
        num = int(c["name"].rsplit("C", 1)[-1])
    except ValueError:
        num = 1  # custom names need not end in a C-number
    return {
        "num": num,
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


def resolve_skin_ids(aircraft_list: list[dict]) -> tuple[int, int]:
    """Fill each dict's `skin_id` from the `skin_img` filename it was scraped with.

    The planning payload identifies an aircraft's livery by picture only, so the
    numeric id comes from `mobile_skins` — matched on the filename, since the
    two differ just in the size directory (`skins/small/` vs `skins/big/`).
    A handful of basenames are shared by two skins (one artwork file reused
    across models, e.g. `b777-300.png` for both the 777-300 and the 777-300ER);
    for those the id recorded by the mobile fleet sync for that exact aircraft
    wins, then the candidate whose skin name is that model's ("777-300ER -
    (Manufacturer livery)"). Anything still ambiguous is left unset rather than
    guessed. Aircraft with no `skin_img` fall back to the mobile sync too.

    Returns (resolved, unresolved).
    """
    db = get_db()
    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

    by_img: dict[str, list[tuple[int, str]]] = {}
    if "mobile_skins" in tables:
        for skin_id, path, skin_name in db.execute(
            "SELECT skin_id, picture_path, name FROM mobile_skins "
            "WHERE picture_path IS NOT NULL"
        ).fetchall():
            # skin names read "<model> - <livery>"; keep the model half
            model = (skin_name or "").split(" - ")[0].strip().lower()
            by_img.setdefault(path.rsplit("/", 1)[-1], []).append((skin_id, model))

    from_mobile: dict[int, int] = {}
    if "mobile_aircraft" in tables:
        from_mobile = {r[0]: r[1] for r in db.execute(
            "SELECT aircraft_id, skin_id FROM mobile_aircraft "
            "WHERE skin_id IS NOT NULL").fetchall()}

    resolved = 0
    for ac in aircraft_list:
        candidates = by_img.get(ac.get("skin_img") or "", [])
        ids = [sid for sid, _ in candidates]
        hint = from_mobile.get(ac.get("id"))
        model = (ac.get("model") or "").strip().lower()
        by_model = [sid for sid, m in candidates if m and m == model]
        if len(candidates) == 1:
            ac["skin_id"] = ids[0]
        elif hint is not None and (not ids or hint in ids):
            ac["skin_id"] = hint
        elif len(by_model) == 1:
            ac["skin_id"] = by_model[0]
        else:
            ac["skin_id"] = None
        resolved += ac["skin_id"] is not None
    return resolved, len(aircraft_list) - resolved


def upsert_fleet(aircraft_list: list[dict]):
    """Update or insert fleet data from a list of dicts:
    [{id, name, model, util, hub, skin_id?, skin_img?}, ...]
    """
    db = get_db()
    db.executemany(
        "INSERT INTO fleet (aircraft_id, name, model, utilization, hub_iata, "
        "skin_id, skin_img, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(aircraft_id) DO UPDATE SET "
        "name=excluded.name, model=excluded.model, "
        "utilization=excluded.utilization, hub_iata=excluded.hub_iata, "
        # a sync that could not identify the livery leaves the last known one
        "skin_id=COALESCE(excluded.skin_id, fleet.skin_id), "
        "skin_img=COALESCE(excluded.skin_img, fleet.skin_img), "
        "updated_at=CURRENT_TIMESTAMP",
        [(ac["id"], ac["name"], ac["model"], ac["util"], ac["hub"],
          ac.get("skin_id"), ac.get("skin_img") or None)
         for ac in aircraft_list],
    )
    db.commit()


def get_stored_aircraft(min_util=0, max_util=0, hubs=None, models=None, name_query=None, model_query=None):
    """Return aircraft from the fleet table within the utilization range, optionally filtered by hub, model, and case-insensitive name substring."""
    db = get_db()
    sql = (
        "SELECT f.aircraft_id, f.name, f.model, f.utilization, f.hub_iata, f.updated_at, "
        "f.skin_id, f.skin_img, a.icao_code AS icao_code "
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


# Haul classes follow the game's own split of the aircraft shop, which
# aircraft_buyer.CATEGORY_TO_HAUL already encodes: cat 1-3 short, 4-6 medium,
# 7-10 long. Cargo cuts across all three (a.type = 'Cargo'), so it is a peer
# tab rather than a fourth haul. Models missing from the `aircraft` table get
# NULL and stay visible only under "all".
HAUL_CASE_SQL = """
    CASE
        WHEN a.category BETWEEN 1 AND 3  THEN 'short'
        WHEN a.category BETWEEN 4 AND 6  THEN 'medium'
        WHEN a.category BETWEEN 7 AND 10 THEN 'long'
    END
"""

IS_CARGO_SQL = "(CASE WHEN a.type = 'Cargo' THEN 1 ELSE 0 END)"


def haul_filter_sql(haul):
    """SQL fragment for a haul tab. Returns '' for 'all'/None (no filtering)."""
    h = (haul or "all").strip().lower()
    if h in ("", "all"):
        return ""
    if h == "cargo":
        return f" AND {IS_CARGO_SQL} = 1"
    if h in ("short", "medium", "long"):
        return f" AND {HAUL_CASE_SQL.strip()} = '{h}'"
    return ""


def get_fleet_aircraft(
    hubs=None,
    models=None,
    min_util=None,
    max_util=None,
    name_query=None,
    model_query=None,
    skin_filter=None,
    skin_id=None,
    haul=None,
    sort_by="name",
    limit=None,
    offset=None,
):
    """Return fully joined aircraft records from fleet, aircraft specs, mobile_skins, and mobile_aircraft."""
    db = get_db()
    sql = """
        SELECT f.aircraft_id, f.name, f.model, f.utilization, f.hub_iata, f.updated_at,
               f.skin_id, f.skin_img,
               a.category, a.speed_kmh, a.range_km, a.max_pax, a.max_tonnage, a.gross_price, a.icao_code,
               a.type AS ac_type,
               """ + HAUL_CASE_SQL + """ AS haul,
               """ + IS_CARGO_SQL + """ AS is_cargo,
               s.name AS skin_name, s.picture_path AS skin_picture_path,
               m.seats_eco, m.seats_bus, m.seats_first, m.payload_t, m.wear
        FROM fleet f
        LEFT JOIN aircraft a ON a.model = f.model
        LEFT JOIN mobile_skins s ON s.skin_id = f.skin_id
        LEFT JOIN mobile_aircraft m ON m.aircraft_id = f.aircraft_id
        WHERE 1=1
    """
    args = []

    if hubs:
        if isinstance(hubs, str):
            hubs = [hubs]
        hubs = [h.upper().strip() for h in hubs if h and h.strip()]
        if hubs:
            sql += f" AND f.hub_iata IN ({','.join('?'*len(hubs))})"
            args.extend(hubs)

    if models:
        if isinstance(models, str):
            models = [models]
        models = [m.strip() for m in models if m and m.strip()]
        if models:
            sql += f" AND f.model IN ({','.join('?'*len(models))})"
            args.extend(models)

    if min_util is not None:
        sql += " AND f.utilization >= ?"
        args.append(float(min_util))

    if max_util is not None:
        sql += " AND f.utilization <= ?"
        args.append(float(max_util))

    if name_query:
        q = name_query.strip().lower()
        if q:
            sql += " AND LOWER(f.name) LIKE ?"
            args.append(f"%{q}%")

    if model_query:
        q = model_query.strip().lower()
        if q:
            sql += " AND (LOWER(f.model) LIKE ? OR LOWER(COALESCE(a.icao_code, '')) LIKE ?)"
            args.extend([f"%{q}%", f"%{q}%"])

    sql += haul_filter_sql(haul)

    if skin_id is not None:
        sql += " AND f.skin_id = ?"
        args.append(int(skin_id))
    elif skin_filter == "special":
        sql += """ AND f.skin_id IS NOT NULL AND NOT (
            s.name LIKE '%(Manufacturer%' OR s.name LIKE '%Manufacturer%'
            OR s.picture_path LIKE '%Manufacturer%' OR s.picture_path LIKE '%constructeur%'
            OR f.skin_img LIKE '%Manufacturer%' OR f.skin_img LIKE '%constructeur%'
        )"""
    elif skin_filter == "manufacturer":
        sql += """ AND (
            s.name LIKE '%(Manufacturer%' OR s.name LIKE '%Manufacturer%'
            OR s.picture_path LIKE '%Manufacturer%' OR s.picture_path LIKE '%constructeur%'
            OR f.skin_img LIKE '%Manufacturer%' OR f.skin_img LIKE '%constructeur%'
            OR f.skin_id IS NULL
        )"""

    if sort_by == "util_asc":
        sql += " ORDER BY f.utilization ASC, f.name ASC"
    elif sort_by == "util_desc":
        sql += " ORDER BY f.utilization DESC, f.name ASC"
    elif sort_by == "hub":
        sql += " ORDER BY f.hub_iata ASC, f.model ASC, f.name ASC"
    elif sort_by == "model":
        sql += " ORDER BY f.model ASC, f.name ASC"
    elif sort_by == "wear_desc":
        sql += " ORDER BY COALESCE(m.wear, 0) DESC, f.name ASC"
    else:
        sql += " ORDER BY f.name ASC"

    if limit is not None:
        sql += f" LIMIT {int(limit)}"
        if offset is not None:
            sql += f" OFFSET {int(offset)}"

    rows = db.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


# The `hubs` catalog carries country_code, but only for hubs the game still
# lists for sale — most owned hubs are missing from it, so this fills the gaps.
# Codes are ISO 3166-1 alpha-2; the UI turns them into flags, so no emoji here.
HUB_COUNTRY_FALLBACK = {
    "AKU": "cn", "CDG": "fr", "CGK": "id", "CPT": "za", "DME": "ru",
    "FRA": "de", "GIG": "br", "GRU": "br", "GYD": "az", "HKG": "hk",
    "HND": "jp", "JNB": "za", "JRO": "tz", "LAX": "us", "LHR": "gb",
    "MPM": "mz", "ORY": "fr", "PBM": "sr", "PEK": "cn", "PER": "au",
    "RVN": "fi", "SYD": "au", "ZRH": "ch",
    # Common hubs not owned yet, so a new hub usually still gets a flag.
    "AMS": "nl", "ARN": "se", "ATH": "gr", "BKK": "th", "BOM": "in",
    "BRU": "be", "CAI": "eg", "CPH": "dk", "DEL": "in", "DUB": "ie",
    "DXB": "ae", "EZE": "ar", "FCO": "it", "HEL": "fi", "ICN": "kr",
    "IST": "tr", "JFK": "us", "LIS": "pt", "LOS": "ng", "MAD": "es",
    "MEX": "mx", "NBO": "ke", "ORD": "us", "OSL": "no", "PRG": "cz",
    "SIN": "sg", "VIE": "at", "WAW": "pl", "YYZ": "ca",
}


def get_hub_country_code(iata: str) -> str | None:
    """ISO 3166-1 alpha-2 code for a hub, from the hubs catalog or the fallback."""
    code = (iata or "").upper().strip()
    if not code:
        return None
    db = get_db()
    # The hubs catalog is populated by a separate scrape, so a fresh DB may not
    # have the table at all — fall back rather than blow up.
    has_table = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='hubs' LIMIT 1"
    ).fetchone()
    if has_table:
        row = db.execute(
            "SELECT country_code FROM hubs WHERE UPPER(iata) = ? AND country_code IS NOT NULL LIMIT 1",
            (code,),
        ).fetchone()
        if row and row["country_code"]:
            return row["country_code"].lower()
    return HUB_COUNTRY_FALLBACK.get(code)


def get_fleet_summary_stats() -> dict:
    """Return aggregated fleet statistics for overview KPIs."""
    db = get_db()
    total_row = db.execute(
        "SELECT COUNT(*) as total, "
        "SUM(CASE WHEN utilization = 0 THEN 1 ELSE 0 END) as idle, "
        "SUM(CASE WHEN utilization > 0 THEN 1 ELSE 0 END) as active, "
        "AVG(utilization) as avg_util FROM fleet"
    ).fetchone()

    total = total_row["total"] or 0
    idle = total_row["idle"] or 0
    active = total_row["active"] or 0
    avg_util = round(total_row["avg_util"] or 0.0, 1)

    hub_rows = db.execute(
        "SELECT hub_iata, COUNT(*) as count, "
        "SUM(CASE WHEN utilization = 0 THEN 1 ELSE 0 END) as idle "
        "FROM fleet GROUP BY hub_iata ORDER BY count DESC"
    ).fetchall()
    hubs = [dict(r) for r in hub_rows]
    for h in hubs:
        h["country_code"] = get_hub_country_code(h["hub_iata"])

    model_rows = db.execute(
        "SELECT model, COUNT(*) as count, "
        "SUM(CASE WHEN utilization = 0 THEN 1 ELSE 0 END) as idle "
        "FROM fleet GROUP BY model ORDER BY count DESC"
    ).fetchall()
    models = [dict(r) for r in model_rows]

    special_skin_row = db.execute("""
        SELECT COUNT(*) as cnt FROM fleet f
        JOIN mobile_skins s ON s.skin_id = f.skin_id
        WHERE NOT (
            s.name LIKE '%(Manufacturer%' OR s.name LIKE '%Manufacturer%'
            OR s.picture_path LIKE '%Manufacturer%' OR s.picture_path LIKE '%constructeur%'
            OR f.skin_img LIKE '%Manufacturer%' OR f.skin_img LIKE '%constructeur%'
        )
    """).fetchone()
    special_skin_count = special_skin_row["cnt"] if special_skin_row else 0

    # Counts behind the haul tabs. Cargo overlaps the haul buckets (a cargo
    # plane still has a category), and "unknown" is fleet models with no row
    # in the `aircraft` table, so these do not sum to `total`.
    haul_row = db.execute(f"""
        SELECT
            SUM(CASE WHEN {HAUL_CASE_SQL} = 'short'  THEN 1 ELSE 0 END) AS short,
            SUM(CASE WHEN {HAUL_CASE_SQL} = 'medium' THEN 1 ELSE 0 END) AS medium,
            SUM(CASE WHEN {HAUL_CASE_SQL} = 'long'   THEN 1 ELSE 0 END) AS long,
            SUM({IS_CARGO_SQL}) AS cargo,
            SUM(CASE WHEN {HAUL_CASE_SQL} IS NULL THEN 1 ELSE 0 END) AS unknown
        FROM fleet f LEFT JOIN aircraft a ON a.model = f.model
    """).fetchone()
    hauls = {
        "all": total,
        "short": haul_row["short"] or 0,
        "medium": haul_row["medium"] or 0,
        "long": haul_row["long"] or 0,
        "cargo": haul_row["cargo"] or 0,
        "unknown": haul_row["unknown"] or 0,
    }

    return {
        "total": total,
        "idle": idle,
        "active": active,
        "avg_utilization": avg_util,
        "hubs": hubs,
        "models": models,
        "special_skin_count": special_skin_count,
        "hauls": hauls,
    }


def get_daily_fleet_liveries(count: int = 3, day: str | None = None) -> list[dict]:
    """Pick `count` special liveries flown by the fleet, rotating once per day.

    Deterministic for a given day: the order is a hash of "<day>:<skin_id>", so
    every client that asks on the same date gets the same three, and the set
    changes at midnight without any stored state. Manufacturer liveries are
    excluded — they are not a collection item.
    """
    db = get_db()
    day = day or datetime.date.today().isoformat()
    rows = db.execute("""
        SELECT s.skin_id, s.name, s.picture_path,
               COUNT(*) AS fleet_count,
               (SELECT COUNT(*) FROM mobile_skin_images i WHERE i.skin_id = s.skin_id) AS has_img
        FROM fleet f
        JOIN mobile_skins s ON s.skin_id = f.skin_id
        WHERE NOT (
            s.name LIKE '%(Manufacturer%' OR s.name LIKE '%Manufacturer%'
            OR s.picture_path LIKE '%Manufacturer%' OR s.picture_path LIKE '%constructeur%'
            OR f.skin_img LIKE '%Manufacturer%' OR f.skin_img LIKE '%constructeur%'
        )
        GROUP BY s.skin_id
    """).fetchall()

    picks = sorted(
        (dict(r) for r in rows),
        key=lambda r: hashlib.sha256(f"{day}:{r['skin_id']}".encode()).hexdigest(),
    )[:max(0, int(count))]

    for item in picks:
        plane = db.execute(
            "SELECT aircraft_id, name, model, hub_iata, utilization "
            "FROM fleet WHERE skin_id = ? ORDER BY name ASC LIMIT 1",
            (item["skin_id"],),
        ).fetchone()
        item["day"] = day
        item["sample_aircraft"] = dict(plane) if plane else None
    return picks


def get_livery_collection(
    include_manufacturer: bool = False,
    status_filter: str | None = None,
    model_query: str | None = None,
    search_query: str | None = None,
) -> list[dict]:
    """Return all special/custom liveries (excluding manufacturer liveries by default),
    indicating ownership and listing owned aircraft names."""
    db = get_db()
    sql = """
        SELECT s.skin_id, s.name, s.model_id, s.picture_path,
               (SELECT GROUP_CONCAT(DISTINCT b.name)
                  FROM mobile_booster_cards c
                  JOIN mobile_boosters b ON b.booster_id = c.booster_id
                 WHERE c.skin_id = s.skin_id) AS boosters,
               (SELECT COUNT(*) FROM fleet f WHERE f.skin_id = s.skin_id) AS fleet_count,
               (SELECT COUNT(*) FROM mobile_aircraft m WHERE m.skin_id = s.skin_id) AS mobile_count,
               (SELECT COUNT(*) FROM mobile_skin_images i WHERE i.skin_id = s.skin_id) AS has_img
        FROM mobile_skins s
        WHERE 1=1
    """
    args = []

    if not include_manufacturer:
        sql += """ AND NOT (
            s.name LIKE '%(Manufacturer%' OR s.name LIKE '%Manufacturer%'
            OR s.picture_path LIKE '%Manufacturer%' OR s.picture_path LIKE '%constructeur%'
        )"""

    if model_query:
        q = model_query.strip().lower()
        if q:
            sql += " AND (LOWER(s.name) LIKE ? OR LOWER(COALESCE(s.picture_path, '')) LIKE ?)"
            args.extend([f"%{q}%", f"%{q}%"])

    if search_query:
        q = search_query.strip().lower()
        if q:
            sql += " AND (LOWER(s.name) LIKE ? OR LOWER(COALESCE(boosters, '')) LIKE ?)"
            args.extend([f"%{q}%", f"%{q}%"])

    sql += " ORDER BY (fleet_count + mobile_count) DESC, s.name ASC"
    rows = db.execute(sql, args).fetchall()

    # Pre-fetch all aircraft names by skin_id to avoid N+1 queries
    ac_map: dict[int, list[dict]] = {}
    ac_rows = db.execute(
        "SELECT aircraft_id, name, model, hub_iata, utilization, skin_id "
        "FROM fleet WHERE skin_id IS NOT NULL ORDER BY name ASC"
    ).fetchall()
    for ac in ac_rows:
        sid = ac["skin_id"]
        if sid not in ac_map:
            ac_map[sid] = []
        ac_map[sid].append({
            "aircraft_id": ac["aircraft_id"],
            "name": ac["name"],
            "model": ac["model"],
            "hub": ac["hub_iata"],
            "utilization": ac["utilization"],
        })

    # Also check mobile_aircraft if any plane is missing from fleet
    mob_rows = db.execute(
        "SELECT aircraft_id, name, skin_id "
        "FROM mobile_aircraft WHERE skin_id IS NOT NULL ORDER BY name ASC"
    ).fetchall()
    for m in mob_rows:
        sid = m["skin_id"]
        if sid not in ac_map or not ac_map[sid]:
            if sid not in ac_map:
                ac_map[sid] = []
            ac_map[sid].append({
                "aircraft_id": m["aircraft_id"],
                "name": m["name"] or f"Aircraft #{m['aircraft_id']}",
                "model": "",
                "hub": "",
                "utilization": 0,
            })

    results = []
    for r in rows:
        item = dict(r)
        sid = item["skin_id"]
        planes = ac_map.get(sid, [])
        item["aircraft"] = planes
        item["aircraft_names"] = [p["name"] for p in planes if p["name"]]
        item["owned_count"] = len(planes)
        item["is_owned"] = len(planes) > 0

        # Apply status filter if requested
        if status_filter == "owned" and not item["is_owned"]:
            continue
        if status_filter == "unowned" and item["is_owned"]:
            continue

        results.append(item)

    return results


def get_skin_image_bytes(skin_id: int, size: str = "big") -> bytes | None:
    """Fetch raw PNG bytes from mobile_skin_images for a given skin_id."""
    db = get_db()
    row = db.execute(
        "SELECT png FROM mobile_skin_images WHERE skin_id = ? AND size = ? LIMIT 1",
        (int(skin_id), size),
    ).fetchone()
    if row and row["png"]:
        return bytes(row["png"])
    # Fallback to any size available
    row = db.execute(
        "SELECT png FROM mobile_skin_images WHERE skin_id = ? LIMIT 1",
        (int(skin_id),),
    ).fetchone()
    return bytes(row["png"]) if row and row["png"] else None

