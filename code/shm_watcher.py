#!/usr/bin/env python3
"""Watch the second-hand market for specific liveries and buy them on sight.

The problem this solves: the SHM has no push channel and no paging. There is
one listing endpoint, `auction/aircraft/auction_list`, it answers with at most
100 auctions, and the live market runs to four figures — so polling it
unfiltered means watching ~8% of the market and hoping. A limited-time livery
can be listed and bought again without ever entering that window.

The way out is the server-side filter the client itself uses,
`filterAircraftListId`. A livery belongs to exactly one aircraft model, and a
read filtered to one model comes back with EVERY live listing of that model
(verified: under the 100 cap, both sorts return identical id sets). So one
cheap request per watched model is complete coverage of the liveries on it,
instead of an incomplete sweep of everything.

A one-shot scan costs one request per selected model plus an optional
newest-first sweep. Continuous runs use two paced lanes instead: newest 100
every 60 seconds and one model every 45 seconds by default. Automatic paid-pack
and challenge models alternate with background models. Per-watch arming caches
limit and balance reads for 10 minutes; pure observation skips them. A two-second
client-wide delay prevents request bursts.

Buying is always buy-now at the listing's own `binPrice` — never an
incremental bid — so the watcher takes a plane only when its individual arm
toggle is on and the balance remains positive. It never enters a bidding war.

Nothing spends money until a watch is armed. Missing paid-pack targets start
armed; challenge and manual watches begin in observation mode.

Usage:
  code/shm_watcher.py add 4670077 --max 1.2b        # watch one livery
  code/shm_watcher.py add-booster 826 --max 2b      # watch a booster's liveries
  code/shm_watcher.py sync-paid-packs               # sync paid-pack and challenge skins
  code/shm_watcher.py list
  code/shm_watcher.py remove 4670077
  code/shm_watcher.py scan                          # one pass, buys nothing
  code/shm_watcher.py scan --arm                    # one pass, buys
  code/shm_watcher.py run                           # armed rows buy, others observe
  code/shm_watcher.py run --arm-paid-packs          # legacy paid-pack-only mode
  code/shm_watcher.py run --arm --budget 2b         # guarded live buyer
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import logging
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

import db as _db
from mobile_api import (AMClient, AMError, AMSession, AUCTION_PAGE_LIMIT,
                        fmt_money)
from mobile_store import MobileStore

# httpx logs full request URLs at INFO and the mobile API passes access_token
# in the query string, so an unsuppressed run prints the live token.
logging.disable(logging.INFO)

# Fallbacks for the server's own limits, used only when `loading/notification`
# can't be read. The live values come from AMClient.auction_rules().
DEFAULT_MAX_BIDS_PER_DAY = 20
DEFAULT_PURCHASE_FEE_PCT = 20.0
DEFAULT_REQUEST_DELAY = 2.0
PAID_PACK_SOURCE = "shop-pack:auto"
CHALLENGE_SOURCE = "challenge:auto"
AUTO_SOURCES = frozenset({PAID_PACK_SOURCE, CHALLENGE_SOURCE})

SCHEMA = """
-- One row per livery being watched. `model_id` is what makes the cheap
-- server-side filter possible; an entry without one can only ever be caught by
-- the wide sweep, so it is worth backfilling.
CREATE TABLE IF NOT EXISTS shm_watch (
    skin_id     INTEGER PRIMARY KEY,
    model_id    INTEGER,
    label       TEXT,
    max_price   INTEGER,           -- highest binPrice to accept; NULL = any
    want        INTEGER NOT NULL DEFAULT 1,
    bought      INTEGER NOT NULL DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1,
    armed       INTEGER NOT NULL DEFAULT 0, -- live-buy this specific watch
    source      TEXT,              -- 'manual' or 'booster:<id>'
    added_at    TEXT DEFAULT (datetime('now')),
    last_seen   TEXT,
    last_price  INTEGER
);
-- Which models were polled when, so a watchlist wider than one pass rotates
-- instead of always re-reading the same head of the list.
CREATE TABLE IF NOT EXISTS shm_model_checks (
    model_id     INTEGER PRIMARY KEY,
    last_checked TEXT,
    checks       INTEGER NOT NULL DEFAULT 0,
    truncated    INTEGER NOT NULL DEFAULT 0   -- read came back at the 100 cap
);
-- Every listing the watcher has seen, watched or not. This is the price
-- history that makes a sensible --max reachable rather than guessed.
CREATE TABLE IF NOT EXISTS shm_sightings (
    auction_id    INTEGER PRIMARY KEY,
    skin_id       INTEGER,
    model_id      INTEGER,
    skin_name     TEXT,
    skin_type     INTEGER,
    current_price INTEGER,
    bin_price     INTEGER,
    time_left_s   INTEGER,
    bids          INTEGER,
    seller_pool   INTEGER,
    is_own        INTEGER,
    first_seen    TEXT DEFAULT (datetime('now')),
    last_seen     TEXT DEFAULT (datetime('now'))
);
-- Every buy the watcher decided on, dry-run ones included, so a rehearsal is
-- auditable and a real run has a spend ledger the daily budget reads back.
CREATE TABLE IF NOT EXISTS shm_buys (
    buy_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    auction_id  INTEGER,
    skin_id     INTEGER,
    model_id    INTEGER,
    skin_name   TEXT,
    bin_price   INTEGER,
    est_cost    INTEGER,
    fee_pct     REAL,
    dry_run     INTEGER NOT NULL DEFAULT 1,
    confirmed   INTEGER,            -- re-read said we hold it
    note        TEXT,
    bought_at   TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_shm_sightings_skin ON shm_sightings(skin_id);
CREATE INDEX IF NOT EXISTS ix_shm_buys_at ON shm_buys(bought_at);
"""


def open_db():
    conn = _db.get_db()
    conn.executescript(SCHEMA)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(shm_watch)")}
    if "armed" not in columns:
        conn.execute("ALTER TABLE shm_watch ADD COLUMN armed INTEGER NOT NULL DEFAULT 0")
        # Paid-pack watches created before per-watch arming existed retain the
        # intended automatic behavior. Later UI changes are never overwritten.
        conn.execute("UPDATE shm_watch SET armed=1 WHERE source=?",
                     (PAID_PACK_SOURCE,))
    conn.commit()
    return conn


# ── money parsing ───────────────────────────────────────────────────────────
def parse_money(text: str) -> int:
    """'1.2b' / '850m' / '1_209_000_000' → int. Prices here are 9-10 digits."""
    s = str(text).strip().lower().replace("_", "").replace(",", "").lstrip("$")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([kmb])?", s)
    if not m:
        raise argparse.ArgumentTypeError(f"not a price: {text!r}")
    scale = {None: 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[m.group(2)]
    return int(float(m.group(1)) * scale)


# ── watchlist ───────────────────────────────────────────────────────────────
@dataclass
class Watch:
    skin_id: int
    model_id: Optional[int]
    label: str
    max_price: Optional[int]
    want: int
    bought: int
    source: str = "manual"
    armed: bool = False

    @property
    def remaining(self) -> int:
        return max(0, self.want - self.bought)


def _skin_row(conn, skin_id: int) -> tuple[Optional[int], Optional[str]]:
    """(model_id, name) for a skin, from whatever the DB already knows."""
    row = conn.execute("SELECT model_id, name FROM mobile_skins WHERE skin_id=?",
                       (skin_id,)).fetchone()
    if row and row["model_id"] is not None:
        return row["model_id"], row["name"]
    card = conn.execute("SELECT model_id FROM mobile_booster_cards "
                        "WHERE skin_id=? AND model_id IS NOT NULL LIMIT 1",
                        (skin_id,)).fetchone()
    seen = conn.execute("SELECT model_id, skin_name FROM shm_sightings "
                        "WHERE skin_id=? AND model_id IS NOT NULL LIMIT 1",
                        (skin_id,)).fetchone()
    model = (card["model_id"] if card else None) or (seen["model_id"] if seen else None)
    name = (row["name"] if row else None) or (seen["skin_name"] if seen else None)
    return model, name


def add_watch(conn, skin_id: int, max_price: Optional[int] = None,
              want: int = 1, source: str = "manual",
              label: Optional[str] = None, armed: bool = False) -> Watch:
    model_id, name = _skin_row(conn, skin_id)
    label = label or name or f"skin {skin_id}"
    conn.execute("""
        INSERT INTO shm_watch
            (skin_id, model_id, label, max_price, want, source, armed)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(skin_id) DO UPDATE SET
          model_id=COALESCE(excluded.model_id, shm_watch.model_id),
          label=excluded.label,
          max_price=excluded.max_price,
          want=excluded.want,
          source=excluded.source,
          active=1
    """, (skin_id, model_id, label, max_price, want, source, int(armed)))
    conn.commit()
    return Watch(skin_id, model_id, label, max_price, want, 0, source, armed)


def remove_watch(conn, skin_id: int) -> int:
    cur = conn.execute("DELETE FROM shm_watch WHERE skin_id=?", (skin_id,))
    conn.commit()
    return cur.rowcount


def active_watches(conn) -> list[Watch]:
    rows = conn.execute("""
        SELECT skin_id, model_id, label, max_price, want, bought, source, armed
          FROM shm_watch WHERE active=1 AND bought < want
         ORDER BY model_id IS NULL, model_id, skin_id
    """).fetchall()
    return [Watch(r["skin_id"], r["model_id"], r["label"], r["max_price"],
                  r["want"], r["bought"], r["source"] or "manual",
                  bool(r["armed"])) for r in rows]


def has_armed_watches(conn) -> bool:
    return bool(conn.execute("""
        SELECT 1 FROM shm_watch
         WHERE active=1 AND bought < want AND armed=1
         LIMIT 1
    """).fetchone())


def set_watch_armed(conn, skin_id: int, armed: bool) -> bool:
    """Persist one watch's live-buy choice without changing its observation."""
    cur = conn.execute("UPDATE shm_watch SET armed=? WHERE skin_id=?",
                       (int(armed), skin_id))
    conn.commit()
    return bool(cur.rowcount)


def automatic_target_skins(conn) -> list[dict]:
    """Special paid-pack and challenge liveries, including owned catalog rows."""
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    sources = ["""
        SELECT DISTINCT i.skin_id AS skin_id, ? AS source, 0 AS priority
          FROM mobile_shop_offer_items i
          JOIN mobile_shop_offers o ON o.offer_id = i.offer_id
         WHERE i.skin_id IS NOT NULL
           AND o.template = 'pack'
           AND o.currency = 'realMoney'
    """]
    params: list[Any] = [PAID_PACK_SOURCE]
    if "mobile_challenge_rewards" in tables:
        sources.append("""
            SELECT DISTINCT skin_id, ? AS source, 1 AS priority
              FROM mobile_challenge_rewards
             WHERE skin_id IS NOT NULL
        """)
        params.append(CHALLENGE_SOURCE)
    rows = conn.execute(f"""
        WITH candidates AS ({" UNION ALL ".join(sources)}),
        ranked AS (
            SELECT skin_id, source,
                   ROW_NUMBER() OVER (PARTITION BY skin_id ORDER BY priority) AS rank
              FROM candidates
        ), owned AS (
            SELECT skin_id FROM fleet WHERE skin_id IS NOT NULL
            UNION
            SELECT skin_id FROM mobile_aircraft WHERE skin_id IS NOT NULL
        )
        SELECT s.skin_id, s.model_id, s.name, r.source,
               CASE WHEN x.skin_id IS NULL THEN 0 ELSE 1 END AS owned
          FROM mobile_skins s
          JOIN ranked r ON r.skin_id = s.skin_id AND r.rank = 1
          LEFT JOIN owned x ON x.skin_id = s.skin_id
         WHERE COALESCE(s.source, '') != 'manufacturer'
         ORDER BY r.source, s.model_id, s.skin_id
    """, params).fetchall()
    return [dict(r) for r in rows]


def sync_automatic_watches(conn, max_price: Optional[int] = None) -> dict:
    """Sync paid-pack and challenge catalog rows into the watchlist.

    Missing paid-pack targets start armed. Challenge and manual targets remain
    observation-only until their individual arm toggle is enabled. Owned
    automatic catalog rows stay visible but inactive, so the UI explains why a
    known paid livery is not a purchase target.
    """
    targets = automatic_target_skins(conn)
    target_ids = {int(r["skin_id"]) for r in targets}
    added = 0
    for row in targets:
        cur = conn.execute("""
            INSERT OR IGNORE INTO shm_watch
                (skin_id, model_id, label, max_price, want, bought, active, source, armed)
            VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
        """, (row["skin_id"], row["model_id"], row["name"], max_price,
              int(row["owned"]), 0 if row["owned"] else 1, row["source"],
              int(row["source"] == PAID_PACK_SOURCE and not row["owned"])))
        added += cur.rowcount

    updated = 0
    if max_price is not None and target_ids:
        placeholders = ",".join("?" for _ in target_ids)
        updated = conn.execute(
            f"UPDATE shm_watch SET max_price=? "
            f"WHERE source=? AND skin_id IN ({placeholders}) "
            "AND COALESCE(max_price, -1) != ?",
            (max_price, PAID_PACK_SOURCE, *sorted(target_ids), max_price),
        ).rowcount

    for row in targets:
        if row["owned"]:
            conn.execute("""
                UPDATE shm_watch SET bought=want, active=0, armed=0
                 WHERE skin_id=? AND source IN (?, ?)
            """, (row["skin_id"], PAID_PACK_SOURCE, CHALLENGE_SOURCE))
    managed = conn.execute(
        "SELECT skin_id FROM shm_watch WHERE source IN (?, ?) AND active=1",
        tuple(AUTO_SOURCES),
    ).fetchall()
    disabled = 0
    for row in managed:
        if int(row["skin_id"]) not in target_ids:
            disabled += conn.execute(
                "UPDATE shm_watch SET active=0, armed=0 WHERE skin_id=? "
                "AND source IN (?, ?)",
                (row["skin_id"], PAID_PACK_SOURCE, CHALLENGE_SOURCE),
            ).rowcount
    conn.commit()
    return {"targets": len(targets), "added": added, "updated": updated,
            "disabled": disabled,
            "models": len({r["model_id"] for r in targets
                           if r["model_id"] is not None}),
            "liveries": targets}


def sync_paid_pack_watches(conn, max_price: Optional[int] = None) -> dict:
    """Backward-compatible name for the paid-pack and challenge target sync."""
    return sync_automatic_watches(conn, max_price)


def booster_skins(conn, booster_id: int,
                  include_manufacturer: bool = False) -> list[tuple[int, str]]:
    """(skin_id, label) for the liveries in a booster's drop table."""
    sql = """SELECT c.skin_id, COALESCE(s.name, c.label, '') AS name
               FROM mobile_booster_cards c
               LEFT JOIN mobile_skins s ON s.skin_id = c.skin_id
              WHERE c.booster_id=? AND c.skin_id IS NOT NULL"""
    args: list[Any] = [booster_id]
    out = []
    for r in conn.execute(sql, args).fetchall():
        name = r["name"] or ""
        # Manufacturer paint is the model's default — it is on the market by
        # the hundred and is never what someone means by "that livery".
        if not include_manufacturer and "(manufacturer livery)" in name.lower():
            continue
        out.append((r["skin_id"], name or f"skin {r['skin_id']}"))
    return sorted(set(out))


# ── scanning ────────────────────────────────────────────────────────────────
def plan_models(conn, watches: Iterable[Watch], per_pass: int) -> list[int]:
    """Which models to read this pass: least-recently-checked first."""
    models = sorted({w.model_id for w in watches if w.model_id is not None})
    if not models:
        return []
    seen = {r["model_id"]: r["last_checked"] or ""
            for r in conn.execute("SELECT model_id, last_checked "
                                  "FROM shm_model_checks").fetchall()}
    models.sort(key=lambda m: (seen.get(m, ""), m))
    return models[:per_pass] if per_pass > 0 else models


def plan_next_model(conn, watches: Iterable[Watch], priority_turn: bool) -> Optional[int]:
    """Choose one due model while giving paid-pack targets a faster lane.

    Scheduler ticks alternate between priority and background models. If one
    lane is empty the other receives every tick. Ordering inside either lane
    remains least-recently-checked first.
    """
    watches = list(watches)
    priority_models = {w.model_id for w in watches
                       if w.model_id is not None and w.source in AUTO_SOURCES}
    background_models = {w.model_id for w in watches
                         if w.model_id is not None and w.model_id not in priority_models}
    wanted = priority_models if priority_turn else background_models
    if not wanted:
        wanted = background_models if priority_turn else priority_models
    lane = [w for w in watches if w.model_id in wanted]
    planned = plan_models(conn, lane, 1)
    return planned[0] if planned else None


def _note_check(conn, model_id: int, truncated: bool) -> None:
    conn.execute("""
        INSERT INTO shm_model_checks (model_id, last_checked, checks, truncated)
        VALUES (?, datetime('now'), 1, ?)
        ON CONFLICT(model_id) DO UPDATE SET
          last_checked=datetime('now'),
          checks=shm_model_checks.checks + 1,
          truncated=excluded.truncated
    """, (model_id, 1 if truncated else 0))


def record_sighting(conn, a: dict) -> None:
    ac = a.get("aircraft") or {}
    skin = ac.get("skin") or {}
    conn.execute("""
        INSERT INTO shm_sightings (auction_id, skin_id, model_id, skin_name,
                                   skin_type, current_price, bin_price,
                                   time_left_s, bids, seller_pool, is_own)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(auction_id) DO UPDATE SET
          current_price=excluded.current_price,
          bin_price=excluded.bin_price,
          time_left_s=excluded.time_left_s,
          bids=excluded.bids,
          last_seen=datetime('now')
    """, (a.get("id"), skin.get("id"), ac.get("aircraftListId"), skin.get("name"),
          skin.get("type"), a.get("currentPrice"), a.get("binPrice"),
          a.get("timeLeft"), a.get("countParticipant"),
          (a.get("sellerPool") or {}).get("key"),
          1 if a.get("isAirlineSeller") else 0))


def _record_listings(conn, listings: Iterable[dict]) -> list[dict]:
    seen = {int(a["id"]): a for a in listings}
    for a in seen.values():
        record_sighting(conn, a)
    conn.commit()
    return list(seen.values())


def collect_sweep(client: AMClient, conn, pool_only: bool = False) -> list[dict]:
    """Read only the newest-100 lane and persist its sightings."""
    return _record_listings(
        conn, client.auctions(sort="timePlus", pool_only=pool_only))


def collect_model(client: AMClient, conn, model_id: int,
                  pool_only: bool = False) -> list[dict]:
    """Read one complete model lane and persist its sightings."""
    listings = client.auctions(sort="timePlus", pool_only=pool_only,
                               model_id=model_id)
    _note_check(conn, model_id, truncated=len(listings) >= AUCTION_PAGE_LIMIT)
    return _record_listings(conn, listings)


def collect(client: AMClient, conn, watches: list[Watch], per_pass: int,
            sweep: bool = True, pool_only: bool = False) -> tuple[list[dict], int]:
    """One pass. Returns (listings seen, requests spent)."""
    seen: dict[int, dict] = {}
    requests = 0

    if sweep:
        for a in collect_sweep(client, conn, pool_only=pool_only):
            seen[a["id"]] = a
        requests += 1

    for model_id in plan_models(conn, watches, per_pass):
        got = collect_model(client, conn, model_id, pool_only=pool_only)
        requests += 1
        for a in got:
            seen[a["id"]] = a
    return list(seen.values()), requests


# ── decisions ───────────────────────────────────────────────────────────────
@dataclass
class Candidate:
    auction: dict
    watch: Watch
    bin_price: int
    est_cost: int

    @property
    def auction_id(self) -> int:
        return self.auction["id"]

    @property
    def skin_name(self) -> str:
        return ((self.auction.get("aircraft") or {}).get("skin") or {}
                ).get("name") or self.watch.label


@dataclass
class Skipped:
    auction_id: int
    label: str
    reason: str


def choose(listings: Iterable[dict], watches: Iterable[Watch],
           fee_pct: float = DEFAULT_PURCHASE_FEE_PCT
           ) -> tuple[list[Candidate], list[Skipped]]:
    """Pick the cheapest buyable listing per watched livery.

    `max_price` is compared against the raw `binPrice` — the number shown on
    the market. The game's auction-rule fee is not confirmed as a buyer charge,
    so headroom is based on the buy-now price this client actually submits.
    """
    by_skin = {w.skin_id: w for w in watches if w.remaining > 0}
    best: dict[int, Candidate] = {}
    skipped: list[Skipped] = []
    for a in listings:
        ac = a.get("aircraft") or {}
        skin_id = (ac.get("skin") or {}).get("id")
        w = by_skin.get(skin_id)
        if w is None:
            continue
        label = (ac.get("skin") or {}).get("name") or w.label
        bin_price = int(a.get("binPrice") or 0)
        if a.get("isAirlineSeller"):
            skipped.append(Skipped(a["id"], label, "own listing"))
            continue
        if a.get("isConcluded") or a.get("isPurchased") or (a.get("timeLeft") or 0) <= 0:
            skipped.append(Skipped(a["id"], label, "already gone"))
            continue
        if bin_price <= 0:
            # No buy-now means the only way in is an auction bid, which is a
            # price war with an unknown ceiling. Out of scope by design.
            skipped.append(Skipped(a["id"], label, "no buy-now price"))
            continue
        if w.max_price is not None and bin_price > w.max_price:
            skipped.append(Skipped(
                a["id"], label,
                f"{fmt_money(bin_price)} over cap {fmt_money(w.max_price)}"))
            continue
        cand = Candidate(a, w, bin_price, bin_price)
        cur = best.get(skin_id)
        if cur is None or cand.bin_price < cur.bin_price:
            best[skin_id] = cand
    return sorted(best.values(), key=lambda c: c.bin_price), skipped


def spent_today(conn) -> tuple[int, int]:
    """(count, dollars) of real buys since UTC midnight."""
    row = conn.execute("""
        SELECT COUNT(*) AS n, COALESCE(SUM(est_cost), 0) AS spend
          FROM shm_buys
         WHERE dry_run=0 AND bought_at >= date('now') || ' 00:00:00'
    """).fetchone()
    return row["n"], row["spend"]


@dataclass
class Limits:
    max_bids_per_day: int = DEFAULT_MAX_BIDS_PER_DAY
    fee_pct: float = DEFAULT_PURCHASE_FEE_PCT
    reserve_bids: int = 0
    daily_budget: Optional[int] = None
    balance: Optional[int] = None
    bids_used: int = 0

    @property
    def bids_left(self) -> int:
        return self.max_bids_per_day - self.reserve_bids - self.bids_used


@dataclass
class LimitCache:
    """Live purchase guards refreshed on a slow cadence, not every scan."""

    ttl: float = 600.0
    limits: Optional[Limits] = None
    refreshed_at: float = 0.0

    def due(self, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        return self.limits is None or now - self.refreshed_at >= self.ttl

    def refresh(self, client: AMClient, conn, reserve_bids: int,
                daily_budget: Optional[int], now: Optional[float] = None,
                strict: bool = False) -> Limits:
        self.limits = read_limits(client, conn, reserve_bids, daily_budget,
                                  strict=strict)
        self.refreshed_at = time.monotonic() if now is None else now
        return self.limits


def read_limits(client: AMClient, conn, reserve_bids: int = 0,
                daily_budget: Optional[int] = None,
                strict: bool = False) -> Limits:
    """Pull the server's own caps plus today's usage. Never guesses silently."""
    lim = Limits(reserve_bids=reserve_bids, daily_budget=daily_budget)
    errors = []
    try:
        rules = client.auction_rules()
        lim.max_bids_per_day = int(rules.get("maxBidByDay") or lim.max_bids_per_day)
        lim.fee_pct = float(rules.get("purchaseFeePercent") or lim.fee_pct)
    except AMError as e:
        errors.append(f"auction rules: {e}")
        print(f"  ! auction rules unreadable ({e}); using defaults", file=sys.stderr)
    local_bids, _ = spent_today(conn)
    server_bids = 0
    try:
        server_bids = int(client.my_bidding()["summary"].get("countOfBidding") or 0)
    except AMError as e:
        errors.append(f"bid usage: {e}")
    # The server counts manual bids too, the local ledger counts only ours —
    # whichever is higher is the one that can hit the cap first.
    lim.bids_used = max(local_bids, server_bids)
    try:
        lim.balance = int(client.resources().get("dollar") or 0)
    except AMError as e:
        errors.append(f"balance: {e}")
    if strict and errors:
        raise AMError("purchase guards unavailable: " + "; ".join(errors))
    return lim


# ── execution ───────────────────────────────────────────────────────────────
def buy(client: AMClient, conn, cand: Candidate, dry_run: bool = True,
        note: str = "") -> dict:
    """Take a listing at its buy-now price. Bids exactly binPrice, never more."""
    result: dict[str, Any] = {"auction_id": cand.auction_id,
                              "skin_id": cand.watch.skin_id,
                              "label": cand.skin_name,
                              "bin_price": cand.bin_price,
                              "est_cost": cand.est_cost,
                              "dry_run": dry_run}
    confirmed: Optional[int] = None
    if not dry_run:
        try:
            client.bid(cand.auction_id, cand.bin_price)
        except AMError as e:
            result["ok"] = False
            result["error"] = str(e)
            note = (note + f" bid failed: {e}").strip()
        else:
            result["ok"] = True
        if result.get("ok"):
            # Re-read rather than trust the POST: a buy-now that raced another
            # buyer still answers, and the auction itself is the only witness.
            try:
                after = client.auction(cand.auction_id)
                winner = str(((after.get("winner") or {}).get("id") or ""))
                confirmed = 1 if (after.get("isPurchased")
                                  and winner == str(client.s.player_id)) else 0
                result["confirmed"] = bool(confirmed)
            except AMError as e:
                result["confirmed"] = None
                note = (note + f" (unverified: {e})").strip()
    else:
        result["ok"] = True

    conn.execute("""
        INSERT INTO shm_buys (auction_id, skin_id, model_id, skin_name,
                              bin_price, est_cost, fee_pct, dry_run,
                              confirmed, note)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (cand.auction_id, cand.watch.skin_id, cand.watch.model_id,
          cand.skin_name, cand.bin_price, cand.est_cost,
          round(cand.est_cost / cand.bin_price * 100 - 100, 2) if cand.bin_price else 0,
          1 if dry_run else 0, confirmed, note or None))
    if not dry_run and result.get("ok") and confirmed != 0:
        conn.execute("UPDATE shm_watch SET bought = bought + 1, "
                     "active = CASE WHEN bought + 1 >= want THEN 0 ELSE 1 END "
                     "WHERE skin_id=?", (cand.watch.skin_id,))
    conn.commit()
    return result


def act_on_listings(client: AMClient, conn, listings: list[dict], *, arm: bool,
                    reserve_bids: int, daily_budget: Optional[int],
                    limits: Optional[Limits] = None,
                    arm_sources: Optional[set[str]] = None,
                    require_armed: bool = False) -> dict:
    """Evaluate one listing response and buy qualifying watches.

    When no candidate exists this performs no guard reads. A caller that scans
    repeatedly can pass a slowly refreshed Limits instance, keeping the hot
    listing-to-buy path free of three extra GETs.
    """
    watches = active_watches(conn)
    preliminary, _ = choose(listings, watches, DEFAULT_PURCHASE_FEE_PCT)
    live_preliminary = [cand for cand in preliminary
                        if arm and (not require_armed or cand.watch.armed)
                        and (arm_sources is None
                             or cand.watch.source in arm_sources)]
    if live_preliminary and limits is None:
        limits = read_limits(client, conn, reserve_bids, daily_budget,
                             strict=True)
    if limits is None:
        limits = Limits(reserve_bids=reserve_bids, daily_budget=daily_budget)
    else:
        limits.reserve_bids = reserve_bids
        limits.daily_budget = daily_budget
    candidates, skipped = choose(listings, watches, limits.fee_pct)

    log: list[str] = []
    for item in skipped:
        log.append(f"    - skip {item.label}: {item.reason}")

    _, spend = spent_today(conn)
    bought: list[dict] = []
    for cand in candidates:
        live_buy = (arm and (not require_armed or cand.watch.armed)
                    and (arm_sources is None
                         or cand.watch.source in arm_sources))
        if live_buy and limits.bids_left <= 0:
            log.append(f"    ! daily bid cap reached ({limits.bids_used}/"
                       f"{limits.max_bids_per_day}) - skipping")
            continue
        if (live_buy and limits.daily_budget is not None
                and spend + cand.est_cost > limits.daily_budget):
            log.append(f"    ! {cand.skin_name} would break the daily budget "
                       f"({fmt_money(spend + cand.est_cost)} > "
                       f"{fmt_money(limits.daily_budget)}) - skipping")
            continue
        if (live_buy and limits.balance is not None
                and cand.est_cost >= limits.balance):
            log.append(f"    ! {cand.skin_name} costs {fmt_money(cand.est_cost)}, "
                       f"balance is {fmt_money(limits.balance)} - keeping a positive balance")
            continue
        log.append(f"    * {'BUY' if live_buy else 'would buy'} {cand.skin_name} @ "
                   f"{fmt_money(cand.bin_price)} (auction {cand.auction_id})")
        result = buy(client, conn, cand, dry_run=not live_buy)
        bought.append(result)
        if live_buy and result.get("ok"):
            limits.bids_used += 1
            spend += cand.est_cost
            if limits.balance is not None:
                limits.balance -= cand.est_cost
            log.append("      confirmed" if result.get("confirmed") else
                       "      NOT confirmed - the re-read did not show us as the buyer")
        elif live_buy:
            log.append(f"      failed: {result.get('error')}")

    if not candidates:
        log.append("    nothing on the watchlist is buyable right now")
    return {"bought": bought, "bids_left": limits.bids_left,
            "fee_pct": limits.fee_pct, "log": log,
            "candidates": len(candidates)}


def one_pass(client: AMClient, conn, *, arm: bool, per_pass: int,
             sweep: bool, pool_only: bool, reserve_bids: int,
             daily_budget: Optional[int], verbose: bool = True,
             limits: Optional[Limits] = None,
             require_armed: bool = False) -> dict:
    """Look once, buy what qualifies. Returns a report; prints only if asked.

    The MCP server drives this over stdio, where a stray print corrupts the
    protocol, so every line goes into `log` and printing is the caller's call.
    """
    log: list[str] = []

    def say(line: str) -> None:
        log.append(line)
        if verbose:
            print(line)

    watches = active_watches(conn)
    if not watches:
        say("  watchlist empty — nothing to look for")
        return {"ok": True, "watched": 0, "requests": 0, "bought": [], "log": log}

    listings, requests = collect(client, conn, watches, per_pass,
                                 sweep=sweep, pool_only=pool_only)
    action = act_on_listings(
        client, conn, listings, arm=arm, reserve_bids=reserve_bids,
        daily_budget=daily_budget, limits=limits, require_armed=require_armed)

    stale = sum(1 for w in watches if w.model_id is None)
    say(f"  {len(watches)} liveries watched"
        + (f" ({stale} without a model id — sweep-only)" if stale else "")
        + f", {len(listings)} listings read in {requests} request(s)")
    for line in action["log"]:
        say(line)
    return {"ok": True, "watched": len(watches), "requests": requests,
            "listings": len(listings), "bids_left": action["bids_left"],
            "fee_pct": action["fee_pct"], "bought": action["bought"], "log": log}


# ── CLI ─────────────────────────────────────────────────────────────────────
def _client(store: bool = True,
            min_delay: float = DEFAULT_REQUEST_DELAY) -> AMClient:
    session = AMSession.load()
    return AMClient(session, min_delay=min_delay,
                    store=MobileStore() if store else None)


class WatcherAlreadyRunning(RuntimeError):
    pass


@contextlib.contextmanager
def watcher_lock(path: Optional[str] = None):
    """Allow only one continuous watcher to own an account database."""
    lock_path = Path(path) if path else Path(f"{_db.DB}.shm-watcher.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    acquired = False
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError as exc:
            raise WatcherAlreadyRunning(
                f"another SHM watcher owns {lock_path}") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        yield lock_path
    finally:
        try:
            if acquired:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _spread(interval: float, jitter: float) -> float:
    """Small timing spread to avoid synchronized load spikes."""
    return max(15.0, interval * (1 + random.uniform(-jitter, jitter)))


def _quiet_now(window: Optional[str]) -> bool:
    """`--quiet 02:00-08:00` in local time, wrapping past midnight."""
    if not window:
        return False
    start, end = (datetime.strptime(p, "%H:%M").time()
                  for p in window.split("-", 1))
    now = datetime.now().time()
    return start <= now < end if start <= end else (now >= start or now < end)


def cmd_add(args) -> int:
    conn = open_db()
    w = add_watch(conn, args.skin_id, args.max, args.want, "manual", args.label)
    where = f"model {w.model_id}" if w.model_id else "NO model id (sweep-only)"
    cap = fmt_money(w.max_price) if w.max_price else "any price"
    print(f"watching {w.label} (skin {w.skin_id}, {where}) up to {cap} ×{w.want}")
    return 0


def cmd_add_booster(args) -> int:
    conn = open_db()
    skins = booster_skins(conn, args.booster_id,
                          args.include_manufacturer)
    if not skins:
        print(f"no cards for booster {args.booster_id} — run booster_sync.py first",
              file=sys.stderr)
        return 1
    for skin_id, label in skins:
        w = add_watch(conn, skin_id, args.max, args.want,
                      f"booster:{args.booster_id}", label)
        flag = "" if w.model_id else "  (no model id — sweep-only)"
        print(f"  + {w.label} (skin {skin_id}){flag}")
    models = {w.model_id for w in active_watches(conn) if w.model_id is not None}
    print(f"{len(skins)} liveries watched across {len(models)} models "
          f"— a full pass costs {len(models)} request(s)")
    return 0


def cmd_sync_paid_packs(args) -> int:
    conn = open_db()
    result = sync_paid_pack_watches(conn, max_price=args.max)
    cap = fmt_money(args.max) if args.max is not None else "observe only"
    for row in result["liveries"]:
        print(f"  + {row['name']} (skin {row['skin_id']}, model "
              f"{row['model_id'] or 'unknown'})")
    print(f"{result['targets']} missing paid-pack liveries across "
          f"{result['models']} models; {result['added']} new watch(es), "
          f"{result['updated']} cap update(s), "
          f"{result['disabled']} acquired watch(es) disabled; {cap}")
    return 0


def cmd_list(args) -> int:
    conn = open_db()
    rows = conn.execute("""
        SELECT w.*, (SELECT COUNT(*) FROM shm_sightings s
                      WHERE s.skin_id = w.skin_id) AS sightings
          FROM shm_watch w ORDER BY w.active DESC, w.model_id, w.skin_id
    """).fetchall()
    if not rows:
        print("watchlist is empty")
        return 0
    print(f"{'skin':>9}  {'model':>5}  {'cap':>10}  {'got':>5}  {'seen':>4}  livery")
    for r in rows:
        cap = fmt_money(r["max_price"]) if r["max_price"] else "any"
        mark = "" if r["active"] else "  [done]"
        print(f"{r['skin_id']:>9}  {r['model_id'] or '-':>5}  {cap:>10}  "
              f"{r['bought']}/{r['want']:<3}  {r['sightings']:>4}  "
              f"{r['label']}{mark}")
    n, spend = spent_today(conn)
    print(f"\ntoday: {n} buy(s), {fmt_money(spend)}")
    return 0


def cmd_prices(args) -> int:
    """What the watched liveries have actually been listed at.

    The point of keeping sightings: a `--max` picked off this table is a real
    number, where one picked off a single scan is whatever happened to be on
    the market that minute.
    """
    conn = open_db()
    where = "" if args.all else "JOIN shm_watch w ON w.skin_id = s.skin_id"
    rows = conn.execute(f"""
        SELECT s.skin_name AS name, COUNT(*) AS n,
               MIN(s.bin_price) AS lo, MAX(s.bin_price) AS hi,
               MIN(s.last_seen) AS since
          FROM shm_sightings s {where}
         WHERE s.bin_price > 0 AND s.is_own = 0
         GROUP BY s.skin_id ORDER BY lo
    """).fetchall()
    if not rows:
        print("no sightings yet — run a scan first")
        return 0
    print(f"{'seen':>4}  {'cheapest':>10}  {'dearest':>10}  livery")
    for r in rows[:args.limit]:
        print(f"{r['n']:>4}  {fmt_money(r['lo']):>10}  {fmt_money(r['hi']):>10}  "
              f"{r['name']}")
    return 0


def cmd_remove(args) -> int:
    conn = open_db()
    n = remove_watch(conn, args.skin_id)
    print(f"removed {n} entr{'y' if n == 1 else 'ies'}")
    return 0


def cmd_scan(args) -> int:
    conn = open_db()
    client = _client()
    try:
        one_pass(client, conn, arm=args.arm, per_pass=args.per_pass,
                 sweep=not args.no_sweep, pool_only=args.pool_only,
                 reserve_bids=args.reserve_bids, daily_budget=args.budget)
    finally:
        client.close()
    return 0


def cmd_run(args) -> int:
    conn = open_db()
    sync_automatic_watches(conn)
    watches = active_watches(conn)
    if not watches:
        print("watchlist is empty - nothing to run")
        return 0
    sweep_interval = args.interval or args.sweep_interval
    arm_sources = ({PAID_PACK_SOURCE}
                   if args.arm_paid_packs and not args.arm else None)
    require_armed = not args.arm and not args.arm_paid_packs
    cache = LimitCache(ttl=args.limits_interval)
    sweeps = 0
    failures = 0
    backoff_until = 0.0
    priority_turn = True
    client = None
    try:
        with watcher_lock(args.lock_file):
            client = _client(min_delay=args.min_delay)
            now = time.monotonic()
            next_sweep = now
            next_model = now + args.model_interval / 2
            while args.max_passes == 0 or sweeps < args.max_passes:
                if _quiet_now(args.quiet):
                    print(f"[{datetime.now():%H:%M:%S}] quiet hours - idle 300s")
                    time.sleep(300)
                    continue
                now = time.monotonic()
                if now < backoff_until:
                    time.sleep(min(5.0, backoff_until - now))
                    continue
                did_work = False
                try:
                    live_buying = (args.arm or args.arm_paid_packs
                                   or has_armed_watches(conn))
                    if live_buying and cache.due(now):
                        cache.refresh(client, conn, args.reserve_bids,
                                      args.budget, now=now, strict=True)
                        did_work = True

                    if not args.no_sweep and now >= next_sweep:
                        listings = collect_sweep(client, conn,
                                                 pool_only=args.pool_only)
                        result = act_on_listings(
                            client, conn, listings, arm=live_buying,
                            reserve_bids=args.reserve_bids,
                            daily_budget=args.budget, limits=cache.limits,
                            arm_sources=arm_sources, require_armed=require_armed)
                        sweeps += 1
                        print(f"[{datetime.now():%H:%M:%S}] newest sweep {sweeps}: "
                              f"{len(listings)} listings")
                        for line in result["log"]:
                            if result["candidates"] or "nothing" not in line:
                                print(line)
                        next_sweep = time.monotonic() + _spread(
                            sweep_interval, args.jitter)
                        failures = 0
                        did_work = True

                    now = time.monotonic()
                    if now >= next_model:
                        model_id = plan_next_model(
                            conn, active_watches(conn), priority_turn)
                        priority_turn = not priority_turn
                        if model_id is not None:
                            listings = collect_model(
                                client, conn, model_id,
                                pool_only=args.pool_only)
                            result = act_on_listings(
                                client, conn, listings, arm=live_buying,
                                reserve_bids=args.reserve_bids,
                                daily_budget=args.budget, limits=cache.limits,
                                arm_sources=arm_sources, require_armed=require_armed)
                            print(f"[{datetime.now():%H:%M:%S}] model {model_id}: "
                                  f"{len(listings)} listings")
                            for line in result["log"]:
                                if result["candidates"] or "nothing" not in line:
                                    print(line)
                        if args.no_sweep:
                            sweeps += 1
                        next_model = time.monotonic() + _spread(
                            args.model_interval, args.jitter)
                        failures = 0
                        did_work = True
                except AMError as exc:
                    failures += 1
                    backoff = min(3600.0, 60.0 * (2 ** (failures - 1)))
                    print(f"  ! {exc}; backing off {backoff:.0f}s", file=sys.stderr)
                    backoff_until = time.monotonic() + backoff
                    next_sweep = next_model = backoff_until
                    continue

                if not did_work:
                    due = [next_model]
                    if not args.no_sweep:
                        due.append(next_sweep)
                    if live_buying and cache.limits is not None:
                        due.append(cache.refreshed_at + cache.ttl)
                    nap = max(0.1, min(5.0, min(due) - time.monotonic()))
                    time.sleep(nap)
    except KeyboardInterrupt:
        print("\nstopped")
    except WatcherAlreadyRunning as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        if client is not None:
            client.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="watch one livery by skin id")
    a.add_argument("skin_id", type=int)
    a.add_argument("--max", type=parse_money, help="highest buy-now to accept")
    a.add_argument("--want", type=int, default=1)
    a.add_argument("--label")
    a.set_defaults(func=cmd_add)

    b = sub.add_parser("add-booster", help="watch every livery in a booster")
    b.add_argument("booster_id", type=int)
    b.add_argument("--max", type=parse_money)
    b.add_argument("--want", type=int, default=1)
    b.add_argument("--include-manufacturer", action="store_true",
                   help="also watch the plain manufacturer paints")
    b.set_defaults(func=cmd_add_booster)

    pp = sub.add_parser(
        "sync-paid-packs",
        help="watch missing special liveries found in real-money packs")
    pp.add_argument("--max", type=parse_money,
                    help="one cap for newly added watches; omitted is observe-only")
    pp.set_defaults(func=cmd_sync_paid_packs)

    sub.add_parser("list", help="show the watchlist").set_defaults(func=cmd_list)

    pr = sub.add_parser("prices", help="listed prices seen so far")
    pr.add_argument("--all", action="store_true",
                    help="every livery seen, not just watched ones")
    pr.add_argument("--limit", type=int, default=40)
    pr.set_defaults(func=cmd_prices)

    r = sub.add_parser("remove", help="stop watching a livery")
    r.add_argument("skin_id", type=int)
    r.set_defaults(func=cmd_remove)

    for name, fn in (("scan", cmd_scan), ("run", cmd_run)):
        p = sub.add_parser(name, help="one pass" if name == "scan"
                           else "keep watching until interrupted")
        p.add_argument("--arm", action="store_true",
                       help="actually buy; without it nothing is spent")
        p.add_argument("--per-pass", type=int, default=12,
                       help="models per one-shot scan; continuous run uses one")
        p.add_argument("--no-sweep", action="store_true",
                       help="skip the unfiltered newest-100 read")
        p.add_argument("--pool-only", action="store_true",
                       help="restrict to your own star pool")
        p.add_argument("--reserve-bids", type=int, default=0,
                       help="leave this many of the day's bids unused")
        p.add_argument("--budget", type=parse_money,
                       help="cap total spend per UTC day")
        if name == "run":
            p.set_defaults(reserve_bids=5)
            p.add_argument(
                "--arm-paid-packs", action="store_true",
                help="buy paid-pack watches only; all other watches stay dry-run")
            p.add_argument("--sweep-interval", type=float, default=60.0,
                           help="seconds between newest-100 reads")
            p.add_argument("--model-interval", type=float, default=45.0,
                           help="seconds between single-model reads")
            p.add_argument("--limits-interval", type=float, default=600.0,
                           help="seconds between balance and bid-limit refreshes")
            p.add_argument("--min-delay", type=float, default=DEFAULT_REQUEST_DELAY,
                           help="minimum seconds between any API requests")
            p.add_argument("--interval", type=float,
                           help="legacy alias for --sweep-interval")
            p.add_argument("--jitter", type=float, default=0.1,
                           help="small timing spread to avoid synchronized load")
            p.add_argument("--quiet", help="idle window, e.g. 02:00-08:00")
            p.add_argument("--max-passes", type=int, default=0)
            p.add_argument("--lock-file",
                           help="override the single-instance lock path")
        p.set_defaults(func=fn)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
