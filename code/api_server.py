#!/usr/bin/env python3
"""
Autopilot AM — Modern Web API Server.

Serves the REST API for Fleet Operations, Livery Collection, and Game Automation,
plus the compiled modern React/Vite web application.

Usage:
    python3 code/api_server.py                  # runs on http://127.0.0.1:8000
    python3 code/api_server.py --port 8080      # custom port
"""

import argparse
import asyncio
import hashlib
import os
import sys
import threading
import time
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import (
    get_command_center_snapshot,
    get_db,
    get_fleet_aircraft,
    get_fleet_aircraft_page,
    get_daily_fleet_liveries,
    get_fleet_summary_stats,
    get_livery_collection,
    get_shm_monitor_snapshot,
    get_skin_image_bytes,
    fetch_missing_skin_images,
    resolve_skin_ids,
    update_aircraft_tags,
    upsert_fleet,
)
from cdp import CDP, ensure_am_tab, get_am_tab
from planning_page import navigate_to_planning, select_hub, get_aircraft_at_hub
from aircraft_numberer import get_form_token, rename

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIST = os.path.join(REPO_ROOT, "web", "dist")

app = FastAPI(title="Autopilot AM API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_TRANSPARENT_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00"
    b"\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00"
    b"\x00IEND\xaeB`\x82"
)


def _browser_connected() -> bool:
    """Treat an unreachable local CDP endpoint as offline, not as an API error."""
    try:
        return bool(get_am_tab())
    except Exception:
        return False


def _mobile_configured() -> bool:
    """Whether a reusable mobile session is present, without making a network call."""
    try:
        from mobile_api import AMSession
        session = AMSession.load()
        return bool(session.access_token or session.can_renew)
    except Exception:
        return False


# ── REST ENDPOINTS ──────────────────────────────────────────────────────────

@app.get("/api/stats")
def get_stats():
    """Return high-level fleet and livery collection stats."""
    return get_fleet_summary_stats()


@app.get("/api/status")
def get_status():
    """Return the real local connection and cached-data status for the web UI."""
    db = get_db()
    last_sync = db.execute("SELECT MAX(updated_at) AS value FROM fleet").fetchone()["value"]
    return {
        "browser_connected": _browser_connected(),
        "fleet_last_synced": last_sync,
        "api_version": app.version,
    }


def _am_tab_state(tab, timeout: float = 8.0) -> str:
    """Login/usability state of an AM tab: 'in' | 'out' | 'unreachable' | 'unknown'.

    'in'/'out' come from the authenticated header resource bar (`#ressource3`,
    the same element get_balance reads), which exists only when signed in.
    'unreachable' means the CDP websocket handshake was refused — the classic
    sign of a *different* Chrome holding the debug port without
    --remote-allow-origins=*, which can't be driven at all (sync, rename, etc.
    would all fail on it too). 'unknown' is genuine ambiguity (never settled).
    """
    try:
        cdp = CDP(tab["webSocketDebuggerUrl"])
        cdp.connect()
    except Exception:
        return "unreachable"
    try:
        import time as _time
        end = _time.monotonic() + timeout
        while _time.monotonic() < end:
            # Wait for the page to finish loading before calling the resource
            # bar's absence a logout, so a still-loading tab isn't misread.
            state = cdp.eval(
                "document.querySelector('#ressource3') ? 'in' : "
                "(document.readyState === 'complete' ? 'out' : 'loading')"
            )
            if state == "in":
                return "in"
            if state == "out":
                return "out"
            _time.sleep(0.5)
        return "unknown"
    finally:
        cdp.close()


@app.post("/api/launch-browser")
def launch_browser():
    """Start Chrome with CDP (and an AM tab) when the game link is offline.

    Idempotent: if a browser is already connected this returns immediately.
    Otherwise it runs launch_chrome.sh detached, waits for the debug port,
    and opens an Airlines Manager tab. CDP being reachable is not the same as
    being signed in — a launched or attached tab can sit on /login — so we also
    probe the real login state and warn when the tab is connected but logged out
    (e.g. a different Chrome is holding the debug port with the wrong profile).
    """
    tab = ensure_am_tab(timeout=45.0)
    connected = bool(tab)
    if not connected:
        return {
            "status": "timeout",
            "browser_connected": False,
            "logged_in": False,
            "message": "Could not reach Chrome on the debug port in time. Try again.",
        }

    state = _am_tab_state(tab)
    if state == "in":
        message = "Browser connected and signed in."
    elif state == "out":
        message = ("Chrome is connected but not signed in. Sign in on the AM "
                   "tab, or quit that Chrome and launch again to use your saved "
                   "logged-in profile.")
    elif state == "unreachable":
        message = ("A different Chrome is holding the debug port and can't be "
                   "controlled (started without --remote-allow-origins). Quit it, "
                   "then launch again to use your saved logged-in profile.")
    else:
        message = "Browser connected. If a login page opened, sign in to finish linking."

    return {
        "status": "ok",
        "browser_connected": True,
        "logged_in": state == "in",
        "usable": state in ("in", "out", "unknown"),
        "message": message,
    }


@app.get("/api/command-center")
def command_center():
    """Return one operational snapshot for the workflow-first home screen."""
    return get_command_center_snapshot(
        browser_connected=_browser_connected(),
        mobile_configured=_mobile_configured(),
    )


@app.get("/api/shm-monitor")
def shm_monitor():
    """Return the watcher's cached activity without touching the game API."""
    return get_shm_monitor_snapshot()


class ShmWatchArmUpdate(BaseModel):
    armed: bool


@app.patch("/api/shm-monitor/watches/{skin_id}")
def set_shm_watch_arm(skin_id: int, update: ShmWatchArmUpdate):
    """Arm or disarm one standing order without interrupting observation."""
    import shm_watcher

    conn = shm_watcher.open_db()
    if not shm_watcher.set_watch_armed(conn, skin_id, update.armed):
        raise HTTPException(status_code=404, detail="SHM watch not found")
    return {"ok": True, "skin_id": skin_id, "armed": update.armed}


@app.get("/api/fleet")
def list_fleet(
    hubs: Optional[str] = Query(None, description="Comma-separated hubs, e.g. FRA,MPM"),
    models: Optional[str] = Query(None, description="Comma-separated models"),
    min_util: Optional[float] = Query(None),
    max_util: Optional[float] = Query(None),
    name_query: Optional[str] = Query(None),
    model_query: Optional[str] = Query(None),
    skin_filter: Optional[str] = Query(None),
    skin_id: Optional[int] = Query(None),
    haul: Optional[str] = Query(None, description="short | medium | long | cargo | all"),
    tag: Optional[str] = Query(None, description="Exact custom aircraft tag"),
    sort_by: Optional[str] = Query("name"),
    limit: Optional[int] = Query(None),
    offset: Optional[int] = Query(None),
):
    """Query fleet with filtering, sorting, and pagination."""
    hub_list = [h.strip().upper() for h in hubs.split(",") if h.strip()] if hubs else None
    model_list = [m.strip() for m in models.split(",") if m.strip()] if models else None

    return get_fleet_aircraft(
        hubs=hub_list,
        models=model_list,
        min_util=min_util,
        max_util=max_util,
        name_query=name_query,
        model_query=model_query,
        skin_filter=skin_filter,
        skin_id=skin_id,
        haul=haul,
        tag=tag,
        sort_by=sort_by or "name",
        limit=limit,
        offset=offset,
    )


@app.get("/api/fleet-page")
def fleet_page(
    q: Optional[str] = Query(None),
    hubs: Optional[str] = Query(None),
    min_util: Optional[float] = Query(None),
    max_util: Optional[float] = Query(None),
    skin_filter: Optional[str] = Query(None),
    haul: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    sort_by: Optional[str] = Query("name"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Return a fleet page with total count for the operations grid."""
    hub_list = [h.strip().upper() for h in hubs.split(",") if h.strip()] if hubs else None
    return get_fleet_aircraft_page(
        query=q,
        hubs=hub_list,
        min_util=min_util,
        max_util=max_util,
        skin_filter=skin_filter,
        haul=haul,
        tag=tag,
        sort_by=sort_by or "name",
        limit=limit,
        offset=offset,
    )


class AircraftTagUpdate(BaseModel):
    aircraft_ids: List[int]
    add: List[str] = Field(default_factory=list)
    remove: List[str] = Field(default_factory=list)


@app.patch("/api/fleet/tags")
def patch_fleet_tags(req: AircraftTagUpdate):
    """Bulk-add or remove local custom tags without changing the game fleet."""
    try:
        tags = update_aircraft_tags(req.aircraft_ids, add=req.add, remove=req.remove)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"aircraft": [{"aircraft_id": aircraft_id, "tags": current}
                          for aircraft_id, current in tags.items()]}


# ── PURCHASE-DATE BACKFILL ──────────────────────────────────────────────────
# The compact fleet read has no purchase dates, and one profile request per
# aircraft is far too slow to hold a sync request open (see purchase_date_sync).
# So a fleet sync kicks the sweep off in a background thread and the UI polls
# `/api/fleet/purchase-dates` for progress.

_backfill_lock = threading.Lock()
_backfill_thread: Optional[threading.Thread] = None
_backfill_stop = threading.Event()
_backfill_state: dict = {
    "state": "idle",       # idle | running | done | stopped | error
    "done": 0,
    "failed": 0,
    "total": 0,
    "pending": None,       # aircraft still missing a date, refreshed per run
    "message": None,
    "started_at": None,
    "finished_at": None,
}


def _backfill_eta_minutes(pending: int) -> int:
    """Rough minutes for a paced sweep: ~1.5s per profile plus the jittered gap."""
    from purchase_date_sync import MAX_PAUSE, MIN_PAUSE
    seconds = pending * (1.5 + (MIN_PAUSE + MAX_PAUSE) / 2)
    return max(1, round(seconds / 60))


def _backfill_snapshot() -> dict:
    with _backfill_lock:
        snapshot = dict(_backfill_state)
    if snapshot["pending"] is None:
        from purchase_date_sync import missing_aircraft_ids
        snapshot["pending"] = len(missing_aircraft_ids())
    snapshot["running"] = snapshot["state"] == "running"
    snapshot["eta_minutes"] = (_backfill_eta_minutes(snapshot["pending"])
                               if snapshot["running"] and snapshot["pending"] else 0)
    return snapshot


def _run_purchase_backfill(workers: int, limit: Optional[int]) -> None:
    from purchase_date_sync import backfill

    def progress(done: int, failed: int, total: int) -> None:
        with _backfill_lock:
            _backfill_state.update(done=done, failed=failed, total=total,
                                   pending=max(0, total - done))

    try:
        result = backfill(workers=workers, limit=limit, progress=progress,
                          should_stop=_backfill_stop.is_set)
    except Exception as exc:                      # noqa: BLE001 — background job
        with _backfill_lock:
            _backfill_state.update(state="error", message=str(exc),
                                   finished_at=time.time(), pending=None)
        return

    if result["error"]:
        state, message = "error", result["error"]
    elif result["stopped"]:
        state, message = "stopped", f"Stopped after {result['done']} purchase dates."
    elif result["total"] == 0:
        state, message = "done", "Every aircraft already has a purchase date."
    else:
        state, message = "done", (f"Cached {result['done']} purchase date(s)"
                                  + (f", {result['failed']} failed." if result["failed"] else "."))
    with _backfill_lock:
        _backfill_state.update(state=state, message=message, done=result["done"],
                               failed=result["failed"], total=result["total"],
                               pending=None, finished_at=time.time())


def _start_purchase_backfill(workers: int = 0, limit: Optional[int] = None) -> dict:
    """Start the sweep unless one is already running. Returns the status snapshot."""
    global _backfill_thread
    from purchase_date_sync import DEFAULT_WORKERS

    with _backfill_lock:
        if _backfill_thread is not None and _backfill_thread.is_alive():
            return dict(_backfill_state, running=True)
        _backfill_stop.clear()
        _backfill_state.update(state="running", done=0, failed=0, total=0,
                               pending=None, message=None, started_at=time.time(),
                               finished_at=None)
        _backfill_thread = threading.Thread(
            target=_run_purchase_backfill,
            args=(workers or DEFAULT_WORKERS, limit),
            name="purchase-date-backfill", daemon=True)
        _backfill_thread.start()
        return dict(_backfill_state, running=True)


@app.get("/api/fleet/purchase-dates")
def purchase_backfill_status():
    """Progress of the background purchase-date sweep, for the fleet page to poll."""
    return _backfill_snapshot()


@app.post("/api/fleet/purchase-dates")
def start_purchase_backfill(workers: int = Query(0, ge=0, le=16),
                            limit: Optional[int] = Query(None, ge=1)):
    """Start the sweep by hand; a sync starts the same job automatically."""
    if not _mobile_configured():
        raise HTTPException(status_code=503,
                            detail="No mobile session — import one before backfilling dates.")
    return _start_purchase_backfill(workers=workers, limit=limit)


@app.delete("/api/fleet/purchase-dates")
def stop_purchase_backfill():
    """Ask a running sweep to stop after its in-flight requests."""
    _backfill_stop.set()
    return _backfill_snapshot()


@app.get("/api/fleet/{aircraft_id}/purchase-date")
def aircraft_purchase_date(aircraft_id: int):
    """Return and cache one aircraft's exact purchase timestamp.

    The mobile compact-fleet endpoint omits this field. Fetching every profile
    during a fleet sync would turn six requests into thousands, so this endpoint
    reads only the requested profile and subsequent calls stay local.
    """
    db = get_db()
    row = db.execute(
        "SELECT f.aircraft_id, m.purchased_at "
        "FROM fleet f LEFT JOIN mobile_aircraft m ON m.aircraft_id = f.aircraft_id "
        "WHERE f.aircraft_id = ?",
        (aircraft_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Aircraft not found in the synced fleet")
    if row["purchased_at"]:
        return {"aircraft_id": aircraft_id, "purchased_at": row["purchased_at"],
                "cached": True}

    from mobile_api import AMClient, AMError, AMSession
    from mobile_store import MobileStore

    client = None
    try:
        store = MobileStore(db)
        client = AMClient(AMSession.load(), store=store)
        profile = client.aircraft(aircraft_id)
    except AMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        if client is not None:
            client.close()

    purchased = profile.get("purchasedAt") if profile else None
    if isinstance(purchased, dict):
        purchased = purchased.get("date")
    if not purchased:
        raise HTTPException(status_code=502, detail="The game did not return a purchase date")
    return {"aircraft_id": aircraft_id, "purchased_at": purchased, "cached": False}


@app.get("/api/liveries")
def list_liveries(
    status_filter: Optional[str] = Query(None, description="'owned', 'unowned', or None"),
    model_query: Optional[str] = Query(None),
    search_query: Optional[str] = Query(None),
    include_user_created: bool = Query(True, description="Include player-designed market liveries"),
):
    """Return special/custom liveries (strictly excluding manufacturer liveries)."""
    return get_livery_collection(
        include_manufacturer=False,
        include_user_created=include_user_created,
        status_filter=status_filter,
        model_query=model_query,
        search_query=search_query,
    )


@app.get("/api/liveries/daily")
def liveries_of_the_day(count: int = Query(3, ge=1, le=12), seed: str | None = Query(None)):
    """Return today's rotating pick of special liveries flown by the fleet.

    An optional `seed` reshuffles the pick for the manual reroll button.
    """
    return get_daily_fleet_liveries(count=count, seed=seed)


def _livery_sync_snapshot(store) -> dict:
    """Small, stable summary for the website's reward-feed sync result."""
    queries = {
        "booster_skins": (
            "SELECT COUNT(DISTINCT skin_id) FROM mobile_booster_cards "
            "WHERE skin_id IS NOT NULL"
        ),
        "shop_skins": (
            "SELECT COUNT(DISTINCT skin_id) FROM mobile_shop_offer_items "
            "WHERE skin_id IS NOT NULL"
        ),
        "challenge_skins": (
            "SELECT COUNT(DISTINCT skin_id) FROM mobile_challenge_rewards "
            "WHERE skin_id IS NOT NULL"
        ),
        "images": "SELECT COUNT(*) FROM mobile_skin_images WHERE size = 'big'",
    }
    summary = {
        name: store.conn.execute(sql).fetchone()[0] or 0
        for name, sql in queries.items()
    }
    summary["catalog_skins"] = len(get_livery_collection(
        include_manufacturer=False,
        include_user_created=True,
    ))
    return summary


@app.post("/api/sync-liveries")
def sync_liveries():
    """Refresh booster, shop, and active-challenge liveries plus their artwork.

    These are mobile API feeds, so the operation does not need a Chrome tab and
    does not perform any purchase or reward-claim mutation.
    """
    from booster_sync import sync_droprates, sync_images
    from mobile_api import AMClient, AMSession
    from mobile_store import MobileStore
    from skin_name_sync import classify_by_artwork, sync_challenge, sync_shop

    store = MobileStore()
    before = _livery_sync_snapshot(store)
    client = None
    try:
        session = AMSession.load()
        session.renew()
        client = AMClient(session, store=store)
        sync_droprates(client, store, dry_run=False)
        sync_shop(client, store, dry_run=False)
        sync_challenge(client, store, dry_run=False)
        import shm_watcher
        shm_watcher.sync_automatic_watches(shm_watcher.open_db())
        sync_images(client, store, "big", all_skins=False, limit=0, dry_run=False)
        classify_by_artwork(store, dry_run=False)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Livery catalog sync failed: {exc}",
        ) from exc
    finally:
        if client is not None:
            client.close()

    after = _livery_sync_snapshot(store)
    new_liveries = max(0, after["catalog_skins"] - before["catalog_skins"])
    new_images = max(0, after["images"] - before["images"])
    return {
        "status": "success",
        "message": (
            f"Reward catalog synced: {after['booster_skins']} booster, "
            f"{after['shop_skins']} shop, and {after['challenge_skins']} challenge "
            f"skin records. Added {new_liveries} special liveries and downloaded "
            f"{new_images} images."
        ),
        "new_liveries": new_liveries,
        "images_fetched": new_images,
        **after,
    }


@app.get("/api/skin_image/{skin_id}")
@app.get("/api/skin_image/{skin_id}/{size}")
def get_skin_image(skin_id: int, size: str = "big", request: Request = None):
    """Serve livery PNG image bytes directly from SQLite image cache.

    The image behind a given skin_id can change (a livery re-fetched, or a wrong
    blob corrected), so the bytes are cached with a content ETag under
    ``no-cache`` rather than a fixed max-age: the browser revalidates every load
    and gets a cheap 304 while the art is unchanged, but the moment the bytes
    change the ETag changes and the new art is served. A fixed max-age would pin
    a week-stale (possibly wrong) image in the browser.
    """
    data = get_skin_image_bytes(skin_id, size)
    payload = data or _TRANSPARENT_PNG
    etag = '"' + hashlib.sha1(payload).hexdigest() + '"'
    if request is not None and request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
    return Response(
        content=payload,
        media_type="image/png",
        headers={"ETag": etag, "Cache-Control": "no-cache"},
    )


class SyncRequest(BaseModel):
    hub: Optional[str] = None


def _mobile_items_to_fleet(items, hub_by_id, model_by_aircraft, model_by_id,
                           requested_hub=None):
    """Translate compact mobile records to the warehouse fleet contract.

    Model ids are shared by the mobile and web APIs, but the compact mobile
    response carries only that id. Learn any missing id-to-name pairs from the
    previous CDP snapshot. If a genuinely new model cannot be named, reject the
    mobile result so the caller can use CDP instead of writing incomplete rows.
    """
    learned_models = {}
    for item in items:
        aircraft_id = item.get("id")
        model_id = item.get("al_id")
        previous_model = model_by_aircraft.get(aircraft_id)
        if model_id is None or not previous_model:
            continue
        known = learned_models.get(model_id) or model_by_id.get(model_id)
        if known and known != previous_model:
            raise ValueError(f"model id {model_id} maps to both {known} and {previous_model}")
        learned_models[model_id] = previous_model

    models = {**model_by_id, **learned_models}
    fleet = []
    selected_hubs = set()
    for item in items:
        aircraft_id = item.get("id")
        hub_iata = hub_by_id.get(item.get("h_id"))
        model = models.get(item.get("al_id")) or model_by_aircraft.get(aircraft_id)
        if requested_hub and hub_iata != requested_hub:
            continue
        if not aircraft_id or not hub_iata or not model:
            raise ValueError(
                f"incomplete mobile aircraft {aircraft_id}: hub={hub_iata!r}, model={model!r}"
            )
        selected_hubs.add(hub_iata)
        fleet.append({
            "id": aircraft_id,
            "name": item.get("n") or "",
            "model": model,
            "util": item.get("up") or 0,
            "hub": hub_iata,
            "skin_id": item.get("as_id"),
        })
    return fleet, selected_hubs, learned_models


def _read_mobile_fleet(requested_hub=None):
    """Read the complete fleet through the mobile API without requiring Chrome."""
    from mobile_api import AMClient, AMSession
    from mobile_store import MobileStore

    db = get_db()
    hub_by_id = {row["hub_id"]: row["hub_iata"] for row in db.execute(
        "SELECT hub_id, hub_iata FROM player_hubs"
    )}
    if requested_hub and requested_hub not in set(hub_by_id.values()):
        raise HTTPException(status_code=400, detail=f"Unknown player hub: {requested_hub}")

    model_by_aircraft = {row["aircraft_id"]: row["model"] for row in db.execute(
        "SELECT aircraft_id, model FROM fleet WHERE model IS NOT NULL AND model != ''"
    )}
    model_by_id = {}
    try:
        model_by_id.update({row["model_id"]: row["name"] for row in db.execute(
            "SELECT model_id, name FROM mobile_models WHERE name IS NOT NULL AND name != ''"
        )})
    except Exception:
        pass

    store = MobileStore(db)
    client = AMClient(AMSession.load(), store=store)
    try:
        first = client.fleet_page(1, per_page=500)
        items = list(first.get("aircraftList", []))
        pagination = first.get("pagination") or {}
        page_count = int(pagination.get("pageCount") or pagination.get("last") or 1)
        expected_count = pagination.get("totalCount")
        for page in range(2, page_count + 1):
            body = client.fleet_page(page, per_page=500)
            items.extend(body.get("aircraftList", []))
    finally:
        client.close()
    if not items:
        raise ValueError("mobile fleet response was empty")
    if expected_count is not None and len(items) != int(expected_count):
        raise ValueError(
            f"mobile fleet response was incomplete: expected {expected_count}, got {len(items)}"
        )
    if len({item.get("id") for item in items}) != len(items):
        raise ValueError("mobile fleet response contained duplicate aircraft")

    fleet, selected_hubs, learned_models = _mobile_items_to_fleet(
        items, hub_by_id, model_by_aircraft, model_by_id, requested_hub
    )
    for model_id, name in learned_models.items():
        db.execute(
            "INSERT INTO mobile_models (model_id, name, last_seen) "
            "VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(model_id) DO UPDATE SET "
            "name=COALESCE(mobile_models.name, excluded.name), last_seen=datetime('now')",
            (model_id, name),
        )
    db.commit()
    # A complete mobile read covers every configured hub, including one that
    # has just become empty and therefore has no record in `selected_hubs`.
    synced_hubs = [requested_hub] if requested_hub else sorted(hub_by_id.values())
    return fleet, synced_hubs


def _read_cdp_fleet(requested_hub=None):
    """Preserved website fleet reader, used when the mobile connection fails."""
    tab = get_am_tab()
    if not tab:
        raise HTTPException(
            status_code=503,
            detail="Mobile fleet sync failed and no Chrome tab is available for fallback.",
        )

    cdp = CDP(tab["webSocketDebuggerUrl"])
    cdp.connect()
    try:
        if requested_hub:
            hubs = [requested_hub]
        else:
            db = get_db()
            hubs = [r[0] for r in db.execute("SELECT hub_iata FROM player_hubs").fetchall()]

        if not hubs:
            raise HTTPException(status_code=400, detail="No hubs found in player_hubs table")

        navigate_to_planning(cdp)

        all_fleet = []
        synced_hubs = []
        for hub in hubs:
            if select_hub(cdp, hub):
                ac_list = get_aircraft_at_hub(cdp, hub)
                for ac in ac_list:
                    ac["hub"] = hub
                all_fleet.extend(ac_list)
                synced_hubs.append(hub)
        return all_fleet, synced_hubs
    finally:
        cdp.close()


@app.post("/api/sync-fleet")
def sync_fleet(req: SyncRequest):
    """Sync through the mobile API first, with the website/CDP path as fallback."""
    requested_hub = req.hub.upper().strip() if req.hub else None
    source = "mobile"
    try:
        all_fleet, synced_hubs = _read_mobile_fleet(requested_hub)
    except HTTPException:
        raise
    except Exception:
        source = "cdp_fallback"
        all_fleet, synced_hubs = _read_cdp_fleet(requested_hub)

    resolved, unresolved = resolve_skin_ids(all_fleet)
    # A hub that was actually re-read is fully replaced: any stored aircraft
    # no longer present there (sold, scrapped) is dropped.
    upsert_fleet(all_fleet, prune_hubs=synced_hubs)

    # Pull artwork only for newly identified liveries missing from the cache.
    images_fetched, images_failed = fetch_missing_skin_images(all_fleet)
    method = "mobile API" if source == "mobile" else "browser fallback"
    msg = (f"Successfully synced {len(all_fleet)} aircraft across {len(synced_hubs)} "
           f"hubs via {method} ({resolved} liveries identified).")
    if images_fetched:
        msg += f" Downloaded {images_fetched} new livery image(s)."

    # Purchase dates need one profile request each, paced to look like ordinary
    # app traffic, so they run in a background thread and the fleet page polls
    # their progress. Only worth starting on the mobile path: the browser
    # fallback means the mobile session is down.
    purchase_backfill = None
    if source == "mobile":
        from purchase_date_sync import missing_aircraft_ids
        pending = len(missing_aircraft_ids())
        if pending:
            purchase_backfill = _start_purchase_backfill()
            msg += (f" Loading {pending} missing purchase date(s) in the background"
                    f" (~{_backfill_eta_minutes(pending)} min).")

    return {
        "status": "success",
        "source": source,
        "message": msg,
        "count": len(all_fleet),
        "resolved_liveries": resolved,
        "images_fetched": images_fetched,
        "images_failed": images_failed,
        "purchase_backfill": purchase_backfill,
    }


class RenameRequest(BaseModel):
    aircraft_ids: List[int]
    prefix: str
    add_numbering: bool = True


@app.post("/api/rename")
def bulk_rename(req: RenameRequest):
    """Bulk rename aircraft in-game using CDP."""
    tab = get_am_tab()
    if not tab:
        raise HTTPException(status_code=503, detail="Chrome CDP not connected.")

    cdp = CDP(tab["webSocketDebuggerUrl"])
    cdp.connect()
    try:
        ok, failed = 0, 0
        db = get_db()
        for idx, aid in enumerate(req.aircraft_ids, 1):
            new_name = f"{req.prefix}-{idx:03d}" if req.add_numbering else req.prefix
            tok = get_form_token(cdp, aid)
            if tok:
                st = rename(cdp, aid, new_name, tok)
                if st in (200, 302):
                    ok += 1
                    db.execute("UPDATE fleet SET name = ? WHERE aircraft_id = ?", (new_name, aid))
                    db.commit()
                else:
                    failed += 1
            else:
                failed += 1
        return {"status": "ok", "ok": ok, "failed": failed}
    finally:
        cdp.close()


class AssignCircuitRequest(BaseModel):
    aircraft_ids: List[int]
    circuit_code: str


@app.post("/api/assign-circuit")
def assign_circuit(req: AssignCircuitRequest):
    """Assign aircraft to circuit naming convention <CODE>-<001...>."""
    tab = get_am_tab()
    if not tab:
        raise HTTPException(status_code=503, detail="Chrome CDP not connected.")

    cdp = CDP(tab["webSocketDebuggerUrl"])
    cdp.connect()
    try:
        ok, failed = 0, 0
        db = get_db()
        code = req.circuit_code.upper().strip()
        for idx, aid in enumerate(req.aircraft_ids, 1):
            new_name = f"{code}-{idx:03d}"
            tok = get_form_token(cdp, aid)
            if tok:
                st = rename(cdp, aid, new_name, tok)
                if st in (200, 302):
                    ok += 1
                    db.execute("UPDATE fleet SET name = ? WHERE aircraft_id = ?", (new_name, aid))
                    db.commit()
                else:
                    failed += 1
            else:
                failed += 1
        return {"status": "ok", "ok": ok, "failed": failed}
    finally:
        cdp.close()


# ── STATIC ASSETS & SPA SERVING ─────────────────────────────────────────────

# The React application is compiled by run_web.sh and served under /app.
if os.path.exists(os.path.join(WEB_DIST, "index.html")):
    app.mount("/app", StaticFiles(directory=WEB_DIST, html=True), name="app")

    @app.get("/", include_in_schema=False)
    async def serve_app():
        return RedirectResponse(url="/app/")


def main():
    parser = argparse.ArgumentParser(description="Autopilot AM Web API Server")
    parser.add_argument("--host", default=os.getenv("AM_HOST", "127.0.0.1"), help="Host to bind")
    parser.add_argument("--port", type=int, default=int(os.getenv("AM_PORT", "8000")), help="Port to bind")
    parser.add_argument("--reload", action="store_true",
                        help="Reload the API server when Python source files change")
    args = parser.parse_args()

    print(f"✈ Autopilot AM Web App & API running at http://{args.host}:{args.port}")
    if args.reload:
        uvicorn.run(
            "api_server:app",
            host=args.host,
            port=args.port,
            log_level="info",
            reload=True,
            reload_dirs=[os.path.dirname(os.path.abspath(__file__))],
        )
    else:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
