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
import hashlib
import os
import sys
import threading
import time
from typing import List, Literal, Optional

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
    get_fleet_name_suggestions,
    get_daily_fleet_liveries,
    get_fleet_summary_stats,
    get_livery_collection,
    get_network_snapshot,
    get_ops_freshness,
    get_player_hub_id,
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
    return bool(get_am_tab())


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


class ShmWatchUpdate(BaseModel):
    armed: Optional[bool] = None
    max_price: Optional[float] = None


@app.patch("/api/shm-monitor/watches/{skin_id}")
def set_shm_watch(skin_id: int, update: ShmWatchUpdate):
    """Arm/disarm one standing order or reprice its cap, without pausing it."""
    import shm_watcher

    conn = shm_watcher.open_db()
    sent = update.model_fields_set
    found = True
    if update.armed is not None:
        found = shm_watcher.set_watch_armed(conn, skin_id, update.armed)
    # max_price is nullable on purpose: an explicit null clears the cap, so
    # only a field the client actually sent counts as a repricing.
    if "max_price" in sent:
        found = shm_watcher.set_watch_max_price(conn, skin_id, update.max_price) and found
    if not found:
        raise HTTPException(status_code=404, detail="SHM watch not found")
    return {"ok": True, "skin_id": skin_id}


@app.get("/api/network")
def network():
    """Circuits, their routes, and hub route coverage — cached, no game calls."""
    return get_network_snapshot()


@app.get("/api/pricing/{hub_iata}")
def pricing(hub_iata: str, backend: str = Query("mobile", pattern="^(mobile|cdp)$")):
    """Live per-route pricing for one hub: current vs the audit's recommendation.

    One request per hub (mobile) instead of one page per route, so this is
    cheap enough to be the tab's on-open load. Read-only: writing prices is
    `mobile_pricer.py`, which has its own 24h-cooldown handling.
    """
    import masstool

    hub = hub_iata.upper().strip()
    hub_id = get_player_hub_id(hub)
    if not hub_id:
        raise HTTPException(status_code=404, detail=f"{hub} is not one of your hubs")
    try:
        data, used = masstool.fetch_hub(hub_id, prefer=backend)
    except Exception as exc:                                    # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Live pricing read failed: {exc}") from exc

    circuit_of = {row["dest_iata"]: row["circuit_name"] for row in get_db().execute(
        "SELECT cr.dest_iata, cr.circuit_name FROM circuit_routes cr "
        "JOIN circuits c ON c.name = cr.circuit_name WHERE c.hub_iata = ?", (hub,))}

    rows = []
    for iata, route in sorted(data.items()):
        summary = masstool.compute_route_summary(route)
        rows.append({
            "iata": iata,
            "line_id": route.get("line_id"),
            "name": route.get("full_name"),
            "circuit": circuit_of.get(iata),
            "price": route.get("price") or {},
            "audit_price": route.get("audit_price") or {},
            "demand": route.get("demand") or {},
            "carried": route.get("carried") or {},
            "remaining": route.get("remaining") or {},
            "locked_until": route.get("locked_until"),
            "daily_revenue": summary["daily_revenue"],
            "weekly_revenue": summary["weekly_revenue"],
        })
    return {"hub_iata": hub, "backend": used, "routes": rows,
            "daily_revenue": sum(r["daily_revenue"] for r in rows)}


class PricingApplyRequest(BaseModel):
    mode: Literal["ideal", "percent", "fill"] = "ideal"
    pct: float = Field(100.0, ge=25.0, le=200.0)
    routes: Optional[List[str]] = None
    circuit: Optional[str] = None
    dry_run: bool = True


@app.post("/api/pricing/{hub_iata}/apply")
def apply_pricing(hub_iata: str, req: PricingApplyRequest):
    """Preview or write route prices for one hub — `mobile_pricer.price_hub`.

    Two guardrails the CLI does not need, because a stray POST is cheaper to
    make than a stray shell command:

    * `pct` is clamped to 25-200%. A fat-fingered multiplier would not just
      misprice the hub, it would burn every route's 24h cooldown getting there.
    * A live write (`dry_run=False`) must name its routes. That forces the
      apply to be exactly the set the operator previewed, and makes
      "reprice the entire hub" impossible to trigger by accident.
    """
    import mobile_pricer

    hub = hub_iata.upper().strip()
    only = {iata.upper() for iata in req.routes} if req.routes else None
    if req.circuit:
        from auto_pricer import load_circuit_dest_iatas
        _, circuit_routes = load_circuit_dest_iatas(req.circuit)
        if not circuit_routes:
            raise HTTPException(status_code=404, detail=f"Circuit {req.circuit} not found")
        only = circuit_routes if only is None else (only & circuit_routes)
    if not req.dry_run and not only:
        raise HTTPException(
            status_code=400,
            detail="Preview first: a live price write must list the routes to change.")

    doc = _hangar_call(lambda client: mobile_pricer.price_hub(
        hub, mode=req.mode, pct=req.pct, only=only,
        dry_run=req.dry_run, client=client))
    if doc.get("error"):
        raise HTTPException(status_code=404, detail=doc["error"])
    return doc


@app.get("/api/ops")
def ops():
    """Delivery queue, claimable dailies, and cache freshness in one read.

    The two mobile sections carry their own `error` instead of failing the
    whole page: a dead mobile session must not hide the freshness table, which
    is exactly what you look at when the session is dead.
    """
    deliveries = {"events": [], "server_time": None, "error": None}
    daily = {"error": None}
    try:
        def run(client):
            now, events = client._events()
            offers = [o for o in client.shop_offers()
                      if o.get("purchaseCost") == 0 and o.get("subCategoryId") == 308
                      and o.get("purchaseCurrency") in ("adv", "free")]
            wheel, slot = client.wheel_rules(), client.slot_rules()
            return now, events, offers, wheel, slot

        now, events, offers, wheel, slot = _hangar_call(run)
        deliveries.update(server_time=now, events=[
            {"event_id": e.get("id"), "type": e.get("type"), "label": e.get("label"),
             "aircraft_id": e.get("objectid"),
             "finish_at": (e.get("finishAt") or {}).get("date"),
             "am_coins_to_skip": e.get("amount")}
            for e in events])
        daily.update(
            currency_claims=sum(int(o.get("remaining") or 0) for o in offers if o.get("isAvailable")),
            currency_offers=len(offers),
            wheel_available=bool(wheel.get("isAllowToPlay") or wheel.get("isAllowToRePlay")),
            slot_games_left=slot.get("nbRemainingGames") or 0)
    except HTTPException as exc:
        deliveries["error"] = daily["error"] = str(exc.detail)

    return {"deliveries": deliveries, "daily": daily, "freshness": get_ops_freshness(),
            "browser_connected": _browser_connected(),
            "mobile_configured": _mobile_configured()}


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


@app.get("/api/fleet-name-suggestions")
def fleet_name_suggestions(prefix: str = Query(..., min_length=2, max_length=20),
                            limit: int = Query(30, ge=1, le=50)):
    """Return only the first cached aircraft names matching a typed prefix."""
    return get_fleet_name_suggestions(prefix, limit)


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


def _name_unknown_models(items, model_by_id, model_by_aircraft, profile):
    """Name any model the DB has never seen, in place, one call per model.

    The first aircraft of a model the airline has never owned is nameless in
    both sources — no previous fleet row, no catalog row — and one such plane
    used to reject the entire fleet into the browser fallback. Its own profile
    names the model.
    """
    for item in items:
        model_id = item.get("al_id")
        if (model_id is None or model_id in model_by_id
                or model_by_aircraft.get(item.get("id"))):
            continue
        name = ((profile(item["id"]) or {}).get("model") or {}).get("name")
        if name:
            model_by_id[model_id] = name


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
        _name_unknown_models(items, model_by_id, model_by_aircraft,
                             client.aircraft)
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


def _read_cdp_fleet(requested_hub=None, mobile_error=None):
    """Preserved website fleet reader, used when the mobile connection fails."""
    tab = get_am_tab()
    if not tab:
        raise HTTPException(
            status_code=503,
            detail=(f"Mobile fleet sync failed ({mobile_error or 'unknown error'}) "
                    "and no Chrome tab is available for fallback."),
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
    except Exception as exc:
        # Never silently: the fallback's own failure used to be the only thing
        # in the log, which said nothing about why mobile gave up.
        print(f"mobile fleet sync failed, falling back to CDP: {exc!r}",
              file=sys.stderr, flush=True)
        source = "cdp_fallback"
        all_fleet, synced_hubs = _read_cdp_fleet(requested_hub, mobile_error=exc)

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


# ── HANGAR: single-aircraft workbench (mobile API) ──────────────────────────
# One aircraft, every write the mobile API can make against it: rename, seats,
# hub, livery, market listing, scrap. Mobile-only by design — these endpoints
# exist precisely because the mobile API can do them cleanly and a refused write
# raises instead of silently no-op'ing, which is not true of the web forms.
# Clearing a schedule is the one exception and still goes over CDP (the mobile
# planning WRITE payload is unknown — ticket 013, gap 1).


def _hangar_call(fn):
    """Run fn(mobile_client), mapping session/API failures to HTTP errors."""
    from mobile_api import AMClient, AMError, AMAuthError, AMSession
    from mobile_store import MobileStore
    try:
        session = AMSession.load()
    except Exception as exc:                                # noqa: BLE001
        raise HTTPException(status_code=503,
                            detail=f"No usable mobile session: {exc}") from exc
    client = AMClient(session, store=MobileStore(get_db()))
    try:
        return fn(client)
    except AMAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        client.close()


def _hub_iata_by_id() -> dict:
    return {row["hub_id"]: row["hub_iata"] for row in get_db().execute(
        "SELECT hub_id, hub_iata FROM player_hubs")}


def _hangar_view(client, aircraft_id: int) -> dict:
    """One aircraft's live profile, flattened for the workbench."""
    profile = client.aircraft(int(aircraft_id)) or {}
    if not profile.get("id"):
        raise HTTPException(status_code=404, detail=f"Unknown aircraft {aircraft_id}")
    model = profile.get("model") or {}
    hub = profile.get("hub") or {}
    seats = profile.get("seats") or {}
    skin = profile.get("skins") or {}
    hubs = _hub_iata_by_id()
    spec = get_db().execute(
        "SELECT icao_code, category, speed_kmh, range_km, max_pax, "
        "max_tonnage, gross_price FROM aircraft WHERE model = ? COLLATE NOCASE LIMIT 1",
        (model.get("name") or "",),
    ).fetchone()
    view = {
        "aircraft_id": profile["id"],
        "name": profile.get("name") or "",
        "model": model.get("name") or "",
        "model_id": model.get("id"),
        "model_seats_total": (model.get("seats") or {}).get("total"),
        "model_payload_t": model.get("payload"),
        "icao_code": spec["icao_code"] if spec else None,
        "category": spec["category"] if spec else model.get("category"),
        "speed_kmh": spec["speed_kmh"] if spec else None,
        "range_km": spec["range_km"] if spec else None,
        "max_pax": spec["max_pax"] if spec else None,
        "max_tonnage": spec["max_tonnage"] if spec else model.get("payload"),
        "gross_price": spec["gross_price"] if spec else None,
        "hub_id": hub.get("id"),
        "hub_iata": hubs.get(hub.get("id")),
        "hub_name": hub.get("name"),
        "utilization": profile.get("utilization") or 0,
        "wear": profile.get("wear") or 0,
        "age": profile.get("age") or 0,
        "mark": profile.get("mark"),
        "is_rental": bool(profile.get("isRental")),
        "is_frozen": bool(profile.get("isFrozen")),
        "purchased_at": (profile.get("purchasedAt") or {}).get("date"),
        "raw_price": profile.get("price"),
        "seats": {"eco": seats.get("eco") or 0,
                  "bus": seats.get("business") or 0,
                  "first": seats.get("first") or 0},
        "payload": profile.get("payload") or 0,
        "skin": {"id": skin.get("id"), "name": skin.get("name")},
        # The three caps the market enforces, plus what the game itself pays.
        # binThreshold is per-LIVERY, so it moves when the livery does.
        "sale": {"scrap": profile.get("sellPrice"),
                 "bin_threshold": profile.get("binThreshold"),
                 "max_start_bid": profile.get("maxAuctionSellPrice"),
                 "min_start_bid": profile.get("minAuctionSellPrice")},
        "hubs": [{"hub_iata": iata, "hub_id": hid}
                 for hid, iata in sorted(hubs.items(), key=lambda kv: kv[1])],
    }
    _sync_fleet_row(view)
    return view


def _sync_fleet_row(view: dict) -> None:
    """Keep the cached fleet row in step with what the game just told us.

    Without this a rename or a hub move only shows up after a full fleet sync,
    and the workbench's own aircraft list would still show the old value.
    """
    db = get_db()
    db.execute(
        "UPDATE fleet SET name = ?, hub_iata = COALESCE(?, hub_iata), "
        "skin_id = COALESCE(?, skin_id), updated_at = CURRENT_TIMESTAMP "
        "WHERE aircraft_id = ?",
        (view["name"], view["hub_iata"], (view["skin"] or {}).get("id"),
         view["aircraft_id"]))
    db.commit()


@app.get("/api/hangar/{aircraft_id}")
def hangar_aircraft(aircraft_id: int):
    """Live profile of one aircraft, read fresh from the mobile API."""
    return _hangar_call(lambda cl: _hangar_view(cl, aircraft_id))


@app.get("/api/hangar/{aircraft_id}/liveries")
def hangar_liveries(aircraft_id: int):
    """Liveries that can be applied to this aircraft's model, art included.

    `canBeApplied` is the game's own answer to "may this plane wear this?", so
    it — not ownership — is the filter: the manufacturer paint is appliable
    without ever being bought. The currently worn livery is prepended even when
    the model's shop listing omits it (awarded challenge liveries are not sold).
    """
    def run(client):
        view = _hangar_view(client, aircraft_id)
        if not view["model_id"]:
            return {"aircraft_id": view["aircraft_id"], "current": view["skin"], "liveries": []}
        from mobile_store import MobileStore
        store = MobileStore(get_db())
        out, seen = [], set()
        current_id = (view["skin"] or {}).get("id")
        for skin in client.model_skins(view["model_id"]):
            if not skin.get("canBeApplied") or skin.get("id") in seen:
                continue
            picture = (skin.get("picture") or {})
            path = (picture.get("big") or picture.get("medium") or "").split("?")[0]
            seen.add(skin["id"])
            store.upsert_skin(skin["id"], model_id=view["model_id"],
                              name=skin.get("name"), creator=skin.get("creator"),
                              status=skin.get("status"), picture_path=path or None,
                              price_amcoins=skin.get("price"), sold=skin.get("sold"),
                              owned=1 if skin.get("purchased") else None)
            out.append({"skin_id": skin["id"], "name": skin.get("name"),
                        "purchased": bool(skin.get("purchased")),
                        "price_amcoins": skin.get("price") or 0,
                        "creator": skin.get("creator"),
                        "is_current": skin["id"] == current_id})
        store.commit()
        if current_id and current_id not in seen:
            out.insert(0, {"skin_id": current_id, "name": (view["skin"] or {}).get("name"),
                           "purchased": True, "price_amcoins": 0,
                           "creator": None, "is_current": True})
        # Warm the image cache so the picker has art on first open; a CDN miss
        # just leaves that one tile blank.
        fetch_missing_skin_images([{"skin_id": item["skin_id"]} for item in out])
        return {"aircraft_id": view["aircraft_id"], "current": view["skin"], "liveries": out}
    return _hangar_call(run)


@app.get("/api/hangar/{aircraft_id}/schedule")
def hangar_schedule(aircraft_id: int, day: int = Query(0, ge=0, le=6)):
    """One day of an aircraft's flights (`day` 0 = today), compacted."""
    def run(client):
        flights = []
        for flight in client.aircraft_flights(int(aircraft_id), day):
            line = flight.get("line") or {}
            flights.append({
                "flight_id": flight.get("id"),
                "line_id": line.get("id"),
                "from_iata": (line.get("airportOne") or {}).get("iata"),
                "to_iata": (line.get("airportTwo") or {}).get("iata"),
                "departure": (flight.get("takeOffTime") or {}).get("date"),
                "arrival": (flight.get("endTime") or {}).get("date"),
                "in_future": bool(flight.get("isInFuture")),
            })
        return {"aircraft_id": int(aircraft_id), "day": day, "flights": flights}
    return _hangar_call(run)


class HangarName(BaseModel):
    name: str = Field(min_length=1, max_length=20)


@app.post("/api/hangar/{aircraft_id}/name")
def hangar_rename(aircraft_id: int, req: HangarName):
    """Rename one aircraft. Free: the seat config is echoed back unchanged."""
    def run(client):
        view = _hangar_view(client, aircraft_id)
        seats = view["seats"]
        client.reconfigure(view["aircraft_id"], name=req.name.strip(),
                           eco=seats["eco"], bus=seats["bus"],
                           first=seats["first"], payload=view["payload"])
        return _hangar_view(client, aircraft_id)
    return _hangar_call(run)


class HangarSeats(BaseModel):
    eco: int = Field(ge=0)
    bus: int = Field(ge=0)
    first: int = Field(ge=0)
    payload: int = Field(ge=0)


@app.post("/api/hangar/{aircraft_id}/seats")
def hangar_reconfigure(aircraft_id: int, req: HangarSeats):
    """Set the seat/cargo configuration. This is a PAID action in game."""
    def run(client):
        view = _hangar_view(client, aircraft_id)
        client.reconfigure(view["aircraft_id"], name=view["name"],
                           eco=req.eco, bus=req.bus, first=req.first,
                           payload=req.payload)
        updated = _hangar_view(client, aircraft_id)
        if (updated["seats"] != {"eco": req.eco, "bus": req.bus, "first": req.first}
                or updated["payload"] != req.payload):
            raise HTTPException(
                status_code=502,
                detail="Reconfigure failed: the game kept the previous configuration")
        return updated
    return _hangar_call(run)


class HangarHub(BaseModel):
    hub_iata: str


@app.post("/api/hangar/{aircraft_id}/hub")
def hangar_assign_hub(aircraft_id: int, req: HangarHub):
    """Relocate the aircraft to another of the airline's hubs (paid)."""
    hub_id = get_player_hub_id(req.hub_iata)
    if hub_id is None:
        raise HTTPException(status_code=400, detail=f"Unknown player hub: {req.hub_iata}")

    def run(client):
        client.assign_hub(int(aircraft_id), int(hub_id))
        return _hangar_view(client, aircraft_id)
    return _hangar_call(run)


class HangarLivery(BaseModel):
    skin_id: int
    # An awarded challenge or event livery cannot be re-applied once painted
    # over, so overwriting one is a deliberate, separately-confirmed act.
    confirm_overwrite: bool = False


@app.post("/api/hangar/{aircraft_id}/livery")
def hangar_apply_livery(aircraft_id: int, req: HangarLivery):
    """Repaint one aircraft in a livery the airline can apply (free if owned)."""
    def run(client):
        view = _hangar_view(client, aircraft_id)
        worn = ((view["skin"] or {}).get("name") or "").lower()
        if "manufacturer livery" not in worn and not req.confirm_overwrite:
            raise HTTPException(
                status_code=409,
                detail=f"{view['name']} wears “{(view['skin'] or {}).get('name')}”. "
                       "An awarded livery cannot be re-applied once painted over — "
                       "confirm the overwrite to proceed.")
        client.apply_skin(int(req.skin_id), [view["aircraft_id"]])
        return _hangar_view(client, aircraft_id)
    return _hangar_call(run)


class HangarSell(BaseModel):
    bin_price: int = Field(gt=0)
    price: Optional[int] = Field(default=None, gt=0)
    duration: int = Field(default=11, ge=1, le=48)


@app.post("/api/hangar/{aircraft_id}/sell")
def hangar_sell(aircraft_id: int, req: HangarSell):
    """List the aircraft on the second-hand market (max 10 active listings)."""
    from mobile_api import AMAuctionLimit, AMNotDelivered, AMRateLimited

    def run(client):
        try:
            auction = client.put_up(int(aircraft_id), req.price or req.bin_price,
                                    req.bin_price, req.duration)
        except AMAuctionLimit as exc:
            raise HTTPException(status_code=409, detail=f"Auction limit reached: {exc}") from exc
        except AMNotDelivered as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except AMRateLimited as exc:
            raise HTTPException(status_code=429,
                                detail=f"Listing cooldown — nothing was listed: {exc}") from exc
        return {"aircraft_id": int(aircraft_id),
                "auction_id": auction.get("id"),
                "bin_price": auction.get("binPrice"),
                "fair_value": auction.get("alertThreshold"),
                "time_left_s": auction.get("timeLeft")}
    return _hangar_call(run)


class HangarScrap(BaseModel):
    # The typed-back aircraft name. Scrapping is irreversible, so the request
    # must name what it is destroying rather than just carrying an id.
    confirm_name: str


@app.post("/api/hangar/{aircraft_id}/scrap")
def hangar_scrap(aircraft_id: int, req: HangarScrap):
    """Sell the aircraft back to the game at its scrap price. Irreversible.

    The mobile write shape is an educated guess (see
    `AMClient.sell_for_scrap`), so a refusal here means the payload needs a
    capture — it does not mean the aircraft is in a strange state.
    """
    def run(client):
        view = _hangar_view(client, aircraft_id)
        if req.confirm_name.strip() != view["name"]:
            raise HTTPException(status_code=400,
                                detail=f"Confirmation does not match “{view['name']}”.")
        result = client.sell_for_scrap(view["aircraft_id"])
        db = get_db()
        db.execute("DELETE FROM fleet WHERE aircraft_id = ?", (view["aircraft_id"],))
        db.commit()
        return {"aircraft_id": view["aircraft_id"], "name": view["name"],
                "scrapped_for": (view["sale"] or {}).get("scrap"),
                "message": result.get("message")}
    return _hangar_call(run)


@app.post("/api/hangar/{aircraft_id}/unschedule")
def hangar_unschedule(aircraft_id: int):
    """Clear every scheduled flight for one aircraft.

    The only hangar action still on CDP: the mobile planning WRITE payload has
    not been recovered (ticket 013, gap 1), so this drives the web endpoint
    `circuit_scheduler.clear_schedule` already uses.
    """
    tab = get_am_tab()
    if not tab:
        raise HTTPException(status_code=503,
                            detail="Clearing a schedule needs Chrome — the mobile API "
                                   "has no known planning write. Link Chrome and retry.")
    from circuit_scheduler import clear_schedule
    cdp = CDP(tab["webSocketDebuggerUrl"])
    cdp.connect()
    try:
        result = clear_schedule(cdp, int(aircraft_id))
    finally:
        cdp.close()
    if not (result and result.get("result") is True):
        raise HTTPException(status_code=502, detail=f"Clear schedule refused: {result}")
    return {"aircraft_id": int(aircraft_id), "cleared": True}


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
