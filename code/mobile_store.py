"""Reference store for the mobile game's identifiers (model/skin/aircraft ids).

The web/CDP schema (aircraft/routes/fleet) doesn't know the mobile API's model
ids, skin/livery ids, or the mobile account's live aircraft ids. This captures
them, keyed by the game's own ids, populated **incrementally** as the mobile
tools make reads. Tables are `mobile_`-prefixed and live in the shared DB
(`db.get_db()`). Persistence is best-effort — it never raises into the API path.
"""

from __future__ import annotations

import hashlib
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
CREATE TABLE IF NOT EXISTS mobile_boosters (
    booster_id  INTEGER PRIMARY KEY,
    name        TEXT,
    is_event    INTEGER,
    description TEXT,
    start_date  TEXT,
    end_date    TEXT,
    pity_max    INTEGER,
    pity_count  INTEGER,
    pity_rarity INTEGER,
    first_seen  TEXT DEFAULT (datetime('now')),
    last_seen   TEXT DEFAULT (datetime('now'))
);
-- One row per card in a booster's drop table. `drop_rate` is the GROUP's
-- percentage (the game publishes odds per rarity group, not per card);
-- `group_size` is kept alongside so a per-card probability is derivable
-- without re-reading the API.
CREATE TABLE IF NOT EXISTS mobile_booster_cards (
    booster_id  INTEGER NOT NULL,
    card_id     INTEGER NOT NULL,
    skin_id     INTEGER,
    model_id    INTEGER,
    rarity      INTEGER,
    group_name  TEXT,
    drop_rate   REAL,
    group_size  INTEGER,
    effect_type TEXT,
    label       TEXT,
    amount      TEXT,
    first_seen  TEXT DEFAULT (datetime('now')),
    last_seen   TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (booster_id, card_id)
);
-- Livery artwork, pulled from the game's CDN and kept as bytes so the DB is
-- self-contained. One row per (skin, size): medium ~8KB, big ~25KB,
-- superBig ~42KB.
CREATE TABLE IF NOT EXISTS mobile_skin_images (
    skin_id    INTEGER NOT NULL,
    size       TEXT NOT NULL,
    png        BLOB,
    byte_len   INTEGER,
    sha256     TEXT,
    source_url TEXT,
    fetched_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (skin_id, size)
);
CREATE INDEX IF NOT EXISTS ix_mobile_skins_model ON mobile_skins(model_id);
CREATE INDEX IF NOT EXISTS ix_mobile_booster_cards_skin
    ON mobile_booster_cards(skin_id);
CREATE INDEX IF NOT EXISTS ix_mobile_booster_cards_rarity
    ON mobile_booster_cards(rarity, drop_rate);
CREATE INDEX IF NOT EXISTS ix_mobile_aircraft_skin ON mobile_aircraft(skin_id);
"""


# One row per known livery: what it is, whether you fly it, how it drops, where
# it is sold, and whether the artwork is cached. `best_card_rate` is the
# per-card chance in the most generous booster offering it. Kept out of _SCHEMA
# because CREATE VIEW IF NOT EXISTS leaves a stale definition alone — this one
# is dropped and rebuilt on every open, so its columns can grow.
_OVERVIEW_VIEW = """
DROP VIEW IF EXISTS mobile_skin_overview;
CREATE VIEW mobile_skin_overview AS
SELECT s.skin_id,
       s.name,
       s.source,
       s.creator,
       s.model_id,
       s.price_amcoins,
       s.sold,
       s.owned,
       (SELECT COUNT(*) FROM mobile_aircraft a
         WHERE a.skin_id = s.skin_id)                       AS owned_aircraft,
       (SELECT MAX(c.drop_rate / NULLIF(c.group_size, 0))
          FROM mobile_booster_cards c
         WHERE c.skin_id = s.skin_id)                       AS best_card_rate,
       (SELECT GROUP_CONCAT(DISTINCT b.name)
          FROM mobile_booster_cards c
          JOIN mobile_boosters b ON b.booster_id = c.booster_id
         WHERE c.skin_id = s.skin_id)                       AS boosters,
       (SELECT COUNT(*) FROM mobile_skin_images i
         WHERE i.skin_id = s.skin_id)                       AS images
  FROM mobile_skins s;
CREATE INDEX IF NOT EXISTS ix_mobile_skins_source ON mobile_skins(source);
"""


# `mobile_skins.source` values by `aircraft.skin.type`. The vocabulary is
# defined in mobile_api (SKIN_TYPE_* / SKIN_SOURCE_*); it is spelled out here
# rather than imported so the store layer stays free of the HTTP client.
_SKIN_SOURCE_BY_TYPE = {0: "manufacturer", 1: "playrion", 2: "market"}


# Columns added to mobile_skins after it shipped. CREATE TABLE IF NOT EXISTS
# leaves an existing table alone, so these need an explicit ALTER.
_SKIN_COLUMNS = [
    ("picture_path", "TEXT"),   # CDN path, e.g. /common/images/.../foo.png
    ("rarity", "INTEGER"),      # legacy, populated by API but unused in UI
    # Where the livery comes from, one of mobile_api.SKIN_SOURCE_*: the model's
    # own paint, an official Playrion livery, or a player-designed one sold on
    # the livery market. The duty free reports it per shop bucket, the SHM per
    # listing (`aircraft.skin.type`); 'manufacturer' is never downgraded.
    ("source", "TEXT"),
    ("price_amcoins", "INTEGER"),  # duty free asking price in AM coins
    ("sold", "INTEGER"),           # copies sold game-wide (duty free counter)
    ("owned", "INTEGER"),          # 1 once the duty free reports it purchased
]


class MobileStore:
    def __init__(self, conn: Optional[sqlite3.Connection] = None):
        self.conn = conn or _db.get_db()
        self.conn.executescript(_SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        try:
            cols = {r[1] for r in
                    self.conn.execute("PRAGMA table_info(mobile_skins)").fetchall()}
        except sqlite3.Error:
            return
        for name, sql_type in _SKIN_COLUMNS:
            if name not in cols:
                self._exec(f"ALTER TABLE mobile_skins ADD COLUMN {name} {sql_type}", ())
        # after the ALTERs, so the view can select the columns they just added
        try:
            self.conn.executescript(_OVERVIEW_VIEW)
        except sqlite3.Error:
            pass

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
                    creator=None, status=None, picture_path=None, rarity=None,
                    source=None, price_amcoins=None, sold=None, owned=None):
        if skin_id is None:
            return
        # `source` is the one field a later read may not downgrade: the SHM
        # reports a manufacturer paint as such, while the duty free lists the
        # same livery in its Playrion bucket, and the SHM answer is the finer
        # one. Price / sold / owned are live counters, so newest wins.
        self._exec("""
            INSERT INTO mobile_skins
              (skin_id,model_id,name,livery_type,creator,status,
               picture_path,rarity,source,price_amcoins,sold,owned,last_seen)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
            ON CONFLICT(skin_id) DO UPDATE SET
              model_id=COALESCE(excluded.model_id, mobile_skins.model_id),
              name=COALESCE(excluded.name, mobile_skins.name),
              livery_type=COALESCE(excluded.livery_type, mobile_skins.livery_type),
              creator=COALESCE(excluded.creator, mobile_skins.creator),
              status=COALESCE(excluded.status, mobile_skins.status),
              picture_path=COALESCE(excluded.picture_path, mobile_skins.picture_path),
              rarity=NULLIF(MAX(COALESCE(excluded.rarity, -1),
                                COALESCE(mobile_skins.rarity, -1)), -1),
              source=CASE WHEN mobile_skins.source = 'manufacturer'
                          THEN 'manufacturer'
                          ELSE COALESCE(excluded.source, mobile_skins.source) END,
              price_amcoins=COALESCE(excluded.price_amcoins,
                                     mobile_skins.price_amcoins),
              sold=COALESCE(excluded.sold, mobile_skins.sold),
              owned=COALESCE(excluded.owned, mobile_skins.owned),
              last_seen=datetime('now')
        """, (skin_id, model_id, name, livery_type, creator, status,
              picture_path, rarity, source, price_amcoins, sold, owned))

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
        # `skin.type` is the SHM's own answer to "Playrion or player-made?"
        # (mobile_api.SKIN_TYPE_*); it is finer than the duty free's buckets
        # because it also calls out a plain manufacturer paint.
        self.upsert_skin(skin.get("id"), model_id=model_id, name=skin.get("name"),
                         livery_type=skin.get("type"),
                         source=_SKIN_SOURCE_BY_TYPE.get(skin.get("type")))
        if model_id is not None and ac.get("rawPrice"):
            self._exec("""
                INSERT INTO mobile_models (model_id, raw_price, is_classic, last_seen)
                VALUES (?,?,?,datetime('now'))
                ON CONFLICT(model_id) DO UPDATE SET
                  raw_price=excluded.raw_price,
                  is_classic=COALESCE(excluded.is_classic, mobile_models.is_classic),
                  last_seen=datetime('now')
            """, (model_id, ac.get("rawPrice"), 1 if ac.get("isClassic") else 0))

    # ── boosters ────────────────────────────────────────────────────────
    def upsert_booster(self, b: dict):
        """One entry from GET booster."""
        if not b or b.get("id") is None:
            return
        pity = b.get("pity") or {}
        self._exec("""
            INSERT INTO mobile_boosters
              (booster_id,name,is_event,description,start_date,end_date,
               pity_max,pity_count,pity_rarity,last_seen)
            VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))
            ON CONFLICT(booster_id) DO UPDATE SET
              name=excluded.name, is_event=excluded.is_event,
              description=excluded.description,
              start_date=excluded.start_date, end_date=excluded.end_date,
              pity_max=excluded.pity_max, pity_count=excluded.pity_count,
              pity_rarity=excluded.pity_rarity, last_seen=datetime('now')
        """, (b.get("id"), b.get("name"), 1 if b.get("isEvent") else 0,
              b.get("description"), _date(b.get("startDate")),
              _date(b.get("endDate")), pity.get("gaugeMax"),
              pity.get("gaugeCount"), pity.get("gaugeRarity")))

    def record_droprate(self, booster_id: int, payload: dict) -> int:
        """Store a GET booster/droprate response. Returns the card count.

        Also backfills mobile_skins: the drop table is the only endpoint that
        gives a real livery NAME for skins the player does not own.
        """
        n = 0
        for grp in (payload.get("dropRates") or []):
            cards = grp.get("cards") or []
            for c in cards:
                skin = c.get("skin") or {}
                skin_id = skin.get("id")
                pic = ((skin.get("picturePath") or {}).get("big")
                       or (skin.get("picturePath") or {}).get("medium"))
                if skin_id is not None:
                    self.upsert_skin(skin_id, model_id=c.get("aircraftModelId"),
                                     name=skin.get("name"),
                                     picture_path=_strip_ver(pic),
                                     rarity=c.get("rarity"))
                self._exec("""
                    INSERT INTO mobile_booster_cards
                      (booster_id,card_id,skin_id,model_id,rarity,group_name,
                       drop_rate,group_size,effect_type,label,amount,last_seen)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
                    ON CONFLICT(booster_id,card_id) DO UPDATE SET
                      skin_id=excluded.skin_id, model_id=excluded.model_id,
                      rarity=excluded.rarity, group_name=excluded.group_name,
                      drop_rate=excluded.drop_rate, group_size=excluded.group_size,
                      effect_type=excluded.effect_type, label=excluded.label,
                      amount=excluded.amount, last_seen=datetime('now')
                """, (booster_id, c.get("id"), skin_id,
                      c.get("aircraftModelId"), c.get("rarity"),
                      grp.get("name"), _num(grp.get("dropRate")), len(cards),
                      c.get("effectType"), c.get("label"), c.get("amount")))
                n += 1
        pity = payload.get("pity") or {}
        if pity:
            self._exec("""
                UPDATE mobile_boosters
                   SET pity_max=?, pity_count=?, pity_rarity=?,
                       last_seen=datetime('now')
                 WHERE booster_id=?
            """, (pity.get("gaugeMax"), pity.get("gaugeCount"),
                  pity.get("gaugeRarity"), booster_id))
        return n

    def store_skin_image(self, skin_id: int, size: str, data: bytes,
                         source_url: str = None):
        if skin_id is None or not data:
            return
        self._exec("""
            INSERT INTO mobile_skin_images
              (skin_id,size,png,byte_len,sha256,source_url,fetched_at)
            VALUES (?,?,?,?,?,?,datetime('now'))
            ON CONFLICT(skin_id,size) DO UPDATE SET
              png=excluded.png, byte_len=excluded.byte_len,
              sha256=excluded.sha256, source_url=excluded.source_url,
              fetched_at=datetime('now')
        """, (skin_id, size, sqlite3.Binary(data), len(data),
              hashlib.sha256(data).hexdigest(), source_url))

    def skins_missing_image(self, size: str) -> list:
        """(skin_id, picture_path) for skins with a known path but no image."""
        try:
            return self.conn.execute("""
                SELECT s.skin_id, s.picture_path
                  FROM mobile_skins s
                  LEFT JOIN mobile_skin_images i
                         ON i.skin_id = s.skin_id AND i.size = ?
                 WHERE s.picture_path IS NOT NULL AND i.skin_id IS NULL
                 ORDER BY s.skin_id
            """, (size,)).fetchall()
        except sqlite3.Error:
            return []

    def counts(self) -> dict:
        c = self.conn.cursor()
        out = {}
        for t in ("mobile_models", "mobile_skins", "mobile_aircraft",
                  "mobile_boosters", "mobile_booster_cards",
                  "mobile_skin_images"):
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


def _date(v):
    """The API wraps dates as {"date": "...", "timezone": ...}; keep the string."""
    if isinstance(v, dict):
        return v.get("date")
    return v


def _strip_ver(path):
    """Drop the ?v=... cache-buster so the stored path is stable."""
    if not path:
        return None
    return path.split("?")[0]
