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

A pass therefore costs one request per watched model. Watchlists wider than
`--per-pass` models rotate least-recently-checked first, and every pass also
does one unfiltered newest-first sweep, which catches a watched livery the
moment it lands anywhere in the newest 100 regardless of whose turn it is.

Buying is always buy-now at the listing's own `binPrice` — never an
incremental bid — so the watcher either takes a plane at a price you capped in
advance or does nothing. It never enters a bidding war.

Nothing spends money without `--arm`.

Usage:
  code/shm_watcher.py add 4670077 --max 1.2b        # watch one livery
  code/shm_watcher.py add-booster 826 --max 2b      # watch a booster's liveries
  code/shm_watcher.py list
  code/shm_watcher.py remove 4670077
  code/shm_watcher.py scan                          # one pass, buys nothing
  code/shm_watcher.py scan --arm                    # one pass, buys
  code/shm_watcher.py run --interval 180 --arm      # keep watching
"""

from __future__ import annotations

import argparse
import logging
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
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
              label: Optional[str] = None) -> Watch:
    model_id, name = _skin_row(conn, skin_id)
    label = label or name or f"skin {skin_id}"
    conn.execute("""
        INSERT INTO shm_watch (skin_id, model_id, label, max_price, want, source)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT(skin_id) DO UPDATE SET
          model_id=COALESCE(excluded.model_id, shm_watch.model_id),
          label=excluded.label,
          max_price=excluded.max_price,
          want=excluded.want,
          source=excluded.source,
          active=1
    """, (skin_id, model_id, label, max_price, want, source))
    conn.commit()
    return Watch(skin_id, model_id, label, max_price, want, 0)


def remove_watch(conn, skin_id: int) -> int:
    cur = conn.execute("DELETE FROM shm_watch WHERE skin_id=?", (skin_id,))
    conn.commit()
    return cur.rowcount


def active_watches(conn) -> list[Watch]:
    rows = conn.execute("""
        SELECT skin_id, model_id, label, max_price, want, bought
          FROM shm_watch WHERE active=1 AND bought < want
         ORDER BY model_id IS NULL, model_id, skin_id
    """).fetchall()
    return [Watch(r["skin_id"], r["model_id"], r["label"], r["max_price"],
                  r["want"], r["bought"]) for r in rows]


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


def collect(client: AMClient, conn, watches: list[Watch], per_pass: int,
            sweep: bool = True, pool_only: bool = False) -> tuple[list[dict], int]:
    """One pass. Returns (listings seen, requests spent)."""
    seen: dict[int, dict] = {}
    requests = 0

    if sweep:
        # One unfiltered newest-first read. Cheap, and it catches a watched
        # livery on any model the rotation has not reached yet.
        for a in client.auctions(sort="timePlus", pool_only=pool_only):
            seen[a["id"]] = a
        requests += 1

    for model_id in plan_models(conn, watches, per_pass):
        got = client.auctions(sort="timePlus", pool_only=pool_only,
                              model_id=model_id)
        requests += 1
        for a in got:
            seen[a["id"]] = a
        _note_check(conn, model_id, truncated=len(got) >= AUCTION_PAGE_LIMIT)

    for a in seen.values():
        record_sighting(conn, a)
    conn.commit()
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
    the market — while `est_cost` adds the server's `purchaseFeePercent` on
    top, which is what the balance and budget guards spend against.
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
        cand = Candidate(a, w, bin_price,
                         int(round(bin_price * (1 + fee_pct / 100.0))))
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


def read_limits(client: AMClient, conn, reserve_bids: int = 0,
                daily_budget: Optional[int] = None) -> Limits:
    """Pull the server's own caps plus today's usage. Never guesses silently."""
    lim = Limits(reserve_bids=reserve_bids, daily_budget=daily_budget)
    try:
        rules = client.auction_rules()
        lim.max_bids_per_day = int(rules.get("maxBidByDay") or lim.max_bids_per_day)
        lim.fee_pct = float(rules.get("purchaseFeePercent") or lim.fee_pct)
    except AMError as e:
        print(f"  ! auction rules unreadable ({e}); using defaults", file=sys.stderr)
    local_bids, _ = spent_today(conn)
    server_bids = 0
    try:
        server_bids = int(client.my_bidding()["summary"].get("countOfBidding") or 0)
    except AMError:
        pass
    # The server counts manual bids too, the local ledger counts only ours —
    # whichever is higher is the one that can hit the cap first.
    lim.bids_used = max(local_bids, server_bids)
    try:
        lim.balance = int(client.resources().get("dollar") or 0)
    except AMError:
        pass
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


def one_pass(client: AMClient, conn, *, arm: bool, per_pass: int,
             sweep: bool, pool_only: bool, reserve_bids: int,
             daily_budget: Optional[int], verbose: bool = True) -> dict:
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
    lim = read_limits(client, conn, reserve_bids, daily_budget)
    cands, skipped = choose(listings, watches, lim.fee_pct)

    stale = sum(1 for w in watches if w.model_id is None)
    say(f"  {len(watches)} liveries watched"
        + (f" ({stale} without a model id — sweep-only)" if stale else "")
        + f", {len(listings)} listings read in {requests} request(s)")
    for sk in skipped:
        say(f"    - skip {sk.label}: {sk.reason}")

    _, spend = spent_today(conn)
    bought: list[dict] = []
    for c in cands:
        if lim.bids_left <= 0:
            say(f"    ! daily bid cap reached ({lim.bids_used}/"
                f"{lim.max_bids_per_day}) — stopping")
            break
        if lim.daily_budget is not None and spend + c.est_cost > lim.daily_budget:
            say(f"    ! {c.skin_name} would break the daily budget "
                f"({fmt_money(spend + c.est_cost)} > "
                f"{fmt_money(lim.daily_budget)}) — stopping")
            break
        if arm and c.watch.max_price is None and lim.daily_budget is None:
            # An armed buy with neither a per-livery cap nor a daily budget has
            # no ceiling at all, and SHM buy-now prices reach $8B. Refuse.
            say(f"    ! {c.skin_name} has no price cap and no budget "
                f"— refusing to buy blind")
            continue
        if lim.balance is not None and c.est_cost > lim.balance:
            say(f"    ! {c.skin_name} costs {fmt_money(c.est_cost)}, "
                f"balance is {fmt_money(lim.balance)} — skipping")
            continue
        say(f"    * {'BUY' if arm else 'would buy'} {c.skin_name} @ "
            f"{fmt_money(c.bin_price)} (≈{fmt_money(c.est_cost)} with the "
            f"{lim.fee_pct:g}% fee, auction {c.auction_id})")
        res = buy(client, conn, c, dry_run=not arm)
        bought.append(res)
        if arm and res.get("ok"):
            lim.bids_used += 1
            spend += c.est_cost
            if lim.balance is not None:
                lim.balance -= c.est_cost
            say("      confirmed" if res.get("confirmed") else
                "      NOT confirmed — the re-read did not show us as the buyer")
        elif arm:
            say(f"      failed: {res.get('error')}")

    if not cands:
        say("    nothing on the watchlist is buyable right now")
    return {"ok": True, "watched": len(watches), "requests": requests,
            "listings": len(listings), "bids_left": lim.bids_left,
            "fee_pct": lim.fee_pct, "bought": bought, "log": log}


# ── CLI ─────────────────────────────────────────────────────────────────────
def _client(store: bool = True) -> AMClient:
    session = AMSession.load()
    return AMClient(session, store=MobileStore() if store else None)


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
    client = _client()
    passes = 0
    try:
        while args.max_passes == 0 or passes < args.max_passes:
            passes += 1
            if _quiet_now(args.quiet):
                nap = 300
                print(f"[{datetime.now():%H:%M:%S}] quiet hours — idle {nap}s")
                time.sleep(nap)
                continue
            print(f"[{datetime.now():%H:%M:%S}] pass {passes}")
            try:
                one_pass(client, conn, arm=args.arm, per_pass=args.per_pass,
                         sweep=not args.no_sweep, pool_only=args.pool_only,
                         reserve_bids=args.reserve_bids, daily_budget=args.budget)
            except AMError as e:
                print(f"  ! {e}", file=sys.stderr)
            # Jittered so the traffic has no machine-obvious period.
            nap = args.interval * (1 + random.uniform(-args.jitter, args.jitter))
            time.sleep(max(15.0, nap))
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
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
                       help="models to poll per pass (0 = all); the rest rotate")
        p.add_argument("--no-sweep", action="store_true",
                       help="skip the unfiltered newest-100 read")
        p.add_argument("--pool-only", action="store_true",
                       help="restrict to your own star pool")
        p.add_argument("--reserve-bids", type=int, default=0,
                       help="leave this many of the day's bids unused")
        p.add_argument("--budget", type=parse_money,
                       help="cap total spend per UTC day")
        if name == "run":
            p.add_argument("--interval", type=float, default=180.0)
            p.add_argument("--jitter", type=float, default=0.35,
                           help="fraction of --interval to randomise by")
            p.add_argument("--quiet", help="idle window, e.g. 02:00-08:00")
            p.add_argument("--max-passes", type=int, default=0)
        p.set_defaults(func=fn)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
