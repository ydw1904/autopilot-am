#!/usr/bin/env python3
"""
Purchase-date backfill — fill `mobile_aircraft.purchased_at` for the synced fleet.

The mobile compact-fleet endpoint (`bfa/paged/aircraft`) omits the purchase
date, so the only source is the per-aircraft profile (`aircraft/{id}`), one
request each at roughly 1.5s. A 2.8k-aircraft fleet is therefore a couple of
hours, which is why a fleet sync never fetched them inline.

**One request at a time, with a jittered pause between them.** The traffic has
to look like the app: the real client opens aircraft profiles one by one as the
player taps them, never several at once, so a thread pool would make this sweep
the most conspicuous thing on the account. It is slow on purpose and runs in a
background thread — `api_server` starts it after every mobile fleet sync, so a
cold fleet fills in over a couple of hours and every later sync only pays for
the handful of aircraft bought since.

Usage:
    python3 code/purchase_date_sync.py                 # backfill everything missing
    python3 code/purchase_date_sync.py --limit 50      # just the first 50
    python3 code/purchase_date_sync.py --workers 2     # faster, more conspicuous
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import get_db

# One request at a time: the app never opens two aircraft profiles in parallel,
# and this sweep is long enough that a burst pattern would stand out. Raising it
# is possible (`--workers`) but trades cover for speed.
DEFAULT_WORKERS = 1
# Seconds of idle time between requests, drawn per request so the gaps are not
# a machine-perfect cadence. Roughly a tap every 2-3s including the round trip.
MIN_PAUSE, MAX_PAUSE = 0.6, 1.6


def missing_aircraft_ids(limit: Optional[int] = None) -> list[int]:
    """Aircraft in the synced fleet with no cached purchase date, oldest id first."""
    db = get_db()
    sql = ("SELECT f.aircraft_id FROM fleet f "
           "LEFT JOIN mobile_aircraft m ON m.aircraft_id = f.aircraft_id "
           "WHERE m.purchased_at IS NULL ORDER BY f.aircraft_id")
    params: list = []
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    return [row[0] for row in db.execute(sql, params).fetchall()]


def backfill(aircraft_ids: Optional[Iterable[int]] = None,
             workers: int = DEFAULT_WORKERS,
             limit: Optional[int] = None,
             pause: bool = True,
             progress: Optional[Callable[[int, int, int], None]] = None,
             should_stop: Optional[Callable[[], bool]] = None) -> dict:
    """Fetch and cache the purchase date of every aircraft still missing one.

    Reads run one at a time by default, separated by a jittered pause, so the
    traffic matches a player opening aircraft cards rather than a sweep.

    `progress(done, failed, total)` is called after each aircraft, and
    `should_stop()` is polled so a caller can cancel mid-run — a cancelled sweep
    resumes from where it stopped, since the cached dates are what defines the
    remaining work. Returns a summary dict. Per-aircraft failures are counted,
    never raised: one dead profile must not abort the sweep. Authentication
    failures do abort it, since every remaining request would fail the same way.
    """
    from mobile_api import AMAuthError, AMClient, AMSession
    from mobile_store import MobileStore

    ids = list(aircraft_ids) if aircraft_ids is not None else missing_aircraft_ids(limit)
    total = len(ids)
    if not total:
        return {"total": 0, "done": 0, "failed": 0, "stopped": False, "error": None}

    session = AMSession.load()
    db = get_db()
    local = threading.local()
    clients: list = []
    clients_lock = threading.Lock()
    counters_lock = threading.Lock()
    state = {"done": 0, "failed": 0, "error": None}

    def client_for_thread():
        client = getattr(local, "client", None)
        if client is None:
            # One httpx client per worker, all sharing the single session object
            # so a mid-sweep token renewal (they expire after 3h) is seen by
            # every thread. mobile_api serializes the renewal itself.
            client = AMClient(session, store=MobileStore(db))
            local.client = client
            with clients_lock:
                clients.append(client)
        return client

    def fetch(aircraft_id: int) -> None:
        if state["error"] or (should_stop and should_stop()):
            return
        if pause:
            # Before, not after, so the sweep never fires two requests back to
            # back even when several workers are in play.
            time.sleep(random.uniform(MIN_PAUSE, MAX_PAUSE))
        try:
            # AMClient.aircraft() caches purchased_at through the store.
            client_for_thread().aircraft(aircraft_id)
            ok = True
        except AMAuthError as exc:
            with counters_lock:
                state["error"] = str(exc)
            return
        except Exception:                       # noqa: BLE001 — best-effort sweep
            ok = False
        with counters_lock:
            if ok:
                state["done"] += 1
            else:
                state["failed"] += 1
            if progress:
                progress(state["done"], state["failed"], total)

    try:
        with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
            list(pool.map(fetch, ids))
    finally:
        for client in clients:
            client.close()

    stopped = bool(should_stop and should_stop())
    return {"total": total, "done": state["done"], "failed": state["failed"],
            "stopped": stopped, "error": state["error"]}


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill fleet purchase dates via the mobile API")
    ap.add_argument("--limit", type=int, help="only the first N aircraft missing a date")
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                    help=f"concurrent profile reads (default {DEFAULT_WORKERS}; "
                         "above 1 the traffic stops looking like the app)")
    ap.add_argument("--no-pause", action="store_true",
                    help="drop the jittered gap between requests (fast, conspicuous)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report how many aircraft are missing a date, fetch nothing")
    args = ap.parse_args()

    pending = missing_aircraft_ids(args.limit)
    if args.dry_run:
        print(f"{len(pending)} aircraft missing a purchase date")
        return 0
    if not pending:
        print("Every aircraft already has a cached purchase date.")
        return 0

    def report(done: int, failed: int, total: int) -> None:
        if (done + failed) % 25 == 0 or done + failed == total:
            print(f"\r{done + failed}/{total} ({failed} failed)", end="", flush=True)

    result = backfill(pending, workers=args.workers, pause=not args.no_pause,
                      progress=report)
    print()
    if result["error"]:
        print(f"Aborted: {result['error']}", file=sys.stderr)
        return 1
    print(f"Cached {result['done']} purchase dates ({result['failed']} failed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
