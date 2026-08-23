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
import os
import sys
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import (
    get_db,
    get_fleet_aircraft,
    get_fleet_summary_stats,
    get_livery_collection,
    get_skin_image_bytes,
    resolve_skin_ids,
    upsert_fleet,
)
from cdp import CDP, get_am_tab
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


# ── REST ENDPOINTS ──────────────────────────────────────────────────────────

@app.get("/api/stats")
def get_stats():
    """Return high-level fleet and livery collection stats."""
    return get_fleet_summary_stats()


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
        sort_by=sort_by or "name",
        limit=limit,
        offset=offset,
    )


@app.get("/api/liveries")
def list_liveries(
    status_filter: Optional[str] = Query(None, description="'owned', 'unowned', or None"),
    rarity: Optional[int] = Query(None),
    model_query: Optional[str] = Query(None),
    search_query: Optional[str] = Query(None),
):
    """Return special/custom liveries (strictly excluding manufacturer liveries)."""
    return get_livery_collection(
        include_manufacturer=False,
        status_filter=status_filter,
        rarity=rarity,
        model_query=model_query,
        search_query=search_query,
    )


@app.get("/api/skin_image/{skin_id}")
@app.get("/api/skin_image/{skin_id}/{size}")
def get_skin_image(skin_id: int, size: str = "big"):
    """Serve livery PNG image bytes directly from SQLite image cache."""
    data = get_skin_image_bytes(skin_id, size)
    if data:
        return Response(
            content=data,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=604800"},
        )
    return Response(
        content=_TRANSPARENT_PNG,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


class SyncRequest(BaseModel):
    hub: Optional[str] = None


@app.post("/api/sync-fleet")
def sync_fleet(req: SyncRequest):
    """Scrape fleet from AM game using CDP and update SQLite database."""
    tab = get_am_tab()
    if not tab:
        raise HTTPException(
            status_code=503,
            detail="No Chrome tab found with Airlines Manager open on port 9222.",
        )

    cdp = CDP(tab["webSocketDebuggerUrl"])
    cdp.connect()
    try:
        db = get_db()
        if req.hub:
            hubs = [req.hub.upper().strip()]
        else:
            hubs = [r[0] for r in db.execute("SELECT hub_iata FROM player_hubs").fetchall()]

        if not hubs:
            raise HTTPException(status_code=400, detail="No hubs found in player_hubs table")

        navigate_to_planning(cdp)

        all_fleet = []
        for hub in hubs:
            if select_hub(cdp, hub):
                ac_list = get_aircraft_at_hub(cdp, hub)
                for ac in ac_list:
                    ac["hub"] = hub
                all_fleet.extend(ac_list)

        resolved, unresolved = resolve_skin_ids(all_fleet)
        upsert_fleet(all_fleet)

        return {
            "status": "success",
            "message": f"Successfully synced {len(all_fleet)} aircraft across {len(hubs)} hubs ({resolved} liveries identified).",
            "count": len(all_fleet),
            "resolved_liveries": resolved,
        }
    finally:
        cdp.close()


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

if os.path.exists(WEB_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(WEB_DIST, "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API route not found")
        target_file = os.path.join(WEB_DIST, full_path)
        if os.path.isfile(target_file):
            return FileResponse(target_file)
        return FileResponse(os.path.join(WEB_DIST, "index.html"))


def main():
    parser = argparse.ArgumentParser(description="Autopilot AM Web API Server")
    parser.add_argument("--host", default=os.getenv("AM_HOST", "127.0.0.1"), help="Host to bind")
    parser.add_argument("--port", type=int, default=int(os.getenv("AM_PORT", "8000")), help="Port to bind")
    args = parser.parse_args()

    print(f"✈ Autopilot AM Web App & API running at http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
