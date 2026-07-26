"""Reference store for the mobile game's identifiers (model/skin/aircraft ids).

The web/CDP schema (aircraft/routes/fleet) doesn't know the mobile API's model
ids, skin/livery ids, or the mobile account's live aircraft ids. This captures
them, keyed by the game's own ids, populated **incrementally** as the mobile
tools make reads. Tables are `mobile_`-prefixed and live in the shared DB
(`db.get_db()`). Persistence is best-effort — it never raises into the API path.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

import db as _db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mobile_models (
    model_id     INTEGER PRIMARY KEY,
    name         TEXT,
    manufacturer TEXT,
    category     INTEGER,
    raw_price    INTEGER,
    amcoins      INTEGER,
    range_km     INTEGER,
    speed_kmh    INTEGER,
    payload_t    INTEGER,
    seats_total  INTEGER,
    is_classic   INTEGER,
    first_seen   TEXT DEFAULT (datetime('now')),
    last_seen    TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS mobile_skins (
    skin_id     INTEGER PRIMARY KEY,
    model_id    INTEGER,
    name        TEXT,
    livery_type INTEGER,
    creator     TEXT,
    status      TEXT,
    first_seen  TEXT DEFAULT (datetime('now')),
    last_seen   TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS mobile_aircraft (
    aircraft_id INTEGER PRIMARY KEY,
    skin_id     INTEGER,
    model_id    INTEGER,
    name        TEXT,
    hub_id      INTEGER,
    hub_name    TEXT,
    seats_eco   INTEGER,
    seats_bus   INTEGER,
    seats_first INTEGER,
    payload_t   INTEGER,
    wear        REAL,
    raw_price   INTEGER,
    first_seen  TEXT DEFAULT (datetime('now')),
    last_seen   TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS mobile_market (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    auction_id    INTEGER,
    model_id      INTEGER,
    skin_id       INTEGER,
    current_price INTEGER,
    bin_price     INTEGER,
    min_stars     INTEGER,
    max_stars     INTEGER,
    time_left     INTEGER,
    observed_at   TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_mobile_skins_model ON mobile_skins(model_id);
CREATE INDEX IF NOT EXISTS ix_mobile_aircraft_skin ON mobile_aircraft(skin_id);
CREATE INDEX IF NOT EXISTS ix_mobile_market_skin ON mobile_market(skin_id, observed_at);
"""


class MobileStore:
    def __init__(self, conn: Optional[sqlite3.Connection] = None):
        self.conn = conn or _db.get_db()
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def commit(self):
        try:
            self.conn.commit()
        except sqlite3.Error:
            pass

    def _exec(self, sql: str, params: tuple):
        try:
            self.conn.execute(sql, params)
        except sqlite3.Error:
            pass

    def upsert_model(self, model: dict):
        if not model or model.get("id") is None:
            return
        price = model.get("price") or {}
        seats = model.get("seats") or {}
        self._exec("""
            INSERT INTO mobile_models
              (model_id,name,manufacturer,category,raw_price,amcoins,range_km,
               speed_kmh,payload_t,seats_total,last_seen)
            VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'))
            ON CONFLICT(model_id) DO UPDATE SET
              name=excluded.name, manufacturer=excluded.manufacturer,
              category=excluded.category, raw_price=excluded.raw_price,
              amcoins=excluded.amcoins, range_km=excluded.range_km,
              speed_kmh=excluded.speed_kmh, payload_t=excluded.payload_t,
              seats_total=excluded.seats_total, last_seen=datetime('now')
        """, (model.get("id"), model.get("name"),
              (model.get("manufacturer") or {}).get("name"),
              model.get("category"), price.get("raw"), price.get("amcoins"),
              model.get("range"), model.get("speed"), model.get("payload"),
              seats.get("total")))

    def upsert_skin(self, skin_id, model_id=None, name=None, livery_type=None,
                    creator=None, status=None):
        if skin_id is None:
            return
        self._exec("""
            INSERT INTO mobile_skins
              (skin_id,model_id,name,livery_type,creator,status,last_seen)
            VALUES (?,?,?,?,?,?,datetime('now'))
            ON CONFLICT(skin_id) DO UPDATE SET
              model_id=COALESCE(excluded.model_id, mobile_skins.model_id),
              name=COALESCE(excluded.name, mobile_skins.name),
              livery_type=COALESCE(excluded.livery_type, mobile_skins.livery_type),
              creator=COALESCE(excluded.creator, mobile_skins.creator),
              status=COALESCE(excluded.status, mobile_skins.status),
              last_seen=datetime('now')
        """, (skin_id, model_id, name, livery_type, creator, status))

    def observe_fleet_item(self, it: dict):
        if it.get("id") is None:
            return
        self._exec("""
            INSERT INTO mobile_aircraft
              (aircraft_id,skin_id,name,hub_id,seats_eco,seats_bus,seats_first,
               payload_t,wear,last_seen)
            VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))
            ON CONFLICT(aircraft_id) DO UPDATE SET
              skin_id=excluded.skin_id, name=excluded.name, hub_id=excluded.hub_id,
              seats_eco=excluded.seats_eco, seats_bus=excluded.seats_bus,
              seats_first=excluded.seats_first, wear=excluded.wear,
              last_seen=datetime('now')
        """, (it.get("id"), it.get("as_id"), it.get("n"), it.get("h_id"),
              it.get("se"), it.get("sb"), it.get("sf"), it.get("sp"),
              _num(it.get("w"))))
        self.upsert_skin(it.get("as_id"))

    def observe_aircraft_profile(self, p: dict):
        if p.get("id") is None:
            return
        model = p.get("model") or {}
        hub = p.get("hub") or {}
        seats = p.get("seats") or {}
        self.upsert_model(model)
        self._exec("""
            INSERT INTO mobile_aircraft
              (aircraft_id,model_id,name,hub_id,hub_name,seats_eco,seats_bus,
               seats_first,payload_t,wear,raw_price,last_seen)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
            ON CONFLICT(aircraft_id) DO UPDATE SET
              model_id=COALESCE(excluded.model_id, mobile_aircraft.model_id),
              name=excluded.name, hub_id=excluded.hub_id, hub_name=excluded.hub_name,
              seats_eco=excluded.seats_eco, seats_bus=excluded.seats_bus,
              seats_first=excluded.seats_first, payload_t=excluded.payload_t,
              wear=excluded.wear, raw_price=excluded.raw_price,
              last_seen=datetime('now')
        """, (p.get("id"), model.get("id"), p.get("name"), hub.get("id"),
              hub.get("name"), seats.get("eco"), seats.get("business"),
              seats.get("first"), p.get("payload"), _num(p.get("wear")),
              p.get("price")))

    def observe_auction(self, a: dict):
        ac = a.get("aircraft") or {}
        skin = ac.get("skin") or {}
        model_id = ac.get("aircraftListId")
        pool = a.get("sellerPool") or {}
        self.upsert_skin(skin.get("id"), model_id=model_id, name=skin.get("name"),
                         livery_type=skin.get("type"))
        if model_id is not None and ac.get("rawPrice"):
            self._exec("""
                INSERT INTO mobile_models (model_id, raw_price, is_classic, last_seen)
                VALUES (?,?,?,datetime('now'))
                ON CONFLICT(model_id) DO UPDATE SET
                  raw_price=excluded.raw_price,
                  is_classic=COALESCE(excluded.is_classic, mobile_models.is_classic),
                  last_seen=datetime('now')
            """, (model_id, ac.get("rawPrice"), 1 if ac.get("isClassic") else 0))
        self._exec("""
            INSERT INTO mobile_market
              (auction_id,model_id,skin_id,current_price,bin_price,min_stars,
               max_stars,time_left)
            VALUES (?,?,?,?,?,?,?,?)
        """, (a.get("id"), model_id, skin.get("id"), a.get("currentPrice"),
              a.get("binPrice"), pool.get("minStars"), pool.get("maxStars"),
              a.get("timeLeft")))

    def counts(self) -> dict:
        c = self.conn.cursor()
        out = {}
        for t in ("mobile_models", "mobile_skins", "mobile_aircraft", "mobile_market"):
            try:
                out[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.Error:
                out[t] = None
        return out


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
