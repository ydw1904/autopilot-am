#!/usr/bin/env python3
"""
Airlines Manager MCP Server
============================
Exposes Airlines Manager game operations as MCP tools so any AI agent
(Hermes, Claude Code, etc.) can control the game via natural language.

Architecture:
  Agent (natural language) --> MCP protocol (stdio) --> This server --> CDP WebSocket --> Chrome

Each tool encapsulates the game's quirks (CSRF tokens, jQuery handlers, rate limits)
so the calling agent doesn't need to know CDP or DOM internals.

Usage with Hermes (~/.hermes/config.yaml):
  mcp_servers:
    airlines-manager:
      command: "python3"
      args: ["/path/to/autopilot-am/code/mcp_server.py"]

Usage standalone (for testing):
  python3 mcp_server.py
  Then send MCP JSON-RPC messages over stdin.

Requirements:
  - Chrome running with --remote-debugging-port=9222 --remote-allow-origins=*
  - Airlines Manager tab open in that Chrome
  - pip install mcp httpx websocket-client
"""

import functools
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from typing import List, Optional, Tuple

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import CDP, get_am_tab, js_args, BASE_URL  # noqa: E402
from db import get_db, get_dest_country, get_player_hub_id, mark_route_owned  # noqa: E402
from planning_page import (  # noqa: E402
    wait_for_js as _wait_for_js,
    wait_for_hub_buttons as _wait_for_hub_buttons,
    select_hub as _select_planning_hub,
    get_aircraft_at_hub as _get_planning_aircraft,
    get_lines_at_hub,
)
from aircraft_buyer import get_balance as read_balance  # noqa: E402
from aircraft_aliases import (  # noqa: E402
    resolve as resolve_aircraft_name,
    catalog as aircraft_catalog_data,
)
from circuit_route_buyer import (  # noqa: E402
    wait_for_listing, find_country_card, finalize_purchase,
)
from circuit_scheduler import (  # noqa: E402
    clear_schedule, submit_flights, _mobile_client as _planning_client,
)

# ── Config ──────────────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
PYTHON_BIN = sys.executable or "python3"

mcp = FastMCP(
    "airlines-manager",
    instructions=(
        "Tools for controlling Airlines Manager (airlines-manager.com) via Chrome CDP. "
        "Chrome must be running with --remote-debugging-port=9222 and an AM tab must be open. "
        "All operations use the game's session cookies from the active Chrome tab. "
        "Tools that take an aircraft 'model' accept any spelling (e.g. 'A380', 'A388', "
        "'A380-800'); use resolve_aircraft to normalize a name or disambiguate when "
        "several models match."
    ),
)


def _trim_output(text: str, limit: int = 12000) -> Tuple[str, bool]:
    """Trim long subprocess output while preserving the beginning and end."""
    text = text or ""
    if len(text) <= limit:
        return text, False
    head = text[: limit // 2]
    tail = text[-(limit // 2):]
    trimmed = (
        f"{head}\n\n...[truncated {len(text) - len(head) - len(tail)} chars]...\n\n{tail}"
    )
    return trimmed, True


def _run_python_script(
    script_name: str,
    args: Optional[List[str]] = None,
    timeout: int = 300,
    parse_json: bool = False,
) -> dict:
    """Run an existing project script and return a structured result."""
    script_path = os.path.join(SCRIPT_DIR, script_name)
    if not os.path.exists(script_path):
        return {"error": f"Script not found: {script_name}"}

    cmd = [PYTHON_BIN, script_path] + list(args or [])
    try:
        proc = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired as e:
        stdout_raw = e.stdout or ""
        stderr_raw = e.stderr or ""
        if isinstance(stdout_raw, bytes):
            stdout_raw = stdout_raw.decode("utf-8", errors="replace")
        if isinstance(stderr_raw, bytes):
            stderr_raw = stderr_raw.decode("utf-8", errors="replace")
        stdout, stdout_truncated = _trim_output(stdout_raw)
        stderr, stderr_truncated = _trim_output(stderr_raw)
        return {
            "ok": False,
            "timed_out": True,
            "timeout_seconds": timeout,
            "command": cmd,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "error": f"Script timed out after {timeout}s",
        }

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    stdout, stdout_truncated = _trim_output(stdout)
    stderr, stderr_truncated = _trim_output(stderr)

    result = {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "command": cmd,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
    }

    # Not gated on returncode: the wrapped scripts exit non-zero on partial
    # success (e.g. 2 = some batches failed) but still emit their document, and
    # that document is exactly what explains the failure.
    if parse_json and (proc.stdout or "").strip():
        try:
            result["data"] = json.loads(proc.stdout)
        except json.JSONDecodeError:
            if proc.returncode == 0:
                result["json_parse_error"] = "stdout was not valid JSON"

    return result


def _has_mobile_session() -> bool:
    """True when a mobile token is on disk, so the mobile path can be primary.

    Checked BEFORE running a mutating script rather than falling back after a
    non-zero exit: a partial failure exits non-zero too, and re-running the
    same work over CDP would apply it twice.
    """
    try:
        from mobile_api import AMSession
        return bool(AMSession.load().access_token)
    except Exception:
        return False


def _lookup_player_hub_id(hub_iata: str) -> Optional[str]:
    """Resolve hub_iata -> player hub_id from the local SQLite DB."""
    hub_id = get_player_hub_id(hub_iata) if hub_iata else None
    return str(hub_id) if hub_id is not None else None


def _normalize_iatas(values: Optional[List[str]]) -> List[str]:
    return [v.upper().strip() for v in (values or []) if v and v.strip()]


# ── Connection Management ──────────────────────────────────────────────────
# The server maintains a persistent CDP connection across tool calls.
# This is more efficient than reconnecting per-call and preserves state.

_cdp = None  # Module-level singleton
# FastMCP dispatches sync tools on a thread pool, and the shared CDP socket
# has a single _msg_id counter: two concurrent tool calls would eat each
# other's responses. Serialize the tools that drive the socket.
_cdp_lock = threading.RLock()


def _serialized_cdp(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _cdp_lock:
            return fn(*args, **kwargs)
    return wrapper


def _get_cdp():
    """Get or create the CDP connection. Finds the AM tab automatically."""
    global _cdp

    if _cdp:
        # Check if WebSocket is still alive with a ping. eval() swallows
        # timeouts (returns None without raising), so only an exception
        # proves the socket is dead — a hung-but-open socket stays.
        try:
            _cdp.eval("1")
            return _cdp
        except Exception:
            _cdp = None

    am_tab = get_am_tab()  # None when Chrome is down or has no AM tab
    if not am_tab:
        return None

    _cdp = CDP(am_tab["webSocketDebuggerUrl"])
    _cdp.connect()
    return _cdp


def _cdp_error_suffix(cdp):
    """' (cdp timeout: ...)' / ' (cdp js: ...)' if the last eval failed, else ''."""
    if cdp.last_error:
        return f" (cdp {cdp.last_error['kind']}: {cdp.last_error['detail']})"
    return ""


def _get_lines_at_selected_hub(cdp, hub_iata=None):
    """Owned lines at a planning hub, remapped to MCP output keys."""
    return [
        {"line_id": str(l["lineId"]), "raw": l.get("name") or "",
         "dest_iata": l.get("dest") or "?"}
        for l in get_lines_at_hub(cdp, hub_iata)
    ]


def _get_aircraft_at_selected_hub(cdp, hub_iata=None):
    """Aircraft at a planning hub, remapped to MCP output keys."""
    return [
        {"aircraft_id": str(a["id"]), "model": a.get("model") or "?",
         "name": a.get("name") or "", "utilization_pct": a.get("util", 0)}
        for a in _get_planning_aircraft(cdp, hub_iata)
    ]


# ── Route purchase helpers (ported from circuit_route_buyer.py) ─────────────
# Two hard-won lessons baked in here:
#   1. The game server silently rejects fetch()-based purchase POSTs (returns
#      200 but does NOT apply the purchase). Native form.submit() is required.
#   2. Some hubs/countries no longer expose /newlinefinalize directly without
#      first going through the country listing page, so we prefer that flow.

def _lookup_dest_country(hub_iata: str, dest_iata: str) -> Optional[str]:
    """Resolve a destination's country slug (lowercase) from the routes table."""
    return get_dest_country(hub_iata, dest_iata)


def _mark_route_owned(hub_iata: str, dest_iata: str) -> None:
    """Best-effort: flag a route as owned in the local routes table."""
    try:
        mark_route_owned(hub_iata, dest_iata)
    except Exception:
        pass


# ── Tools ───────────────────────────────────────────────────────────────────


@mcp.tool()
@_serialized_cdp
def get_balance() -> dict:
    """Get the player's current dollar balance.

    Returns:
        dict with 'balance' (int, dollars), 'error' if failed.
    """
    res = _mobile_call(lambda cl: {"ok": True, **cl.resources()}, store=False)
    if res.get("ok") and res.get("dollar") is not None:
        return {"balance": int(res["dollar"]), "backend": "mobile"}

    cdp = _get_cdp()
    if not cdp:
        return {"error": "No mobile session and no Airlines Manager tab in Chrome.",
                "mobile_error": res.get("error")}

    balance = read_balance(cdp)
    if balance is None:
        return {"error": "Could not read balance. Make sure you're on a game page."}
    return {"balance": balance, "backend": "cdp"}


@mcp.tool()
@_serialized_cdp
def list_hubs() -> dict:
    """List all hubs with their internal IDs and names.

    Returns:
        dict with 'hubs' (list of {name, hub_id, iata}), 'count'.
    """
    # bfa/hub answers every hub in one call, but names them by internal
    # airport id, so the IATA comes from player_hubs (the same id space).
    def _mobile(cl):
        by_id = {int(r["hub_id"]): r["hub_iata"]
                 for r in get_db().execute("SELECT hub_iata, hub_id FROM player_hubs")}
        return {"ok": True, "hubs": [
            {"hub_id": str(h["id"]), "iata": by_id.get(int(h["id"]), "?"),
             "name": by_id.get(int(h["id"]), "?")}
            for h in cl.hubs()]}

    res = _mobile_call(_mobile, store=False)
    if res.get("ok") and res.get("hubs"):
        return {"hubs": res["hubs"], "count": len(res["hubs"]), "backend": "mobile"}

    cdp = _get_cdp()
    if not cdp:
        return {"error": "No mobile session and no Airlines Manager tab in Chrome.",
                "mobile_error": res.get("error"), "hubs": []}

    loaded = cdp.navigate_and_wait(
        f"{BASE_URL}/network/newline",
        "document.querySelectorAll('.hubListBox[data-hubid]').length",
    )
    if not loaded:
        return {"error": "Could not read hubs. Are you on the network page?", "hubs": []}

    hubs = cdp.eval_json("""((() => {
        const boxes = document.querySelectorAll('.hubListBox[data-hubid]');
        return Array.from(boxes).map(b => ({
            hub_id: b.getAttribute('data-hubid'),
            name: b.querySelector('.hubNameBox')?.textContent?.trim() || '?',
            iata: b.querySelector('.title')?.textContent?.trim()?.split(' - ')?.[0] || '?'
        }));
    })())""")

    if not hubs or not isinstance(hubs, list):
        return {"error": "Could not read hubs. Are you on the network page?", "hubs": []}

    return {"hubs": hubs, "count": len(hubs)}


@mcp.tool()
@_serialized_cdp
def list_routes(hub_iata: str) -> dict:
    """List owned routes from a hub.

    Uses the planning page hub selector so the result actually reflects the
    requested hub instead of whichever hub happened to be selected already.

    Args:
        hub_iata: IATA code of the hub, e.g. 'HKG', 'MPM', 'FRA'.

    Returns:
        dict with 'routes' (list of {dest_iata, raw, line_id}), 'hub_iata', 'count'.
    """
    hub_iata = hub_iata.upper().strip()
    hub_id = _lookup_player_hub_id(hub_iata)
    if hub_id:
        # One request per hub page, and the rows already carry the line id and
        # the destination IATA the CDP path has to walk the planning DOM for.
        def _mobile(cl):
            return {"ok": True, "routes": [
                {"dest_iata": (ln.get("aTwoName") or "").upper(),
                 "raw": f"{ln.get('aOneName', '')}/{ln.get('aTwoName', '')}",
                 "line_id": int(ln["id"])}
                for ln in cl.hub_pricing(int(hub_id))]}

        res = _mobile_call(_mobile, store=False)
        if res.get("ok") and res.get("routes"):
            return {"hub_iata": hub_iata, "routes": res["routes"],
                    "count": len(res["routes"]), "backend": "mobile"}

    cdp = _get_cdp()
    if not cdp:
        return {"error": "No mobile session and no Airlines Manager tab in Chrome.",
                "hub_iata": hub_iata, "routes": []}

    cdp.navigate(f"{BASE_URL}/network/planning")
    if not _wait_for_hub_buttons(cdp, timeout=15.0):
        return {"error": "Planning page did not load hub selector.", "hub_iata": hub_iata, "routes": []}
    if not _select_planning_hub(cdp, hub_iata):
        return {"error": f"Could not select hub {hub_iata} on the planning page.", "hub_iata": hub_iata, "routes": []}

    routes = _get_lines_at_selected_hub(cdp, hub_iata)
    if not routes:
        return {"error": f"No routes found from {hub_iata}.", "hub_iata": hub_iata, "routes": []}

    return {"hub_iata": hub_iata, "routes": routes, "count": len(routes)}


@mcp.tool()
@_serialized_cdp
def buy_route(
    hub_iata: str,
    dest_iata: str,
    hub_id: Optional[str] = None,
    country: Optional[str] = None,
    dry_run: bool = True,
    legacy: bool = False,
) -> dict:
    """Purchase a single route from a hub to a destination.

    Ported from circuit_route_buyer.py's battle-tested flow. It prefers the
    country-listing flow: navigate to /network/newline/<hub_id>/<country>, find
    the destination's card, and submit its finalize form. Some hubs no longer
    expose /newlinefinalize directly, so this is more reliable than going
    straight to the finalize URL. Falls back to the direct finalize flow when
    the destination country is unknown or legacy=True.

    Purchases are submitted with native form.submit(), NOT fetch() -- the game
    silently rejects fetch()-based purchase POSTs (returns 200 but applies
    nothing), which is why the old fetch-based tool could "succeed" yet buy
    nothing.

    Args:
        hub_iata: Hub IATA code, e.g. 'HKG'.
        dest_iata: Destination IATA code, e.g. 'LAX'.
        hub_id: Player hub id (from the AM URL). Auto-resolved from the local DB
                (player_hubs), then by scraping the newline page, if omitted.
        country: Destination country slug (lowercase, e.g. 'unitedstates').
                 Auto-resolved from the local routes table if omitted.
        dry_run: If True, verify the route is purchasable (and report its price
                 when available) but don't buy.
        legacy: Force the direct finalize flow, skipping the country listing.

    Returns:
        dict with 'success' (True/False/None), 'message' or 'error', 'flow',
        and context fields. success=None means the outcome was indeterminate.
    """
    cdp = _get_cdp()
    if not cdp:
        return {"error": "No Airlines Manager tab found."}

    hub_iata = hub_iata.upper().strip()
    dest_iata = dest_iata.upper().strip()

    # Resolve hub_id: local DB first, then scrape the newline page.
    if not hub_id:
        hub_id = _lookup_player_hub_id(hub_iata)
    if not hub_id:
        cdp.navigate_and_wait(
            f"{BASE_URL}/network/newline",
            "document.querySelectorAll('.hubListBox[data-hubid]').length",
        )
        hub_id = cdp.eval(
            "(((HUB) => {"
            "  const boxes = document.querySelectorAll('.hubListBox[data-hubid]');"
            "  for (const b of boxes) {"
            "    const name = (b.querySelector('.title')?.textContent || '').trim().toUpperCase();"
            "    if (name.startsWith(HUB)) return b.getAttribute('data-hubid');"
            "  }"
            "  return null;"
            f"}})({js_args(hub_iata)}))"
        )
    if not hub_id or isinstance(hub_id, dict):
        return {"error": f"Could not resolve hub_id for {hub_iata}. Pass hub_id explicitly."}
    hub_id = str(hub_id)

    if not country and not legacy:
        country = _lookup_dest_country(hub_iata, dest_iata)

    use_country_flow = bool(country) and not legacy

    # ── Country-listing flow ─────────────────────────────────────────────────
    if use_country_flow:
        cdp.navigate(f"{BASE_URL}/network/newline/{hub_id}/{country}")
        if not wait_for_listing(cdp, country):
            return {"error": f"Country listing for '{country}' did not load.",
                    "hub_iata": hub_iata, "dest_iata": dest_iata, "hub_id": hub_id}
        card = find_country_card(cdp, dest_iata)
        if not card:
            return {"error": f"{dest_iata} not found on the '{country}' listing page "
                             f"(already owned, or wrong country?).",
                    "hub_iata": hub_iata, "dest_iata": dest_iata,
                    "country": country, "hub_id": hub_id}
        if dry_run:
            return {"success": False, "dry_run": True, "flow": "country",
                    "message": f"{hub_iata}->{dest_iata} is purchasable.",
                    "price": card.get("price"), "country": country, "hub_id": hub_id}
        href = card.get("href")
        if href:
            target = href if href.startswith("http") else f"{BASE_URL}{href}"
        else:
            target = f"{BASE_URL}/network/newlinefinalize/{hub_id}/{dest_iata.lower()}"
        success, msg = finalize_purchase(cdp, target)

    # ── Direct finalize flow ─────────────────────────────────────────────────
    else:
        target = f"{BASE_URL}/network/newlinefinalize/{hub_id}/{dest_iata.lower()}"
        if dry_run:
            if not cdp.navigate_and_wait(target, "!!document.getElementById('linePurchaseForm')"):
                return {"error": f"Route {hub_iata}->{dest_iata} is not available for purchase.",
                        "hub_iata": hub_iata, "dest_iata": dest_iata, "hub_id": hub_id}
            return {"success": False, "dry_run": True, "flow": "direct",
                    "message": f"{hub_iata}->{dest_iata} is purchasable.", "hub_id": hub_id}
        success, msg = finalize_purchase(cdp, target)

    result = {
        "hub_iata": hub_iata, "dest_iata": dest_iata, "hub_id": hub_id,
        "flow": "country" if use_country_flow else "direct", "message": msg,
    }
    if success is True:
        _mark_route_owned(hub_iata, dest_iata)
        result["success"] = True
    elif success is False:
        result["success"] = False
        result["error"] = f"Purchase failed: {msg}"
    else:
        result["success"] = None
        result["warning"] = f"Purchase outcome unknown: {msg}"
    return result


@mcp.tool()
def resolve_aircraft(query: str) -> dict:
    """Resolve an aircraft name/alias/ICAO/colloquial spelling to its canonical model.

    Lets you turn any spelling the user types ('A380', 'A388', 'A380-800', 'a380 800')
    into the exact model string the other tools expect, without scanning the database.

    Args:
        query: Any aircraft name, ICAO code, or partial/family name.

    Returns:
        dict with 'status' ('ok' | 'ambiguous' | 'not_found') and:
          - ok: 'model' (canonical) and 'icao'.
          - ambiguous: 'candidates' (list of {model, icao}); pass one 'model' back.
          - not_found: 'suggestions' (nearest {model, icao} matches).
    """
    return asdict(resolve_aircraft_name(query))


@mcp.resource("aircraft://catalog")
def aircraft_catalog() -> str:
    """Full catalog of aircraft: canonical model, ICAO code, and known aliases.

    Read this once to self-serve aircraft names instead of guessing or scanning the
    DB. JSON list of {model, icao, aliases}. Bare family names ('747', 'A380') are
    handled by the resolve_aircraft tool, not listed here.
    """
    return json.dumps(aircraft_catalog_data(), indent=2)


@mcp.tool()
@_serialized_cdp
def list_aircraft_for_sale(haul: str = "long") -> dict:
    """List aircraft available for purchase.

    Args:
        haul: Aircraft category - 'short', 'middle', 'long', or 'cargo'.

    Returns:
        dict with 'aircraft' (list of {model, game_id, speed, range, category}),
        'haul', 'count'.
    """
    cdp = _get_cdp()
    if not cdp:
        return {"error": "No Airlines Manager tab found."}

    cdp.navigate(f"{BASE_URL}/aircraft/buy/new/{haul}")
    loaded = _wait_for_js(cdp, "document.querySelectorAll('.aircraftPurchaseBox').length", timeout=20.0)
    if not loaded:
        return {"error": f"No aircraft loaded for haul={haul}. Page may still be loading."}

    aircraft = cdp.eval_json("""((() => {
        return Array.from(document.querySelectorAll('.aircraftPurchaseBox')).map(box => {
            const titleEl = box.querySelector('.title');
            const jsonEl = box.querySelector('.aircraftJson');
            let data = {};
            try { data = JSON.parse(jsonEl.textContent); } catch(e) {}
            return {
                model: titleEl?.textContent?.trim() || '?',
                game_id: data.id,
                speed: data.speed,
                range: data.range,
                category: data.category
            };
        });
    })())""")

    if not aircraft:
        return {"error": "Failed to parse aircraft data.", "haul": haul, "aircraft": []}

    return {"haul": haul, "aircraft": aircraft, "count": len(aircraft)}


@mcp.tool()
@_serialized_cdp
def get_aircraft_at_hub(hub_iata: str) -> dict:
    """List owned aircraft at a hub by navigating to the planning page.

    Args:
        hub_iata: Hub IATA code.

    Returns:
        dict with 'aircraft' (list of {aircraft_id, model, name, utilization_pct}),
        'hub_iata', 'count'.
    """
    hub_iata = hub_iata.upper().strip()
    hub_id = _lookup_player_hub_id(hub_iata)
    if hub_id:
        # The fleet listing has no hub filter, so this pages the whole fleet —
        # 500 per request, which is still far cheaper than driving the planning
        # page, and it carries the seat config the DOM does not.
        # The compact fleet record names the model only by id (`al_id`), so
        # the name comes from mobile_models, which the mobile reads populate.
        models = {r["model_id"]: r["name"] for r in
                  get_db().execute("SELECT model_id, name FROM mobile_models")}

        def _mobile(cl):
            out = [{"aircraft_id": str(it["id"]), "name": (it.get("n") or "").strip(),
                    "model": models.get(it.get("al_id"), ""),
                    "seats": {"eco": it.get("se") or 0, "bus": it.get("sb") or 0,
                              "first": it.get("sf") or 0, "cargo": it.get("sp") or 0}}
                   for it in cl.fleet(per_page=500)
                   if it.get("h_id") == int(hub_id)]
            return {"ok": True, "aircraft": out}

        res = _mobile_call(_mobile, store=False)
        if res.get("ok"):
            return {"hub_iata": hub_iata, "aircraft": res["aircraft"],
                    "count": len(res["aircraft"]), "backend": "mobile"}

    cdp = _get_cdp()
    if not cdp:
        return {"error": "No mobile session and no Airlines Manager tab in Chrome.",
                "hub_iata": hub_iata, "aircraft": []}

    cdp.navigate(f"{BASE_URL}/network/planning")
    if not _wait_for_hub_buttons(cdp, timeout=15.0):
        return {"error": "Planning page did not load hub selector." + _cdp_error_suffix(cdp),
                "hub_iata": hub_iata, "aircraft": []}
    if not _select_planning_hub(cdp, hub_iata):
        return {"error": f"Could not select hub {hub_iata} on the planning page." + _cdp_error_suffix(cdp),
                "hub_iata": hub_iata, "aircraft": []}

    aircraft = _get_aircraft_at_selected_hub(cdp, hub_iata)
    return {"hub_iata": hub_iata, "aircraft": aircraft, "count": len(aircraft)}


@mcp.tool()
@_serialized_cdp
def schedule_flight(
    aircraft_id: str,
    flights: list,
    clear_first: bool = False,
) -> dict:
    """Schedule flights for an aircraft.

    Mobile primary (planning/add per flight), CDP fallback (one POST to
    /network/planning/0/ajax). No UI interaction either way.
    takeOffTime is in seconds from Monday 00:00, must be divisible by 900 (15min).
      Monday 00:00 = 0, Monday 06:00 = 21600, Tuesday 00:00 = 86400.

    Args:
        aircraft_id: Internal aircraft ID (string, from get_aircraft_at_hub).
        flights: List of dicts, each with 'lineId' (str/int) and 'takeOffTime' (int).
        clear_first: If True, wipe existing schedule before adding.

    Returns:
        dict with 'success', 'scheduled' count, 'backend', or 'error'.
    """
    backend = _planning_client()
    if backend is None:
        backend = _get_cdp()
        if not backend:
            return {"error": "No mobile session and no Airlines Manager tab in Chrome."}
        which = "cdp"
    else:
        which = "mobile"

    aircraft_id = str(aircraft_id)

    if clear_first:
        res = clear_schedule(backend, aircraft_id)
        if not res or not res.get("result"):
            return {"success": False, "backend": which,
                    "error": f"Failed to clear existing schedule: {res}"}

    result = submit_flights(backend, aircraft_id, flights)

    if not result:
        return {"error": "Scheduling request returned no response.", "backend": which}
    if result.get("result"):
        return {
            "success": True,
            "aircraft_id": aircraft_id,
            "scheduled": len(flights),
            "backend": which,
            "message": result.get("message", "Schedule updated."),
        }
    return {"success": False, "backend": which, "error": result}


@mcp.tool()
@_serialized_cdp
def get_page_text() -> dict:
    """Get visible text of the current Airlines Manager page.

    Useful for reading demand data, audit results, or content not
    exposed by a dedicated tool.

    Returns:
        dict with 'text' (string, up to 5000 chars) and 'url'.
    """
    cdp = _get_cdp()
    if not cdp:
        return {"error": "No Airlines Manager tab found."}

    url = cdp.eval("window.location.href")
    text = cdp.eval("document.body.innerText")

    return {"url": url, "text": str(text)[:5000] if text else ""}


@mcp.tool()
@_serialized_cdp
def navigate_to(path: str) -> dict:
    """Navigate the AM tab to a specific game page.

    Args:
        path: URL path relative to airlines-manager.com,
              e.g. '/network', '/marketing/internalaudit/68624894'.

    Returns:
        dict with 'navigated_to' and 'requested'.
    """
    cdp = _get_cdp()
    if not cdp:
        return {"error": "No Airlines Manager tab found."}

    if not path.startswith("/"):
        path = "/" + path

    url = f"{BASE_URL}{path}"
    cdp.navigate_and_wait(url, "document.readyState === 'complete'")

    actual_url = cdp.eval("window.location.href")
    return {"navigated_to": actual_url, "requested": url}


@mcp.tool()
def plan_circuits(
    hub: str,
    aircraft: List[str],
    circuits: int = 10,
    owned_hubs: Optional[List[str]] = None,
    phase1_only: bool = False,
    save: bool = False,
    min_dist: Optional[int] = None,
    max_dist: Optional[int] = None,
    beam: int = 1200,
    max_routes: int = 150,
    candidates_per_ac: int = 3,
    comfort: float = 500.0,
    speed: float = 700.0,
    max_waves: int = 30,
    overshoot: float = 0.0,
    wave_slack: float = 0.02,
    match: float = 0.9,
) -> dict:
    """Run the circuit planner script.

    This is the main planning workflow. It is read-mostly unless save=True,
    which persists circuits to the local SQLite DB.
    """
    aircraft = _normalize_iatas(aircraft)
    if not aircraft:
        return {"error": "Provide at least one aircraft alias, e.g. ['B742', 'B744']."}

    args = [
        "--hub", hub.upper().strip(),
        "--aircraft", *aircraft,
        "--circuits", str(circuits),
        "--beam", str(beam),
        "--max-routes", str(max_routes),
        "--candidates-per-ac", str(candidates_per_ac),
        "--comfort", str(comfort),
        "--speed", str(speed),
        "--max-waves", str(max_waves),
        "--overshoot", str(overshoot),
        "--wave-slack", str(wave_slack),
        "--match", str(match),
    ]
    if owned_hubs:
        args.extend(["--owned-hubs", *_normalize_iatas(owned_hubs)])
    if min_dist is not None:
        args.extend(["--min-dist", str(min_dist)])
    if max_dist is not None:
        args.extend(["--max-dist", str(max_dist)])
    if phase1_only:
        args.append("--phase1-only")
    if save:
        args.append("--save")
    args.append("--json")

    result = _run_python_script("circuit_planner.py", args, timeout=600,
                                parse_json=True)
    result.update({
        "hub": hub.upper().strip(),
        "aircraft": aircraft,
        "circuits_requested": circuits,
        "save": save,
        "phase1_only": phase1_only,
    })
    return result


@mcp.tool()
def buy_circuit_routes(
    circuit: Optional[str] = None,
    hub_id: Optional[str] = None,
    iatas: Optional[List[str]] = None,
    dry_run: bool = True,
    legacy: bool = False,
) -> dict:
    """Run the batch route buyer.

    Safety default: dry_run=True because this spends in-game money.
    Provide either circuit=... or iatas=[...].
    """
    iatas = _normalize_iatas(iatas)
    if not circuit and not iatas:
        return {"error": "Provide either circuit='HKG-C001' or iatas=['LAX', 'NRT']."}

    if not hub_id and circuit:
        hub_hint = circuit.split("-", 1)[0].upper()
        hub_id = _lookup_player_hub_id(hub_hint)

    if not hub_id:
        return {"error": "hub_id is required for route purchase and could not be auto-resolved from DB."}

    args = []
    if iatas:
        args.extend(iatas)
    if circuit:
        args.extend(["--circuit", circuit])
    args.extend(["--hub-id", str(hub_id)])
    if dry_run:
        args.append("--dry-run")
    if legacy:
        args.append("--legacy")

    result = _run_python_script("circuit_route_buyer.py", args, timeout=600)
    result.update({
        "circuit": circuit,
        "hub_id": str(hub_id),
        "iatas": iatas,
        "dry_run": dry_run,
        "legacy": legacy,
    })
    return result


@mcp.tool()
def buy_aircraft(
    circuit: Optional[str] = None,
    model: Optional[str] = None,
    hub: Optional[str] = None,
    hub_id: Optional[int] = None,
    eco: Optional[int] = None,
    bus: Optional[int] = None,
    first: Optional[int] = None,
    cargo: Optional[int] = None,
    quantity: Optional[int] = None,
    name: Optional[str] = None,
    alliance: bool = False,
    dry_run: bool = True,
    list_only: bool = False,
) -> dict:
    """Run the aircraft buyer script.

    Safety default: dry_run=True because this spends in-game money.
    alliance=True buys via "Purchase through Alliance" (alliance fixed discount
    plus members assistance fronted by the treasury) instead of paying in full
    personally; the personal path takes the game's own variable bulk discount.
    Use either circuit mode or standalone model+hub mode. The 'model' arg accepts
    any spelling (alias/ICAO/colloquial, e.g. 'A380') and is normalized before
    buying; an ambiguous name returns 'candidates' and an unknown one returns
    'suggestions' immediately, without spending money.
    """
    if list_only:
        return _run_python_script("aircraft_buyer.py", ["--list", "--json"],
                                  timeout=180, parse_json=True)

    if not circuit and not model:
        return {"error": "Provide either circuit='HKG-C001' or model='B742'."}
    if model and not circuit and not hub:
        return {"error": "Standalone aircraft purchase requires hub='HKG'-style input."}

    # Pre-flight: normalize the model so 'A380' just works, and fail fast — before the
    # ~900s purchase run — on an ambiguous or unknown name instead of deep in the script.
    if model:
        res = resolve_aircraft_name(model)
        if res.status == "ambiguous":
            return {"error": f"Aircraft '{model}' is ambiguous; pick one model and retry.",
                    "candidates": res.candidates}
        if res.status == "not_found":
            return {"error": f"Unknown aircraft '{model}'.",
                    "suggestions": res.suggestions}
        model = res.model

    args = []
    if circuit:
        args.append(circuit)
    if model:
        args.extend(["--model", model])
    if hub:
        args.extend(["--hub", hub.upper().strip()])
    if hub_id is not None:
        args.extend(["--hub-id", str(hub_id)])
    if eco is not None:
        args.extend(["--eco", str(eco)])
    if bus is not None:
        args.extend(["--bus", str(bus)])
    if first is not None:
        args.extend(["--first", str(first)])
    if cargo is not None:
        args.extend(["--cargo", str(cargo)])
    if quantity is not None:
        args.extend(["--quantity", str(quantity)])
    if name:
        args.extend(["--name", name])
    if alliance:
        args.append("--alliance")
    if dry_run:
        args.append("--dry-run")

    args.append("--json")
    result = _run_python_script("aircraft_buyer.py", args, timeout=900,
                                parse_json=True)
    result.update({
        "circuit": circuit,
        "model": model,
        "hub": hub.upper().strip() if hub else None,
        "alliance": alliance,
        "dry_run": dry_run,
    })
    return result


@mcp.tool()
def schedule_circuits(
    hub: Optional[str] = None,
    circuit: Optional[str] = None,
    dry_run: bool = True,
    list_only: bool = False,
    only_new: bool = False,
) -> dict:
    """Run the circuit scheduler script.

    Safety default: dry_run=True because live scheduling mutates the game.
    """
    if list_only:
        return _run_python_script("circuit_scheduler.py", ["--list", "--json"],
                                  timeout=180, parse_json=True)

    if not hub:
        return {"error": "hub='HKG'-style input is required unless list_only=True."}

    args = ["--hub", hub.upper().strip()]
    if circuit:
        args.extend(["--circuit", circuit])
    if dry_run:
        args.append("--dry-run")
    if only_new:
        args.append("--only-new")

    args.append("--json")
    result = _run_python_script("circuit_scheduler.py", args, timeout=900,
                                parse_json=True)
    result.update({
        "hub": hub.upper().strip(),
        "circuit": circuit,
        "dry_run": dry_run,
        "only_new": only_new,
    })
    return result


@mcp.tool()
def auto_price_routes(
    mode: str = "ideal",
    pct: float = 100.0,
    hub: Optional[str] = None,
    circuit: Optional[str] = None,
    routes: Optional[List[str]] = None,
    max_routes: Optional[int] = None,
    skip_unchanged: bool = True,
    dry_run: bool = True,
) -> dict:
    """Run the pricing script.

    Modes: 'ideal' (corrected ideal price), 'percent' (ideal * pct/100),
    'raw-ideal' (the game's displayed ideal), and 'fill' — the price at which
    remaining demand reaches zero, i.e. the most each seat can be sold for
    while still filling the aircraft.  'fill' needs live seat/demand data, so
    it requires hub or circuit.

    Safety default: dry_run=True because live pricing mutates the game.
    """
    if mode not in {"ideal", "percent", "raw-ideal", "fill"}:
        return {"error": "mode must be one of: ideal, percent, raw-ideal, fill"}
    if mode == "fill" and not (hub or circuit):
        return {"error": "mode 'fill' requires hub or circuit."}

    route_list = _normalize_iatas(routes)
    args = ["--mode", mode]
    if mode == "percent":
        args.extend(["--pct", str(pct)])
    if hub:
        args.extend(["--hub", hub.upper().strip()])
    if circuit:
        args.extend(["--circuit", circuit])
    if route_list:
        args.extend(["--routes", *route_list])
    if max_routes is not None:
        args.extend(["--max", str(max_routes)])
    if skip_unchanged:
        args.append("--skip-unchanged")
    if dry_run:
        args.append("--dry-run")

    args.append("--json")
    # mobile_pricer.py is the primary path: same flags, no Chrome, and it
    # writes through line/price instead of the AM+ masstool endpoint the web
    # form forces. 'raw-ideal' has no mobile equivalent — the mobile audit
    # price is already the corrected one — so that mode stays on CDP.
    use_mobile = mode != "raw-ideal" and _has_mobile_session()
    script = "mobile_pricer.py" if use_mobile else "auto_pricer.py"
    result = _run_python_script(script, args, timeout=900,
                                parse_json=True)
    result["backend"] = "mobile" if use_mobile else "cdp"
    result.update({
        "mode": mode,
        "pct": pct,
        "hub": hub.upper().strip() if hub else None,
        "circuit": circuit,
        "routes": route_list,
        "dry_run": dry_run,
    })
    return result


@mcp.tool()
def refresh_internal_audits(
    hub: str,
    dry_run: bool = True,
    limit: Optional[int] = None,
    sleep_seconds: float = 1.5,
) -> dict:
    """Refresh owned-route demand from internal audit pages.

    Safety default: dry_run=True because live mode writes demand back to SQLite.
    """
    args = ["--hub", hub.upper().strip(), "--sleep", str(sleep_seconds)]
    if dry_run:
        args.append("--dry-run")
    if limit is not None:
        args.extend(["--limit", str(limit)])

    result = _run_python_script("scrape_internal_audits.py", args, timeout=900)
    result.update({
        "hub": hub.upper().strip(),
        "dry_run": dry_run,
        "limit": limit,
    })
    return result


@mcp.tool()
def audit_unpurchased_routes(
    hub: str,
    routes: Optional[List[str]] = None,
    country: Optional[str] = None,
    limit: Optional[int] = None,
    dry_run: bool = True,
    allow_paid: bool = False,
) -> dict:
    """Audit missing demand for unpurchased routes through the mobile API.

    Live mode spends cash and writes each result to SQLite, so it also requires
    allow_paid=True. Preview mode only lists the routes that would be audited.
    """
    args = ["--hub", hub.upper().strip()]
    if routes:
        args.extend(["--routes", *(iata.upper().strip() for iata in routes)])
    if country:
        args.extend(["--country", country.lower().strip()])
    if limit is not None:
        args.extend(["--limit", str(limit)])
    if allow_paid:
        args.append("--allow-paid")
    if not dry_run:
        args.append("--apply")
    result = _run_python_script(
        "mobile_route_auditor.py", args, timeout=3600, parse_json=True)
    result.update({"backend": "mobile", "dry_run": dry_run})
    return result


@mcp.tool()
def sync_warehouse(hub: Optional[str] = None, summary_only: bool = False) -> dict:
    """Sync fleet data from the game into the local warehouse/fleet tables."""
    args = []
    if hub:
        args.extend(["--hub", hub.upper().strip()])
    if summary_only:
        args.append("--summary")

    result = _run_python_script("warehouse_sync.py", args, timeout=600)
    result.update({
        "hub": hub.upper().strip() if hub else None,
        "summary_only": summary_only,
    })
    return result


@mcp.tool()
def get_masstool_data(hub: str, routes: Optional[List[str]] = None) -> dict:
    """Fetch live pricing and remaining-demand data from the masstool endpoint."""
    route_list = _normalize_iatas(routes)
    args = [hub.upper().strip()]
    if route_list:
        args.extend(["--routes", *route_list])
    args.append("--json")

    result = _run_python_script("masstool.py", args, timeout=300, parse_json=True)
    result.update({
        "hub": hub.upper().strip(),
        "routes": route_list,
    })
    return result


@mcp.tool()
def number_circuit_aircraft(circuit: str, dry_run: bool = True) -> dict:
    """Assign canonical names <HUB>-C<NNN>-<MMM> to a circuit's aircraft.

    Wraps aircraft_numberer.py. Normalizes 1-2 digit suffixes to 3-digit,
    numbers freshly-bought bare aircraft into the next free slot, pushes excess
    aircraft to <MODEL>-STORAGE-NNN, and updates waves_bought in the DB.

    Safety default: dry_run=True because live mode renames in-game aircraft
    and writes to SQLite.
    """
    args = ["--circuit", circuit.upper().strip()]
    if dry_run:
        args.append("--dry-run")

    # aircraft_numberer picks its own backend: renaming is its only in-game
    # action, and the mobile API does it without a browser.
    result = _run_python_script("aircraft_numberer.py", args, timeout=900)
    result["backend"] = "mobile" if _has_mobile_session() else "cdp"
    result.update({"circuit": circuit.upper().strip(), "dry_run": dry_run})
    return result


@mcp.tool()
def reconfigure_circuit_aircraft(circuit: str, dry_run: bool = True) -> dict:
    """Relocate + reconfigure a circuit's aircraft to its planned hub and seats.

    Wraps aircraft_reconfigurator.py. For each aircraft named <HUB>-C<NNN>
    (optionally -<MMM>), posts the circuit's hub and seat layout from the DB.
    Preserves the currently-applied livery (refuses to reconfigure if it cannot
    confirm the checked skin).

    Safety default: dry_run=True because live mode mutates the in-game fleet.
    """
    args = ["--circuit", circuit.upper().strip()]
    if dry_run:
        args.append("--dry-run")

    # Mobile first: same CLI, same DB, but seats and hub go over the JSON API,
    # where a refused write raises instead of silently doing nothing.
    use_mobile = _has_mobile_session()
    result = _run_python_script(
        "mobile_reconfigurator.py" if use_mobile else "aircraft_reconfigurator.py",
        args, timeout=900)
    result["backend"] = "mobile" if use_mobile else "cdp"
    result.update({"circuit": circuit.upper().strip(), "dry_run": dry_run})
    return result


@mcp.tool()
def rename_circuit(old: str, new: str, dry_run: bool = True, db_only: bool = False) -> dict:
    """Rename a circuit in the DB and rename its aircraft in-game to match.

    Wraps circuit_renamer.py. Updates circuits + circuit_routes rows from
    <old> to <new>, then renames every aircraft named <old> or <old>-<MMM>.

    Safety default: dry_run=True because live mode mutates the DB and the fleet.
    Set db_only=True to update just the DB and skip in-game renames.
    """
    args = ["--old", old.upper().strip(), "--new", new.upper().strip()]
    if dry_run:
        args.append("--dry-run")
    if db_only:
        args.append("--db-only")

    result = _run_python_script("circuit_renamer.py", args, timeout=900)
    result.update({
        "old": old.upper().strip(),
        "new": new.upper().strip(),
        "dry_run": dry_run,
        "db_only": db_only,
    })
    return result


@mcp.tool()
def mass_rename_aircraft(
    old: str,
    new: str,
    limit: int = 0,
    strip_suffix: bool = False,
    dry_run: bool = True,
) -> dict:
    """Bulk-rename in-game aircraft from one name prefix to another (no DB writes).

    Wraps mass_renamer.py. Aircraft named <old> or <old>-<NNN> become <new> or
    <new>-<NNN>. Use case: repurpose a defunct circuit's aircraft to a new name.

    Safety default: dry_run=True because live mode renames in-game aircraft.
    limit=0 means rename all matches; strip_suffix drops the -NNN tail.
    """
    args = ["--old", old.strip(), "--new", new.strip()]
    if limit and limit > 0:
        args.extend(["--limit", str(limit)])
    if strip_suffix:
        args.append("--strip-suffix")
    if dry_run:
        args.append("--dry-run")

    # Mobile first: one request per rename (no per-aircraft form token), and
    # echoing the current seats back makes the reconfigure a free no-op.
    use_mobile = _has_mobile_session()
    result = _run_python_script(
        "mobile_renamer.py" if use_mobile else "mass_renamer.py", args, timeout=900)
    result["backend"] = "mobile" if use_mobile else "cdp"
    result.update({
        "old": old.strip(),
        "new": new.strip(),
        "limit": limit,
        "strip_suffix": strip_suffix,
        "dry_run": dry_run,
    })
    return result


@mcp.tool()
def mass_unschedule_aircraft(prefixes: List[str], dry_run: bool = True) -> dict:
    """Clear flight schedules for all aircraft whose name starts with a prefix.

    Wraps mass_unscheduler.py. Matches aircraft whose name starts with any of
    the given prefixes (case-insensitive) and clears each one's schedule.

    Safety default: dry_run=True because live mode wipes in-game schedules.
    """
    prefix_list = [p.strip() for p in (prefixes or []) if p and p.strip()]
    if not prefix_list:
        return {"error": "Provide at least one name prefix, e.g. ['MPM-C007']."}

    args = list(prefix_list)
    if dry_run:
        args.append("--dry-run")

    result = _run_python_script("mass_unscheduler.py", args, timeout=900)
    result.update({"prefixes": prefix_list, "dry_run": dry_run})
    return result


@mcp.tool()
def scrape_line_ids(hub: Optional[str] = None, dry_run: bool = True) -> dict:
    """Scrape owned-route line_ids from the planning page into the DB.

    Wraps scrape_line_ids.py. For each player hub (or just `hub`), selects the
    hub on /network/planning, reads owned routes + lineIds, and upserts the
    routes table.

    Safety default: dry_run=True because live mode writes to SQLite.
    """
    args = []
    if hub:
        args.extend(["--hub", hub.upper().strip()])
    if dry_run:
        args.append("--dry-run")

    result = _run_python_script("scrape_line_ids.py", args, timeout=900)
    result.update({"hub": hub.upper().strip() if hub else None, "dry_run": dry_run})
    return result


@mcp.tool()
def scrape_audit_line_ids(hub: Optional[str] = None, dry_run: bool = True) -> dict:
    """Scrape line_ids from the internal-audit linelist into the DB.

    Wraps scrape_audit_line_ids.py. For each player hub (or just `hub`), fetches
    /marketing/internalaudit/linelist, parses line_ids + dest IATAs, and
    upserts the routes table.

    Safety default: dry_run=True because live mode writes to SQLite.
    """
    args = []
    if hub:
        args.extend(["--hub", hub.upper().strip()])
    if dry_run:
        args.append("--dry-run")

    result = _run_python_script("scrape_audit_line_ids.py", args, timeout=900)
    result.update({"hub": hub.upper().strip() if hub else None, "dry_run": dry_run})
    return result


# ── Mobile-app tools (SHM + daily login rewards) ─────────────────────────────
# These target the MOBILE game API (auction/second-hand market and daily
# rewards), which the browser/CDP surface does not expose. They authenticate
# with the mobile access_token (not the web session), stored at
# ~/.airlines_manager/session.json. Separate endpoints, separate auth — not
# merged with the CDP tools above.

import random  # noqa: E402
import time as _time  # noqa: E402

DEFAULT_CAPTURE = os.path.expanduser(
    "~/Python Projects/airlines-manager-tools/tools/captures/am_api.jsonl")


def _mobile_call(fn, *, min_delay: float = 0.0, store: bool = True):
    """Build a mobile client, run fn(client), return its dict; errors → dict."""
    try:
        from mobile_api import AMClient, AMSession, AMError, AMAuthError
        from mobile_store import MobileStore
    except Exception as e:  # httpx / import issue
        return {"ok": False, "error": f"mobile module import failed: {e}"}
    try:
        session = AMSession.load()
    except AMAuthError as e:
        return {"ok": False, "error": str(e),
                "hint": "Refresh with mobile_session_import(capture_path=...)."}
    client = AMClient(session, min_delay=min_delay,
                      store=(MobileStore() if store else None))
    try:
        return fn(client)
    except AMError as e:
        return {"ok": False, "error": str(e)}
    finally:
        client.close()


@mcp.tool()
def mobile_session_import(capture_path: Optional[str] = None) -> dict:
    """Refresh the mobile API session from a mitmproxy capture of the app.

    The mobile access_token expires (~daily). Open the mobile app so it makes a
    fresh authenticated call through the running capture, then call this to pull
    the newest token from the capture JSONL and save it to
    ~/.airlines_manager/session.json. Validates immediately.
    """
    try:
        from mobile_api import import_from_capture, AMClient
    except Exception as e:
        return {"ok": False, "error": f"mobile module import failed: {e}"}
    path = capture_path or DEFAULT_CAPTURE
    if not os.path.exists(path):
        return {"ok": False, "error": f"capture not found: {path}"}
    try:
        sess = import_from_capture(path)
        sess.save()
    except Exception as e:
        return {"ok": False, "error": f"import failed: {e}"}
    client = AMClient(sess)
    try:
        res = client.resources()
        return {"ok": True, "player_id": sess.player_id,
                "token_tail": sess.access_token[-8:],
                "balance": res.get("dollar"), "coins": res.get("amCoins")}
    except Exception as e:
        return {"ok": False, "error": f"saved but validation failed: {e}"}
    finally:
        client.close()


@mcp.tool()
def mobile_session_renew() -> dict:
    """Mint a fresh mobile access_token over HTTP — no emulator, no capture.

    Access tokens last 3h. Once a session has been bootstrapped once (see
    mobile_session_import), it carries the OAuth client credentials and can
    renew itself: refresh-token grant first, password grant as fallback. The
    mobile tools also do this automatically on an auth error, so calling this
    by hand is mostly for checking the plumbing.
    """
    try:
        from mobile_api import AMSession, AMClient, AMError, AMAuthError
    except Exception as e:
        return {"ok": False, "error": f"mobile module import failed: {e}"}
    try:
        sess = AMSession.load()
    except AMAuthError as e:
        return {"ok": False, "error": str(e)}
    if not sess.can_renew:
        return {"ok": False,
                "error": "session has no OAuth material — bootstrap once with "
                         "refresh_mobile_session.sh, then this works over HTTP.",
                "has_client": bool(sess.client_id),
                "has_refresh_token": bool(sess.refresh_token),
                "has_password": bool(sess.password)}
    try:
        how = sess.renew()
    except AMError as e:
        return {"ok": False, "error": str(e)}
    client = AMClient(sess)
    try:
        res = client.resources()
        return {"ok": True, "grant": how, "player_id": sess.player_id,
                "token_tail": sess.access_token[-8:],
                "expires_in_min": (round((sess.expires_at - time.time()) / 60)
                                   if sess.expires_at else None),
                "balance": res.get("dollar")}
    except Exception as e:
        return {"ok": False, "error": f"renewed but validation failed: {e}"}
    finally:
        client.close()


@mcp.tool()
def mobile_balance() -> dict:
    """Mobile account balances: dollars, AM coins, research$, travel cards."""
    return _mobile_call(lambda cl: {"ok": True, **cl.resources()}, store=False)


@mcp.tool()
def mobile_deliveries() -> dict:
    """What is still in delivery — the app's waiting list, in one request.

    Freshly-minted aircraft are NOT sellable until they leave this list, and
    nothing on the aircraft record says so (`purchasedAt` reads an hour ahead
    while a plane is undelivered). Aircraft entries give `aircraft_id`,
    `finish_at` (the real deadline, same clock as `server_time`) and
    `am_coins_to_skip`. Pass `claim_finished=True` to press the app's
    "deliver all finished" button first.
    """
    def run(cl):
        now, events = cl._events()
        return {"ok": True, "server_time": now, "pending": len(events),
                "events": [{"event_id": e.get("id"), "type": e.get("type"),
                            "label": e.get("label"),
                            "aircraft_id": e.get("objectid"),
                            "finish_at": e.get("finishAt", {}).get("date"),
                            "am_coins_to_skip": e.get("amount")}
                           for e in events]}
    return _mobile_call(run, store=False)


@mcp.tool()
def shm_market(contains: str = "", model_id: Optional[int] = None,
               pool_only: bool = False, limit: int = 20) -> dict:
    """Browse the second-hand market. Price stats + live listings for a model.

    Filter by skin/model name substring (contains) or model id. pool_only limits
    to your star bracket. Read-only — use for arbitrage price discovery.

    The endpoint caps at 100 auctions out of a market in the thousands, so an
    unfiltered read is a sample. `model_id` is applied SERVER-side and is the
    only way to get a complete picture: under the cap it returns every live
    listing of that model. `truncated` says whether the read hit the cap.
    """
    def run(cl):
        from mobile_api import AUCTION_PAGE_LIMIT
        aucs = cl.auctions(pool_only=pool_only, model_id=model_id)
        truncated = len(aucs) >= AUCTION_PAGE_LIMIT
        rows = []
        for a in aucs:
            ac = a["aircraft"]
            if contains and contains.lower() not in ac["skin"]["name"].lower():
                continue
            rows.append(a)
        rows.sort(key=lambda x: x.get("timeLeft", 0))
        cur = sorted(a["currentPrice"] for a in rows) if rows else []
        bins = sorted(a["binPrice"] for a in rows if a["binPrice"] > 0)
        listings = [{"auction_id": a["id"], "model_id": a["aircraft"]["aircraftListId"],
                     "skin": a["aircraft"]["skin"]["name"],
                     "current": a["currentPrice"], "bin": a["binPrice"],
                     "time_left_s": a["timeLeft"], "bids": a.get("countParticipant", 0)}
                    for a in rows[:limit]]
        return {"ok": True, "matched": len(rows), "truncated": truncated,
                "current_price": {"min": cur[0], "median": cur[len(cur)//2],
                                  "max": cur[-1]} if cur else None,
                "bin_price": {"min": bins[0], "median": bins[len(bins)//2],
                              "max": bins[-1]} if bins else None,
                "listings": listings}
    return _mobile_call(run)


# ── SHM livery watcher ──────────────────────────────────────────────────────
# Standing orders for specific liveries: shm_watcher.py holds the watchlist and
# the buy logic, these just expose it. Buying stays off unless dry_run=False.
def _watch_db():
    import shm_watcher
    return shm_watcher, shm_watcher.open_db()


@mcp.tool()
def shm_watch_add(skin_id: int, max_price: Optional[int] = None,
                  want: int = 1, label: Optional[str] = None) -> dict:
    """Watch one livery on the second-hand market, to buy on sight.

    `max_price` is the highest buy-now (binPrice) to accept — the number as it
    shows on the market. Without one it observes every listing; use the SHM
    monitor's arm toggle to allow a live buy while the balance stays positive.
    The aircraft model is resolved from the livery so the watcher can use the
    cheap server-side filter.
    """
    sw, conn = _watch_db()
    w = sw.add_watch(conn, skin_id, max_price, want, "manual", label)
    return {"ok": True, "skin_id": w.skin_id, "model_id": w.model_id,
            "label": w.label, "max_price": w.max_price, "want": w.want,
            "note": None if w.model_id else
                    "no model id known for this livery — it can only be caught "
                    "by the wide sweep, not the per-model filter"}


@mcp.tool()
def shm_watch_add_booster(booster_id: int, max_price: Optional[int] = None,
                          include_manufacturer: bool = False) -> dict:
    """Watch every livery in a booster's drop table (the limited-time ones).

    Reads the cached drop table, so run booster_sync.py first.
    Manufacturer paints are excluded by default.
    """
    sw, conn = _watch_db()
    skins = sw.booster_skins(conn, booster_id, include_manufacturer=include_manufacturer)
    if not skins:
        return {"ok": False, "error": f"no cached cards for booster {booster_id}",
                "hint": "run code/booster_sync.py to pull the drop table first"}
    for skin_id, label in skins:
        sw.add_watch(conn, skin_id, max_price, 1, f"booster:{booster_id}", label)
    models = {w.model_id for w in sw.active_watches(conn) if w.model_id}
    return {"ok": True, "added": len(skins), "models": len(models),
            "requests_per_full_pass": len(models),
            "liveries": [label for _, label in skins]}


@mcp.tool()
def shm_watch_list() -> dict:
    """The livery watchlist, with what each one has been seen listed at."""
    sw, conn = _watch_db()
    rows = conn.execute("""
        SELECT w.skin_id, w.model_id, w.label, w.max_price, w.want, w.bought,
               w.active,
               (SELECT MIN(s.bin_price) FROM shm_sightings s
                 WHERE s.skin_id = w.skin_id AND s.bin_price > 0
                   AND s.is_own = 0) AS cheapest_seen
          FROM shm_watch w ORDER BY w.active DESC, w.model_id, w.skin_id
    """).fetchall()
    n, spend = sw.spent_today(conn)
    return {"ok": True, "watching": [dict(r) for r in rows],
            "today": {"buys": n, "spent": spend}}


@mcp.tool()
def shm_watch_remove(skin_id: int) -> dict:
    """Stop watching a livery."""
    sw, conn = _watch_db()
    return {"ok": True, "removed": sw.remove_watch(conn, skin_id)}


@mcp.tool()
def shm_snipe(dry_run: bool = True, per_pass: int = 12,
              daily_budget: Optional[int] = None, reserve_bids: int = 0,
              pool_only: bool = False) -> dict:
    """One watch pass: read the market, buy anything on the watchlist.

    Buys at the listing's own buy-now price, cheapest first, and never places
    an incremental bid — so it either takes a plane under the cap you set or
    does nothing. Guarded by the per-livery cap, `daily_budget`, the game's own
    `maxBidByDay`, and the account balance. Nothing is spent with dry_run.

    `per_pass` models are polled per call (one request each) plus one wide
    sweep; a longer watchlist rotates least-recently-checked first.
    """
    def run(cl):
        sw, conn = _watch_db()
        return sw.one_pass(cl, conn, arm=not dry_run, per_pass=per_pass,
                           sweep=True, pool_only=pool_only,
                           reserve_bids=reserve_bids, daily_budget=daily_budget,
                           verbose=False)
    return _mobile_call(run)


@mcp.tool()
def shm_fleet(name_contains: str = "", skin_id: Optional[int] = None,
              limit: int = 40, all_pages: bool = False) -> dict:
    """List owned (mobile) aircraft — pick sell candidates.

    Filter by nickname substring or skin id. Freshly-bought planes are named
    SHOP-<model> or your buy-name, so e.g. name_contains='747' finds them.
    """
    def run(cl):
        out = []
        for it in cl.fleet(max_pages=None if all_pages else 2):
            if name_contains and name_contains.lower() not in it.get("n", "").lower():
                continue
            if skin_id is not None and it.get("as_id") != skin_id:
                continue
            out.append({"aircraft_id": it["id"], "name": it.get("n"),
                        "skin_id": it.get("as_id"), "hub_id": it.get("h_id")})
            if len(out) >= limit:
                break
        return {"ok": True, "count": len(out), "aircraft": out}
    return _mobile_call(run)


@mcp.tool()
def shm_aircraft(aircraft_id: int) -> dict:
    """Full profile of one owned mobile aircraft (model, raw price, hub, seats).

    Read `binThreshold` here before selling — it is the **max buy-it-now the
    game accepts**, and it is per-livery (a Spirit 747SP caps at $1.209B, an
    Il-96-300 Tokyo Sports Event at $8B). `maxAuctionSellPrice` is the separate
    cap on the *starting bid* (= the model's raw value).
    """
    return _mobile_call(lambda cl: {"ok": True, **(cl.aircraft(aircraft_id) or {})})


@mcp.tool()
def apply_livery(skin_id: int, aircraft_ids: List[int],
                 dry_run: bool = True) -> dict:
    """Repaint owned aircraft in a livery you already own (free, mobile API).

    Raises the sell ceiling before an arbitrage listing: `binThreshold` is
    per-livery, so a 747SP in Spirit 747SP paint (skin 4661635) caps at
    $1.209B against $900M in the manufacturer livery. Find the ids you own via
    the duty free (`purchased: true`) — an owned livery costs no AM coins to
    apply, and applying does not touch `maxAuctionSellPrice`.

    **Only ever repaint a manufacturer-livery plane.** Awarded challenge and
    event liveries cannot be re-applied once painted over, so every target is
    checked here and one wearing a non-manufacturer livery is skipped, not
    repainted. Safety default: dry_run=True.
    """
    def run(cl):
        targets, skipped = [], []
        for ac in aircraft_ids:
            cur = (cl.aircraft(int(ac)) or {}).get("skins") or {}
            if "manufacturer livery" in (cur.get("name") or "").lower():
                targets.append(int(ac))
            else:
                skipped.append({"aircraft_id": int(ac), "wears": cur.get("name"),
                                "skin_id": cur.get("id")})
        if dry_run:
            return {"ok": True, "dry_run": True, "would_paint": targets,
                    "skipped_non_manufacturer": skipped, "skin_id": skin_id}
        if not targets:
            return {"ok": False, "error": "no manufacturer-livery targets",
                    "skipped_non_manufacturer": skipped}
        res = cl.apply_skin(skin_id, targets)
        after = cl.aircraft(targets[0]) or {}
        return {"ok": True, "painted": targets, "skipped_non_manufacturer": skipped,
                "message": res.get("message"),
                "skin_now": (after.get("skins") or {}).get("name"),
                "bin_threshold": after.get("binThreshold")}
    return _mobile_call(run)


@mcp.tool()
def shm_sell(aircraft_id: int, bin_price: int, price: Optional[int] = None,
             duration: int = 11, dry_run: bool = True) -> dict:
    """List one owned aircraft on the second-hand market.

    bin_price = buy-it-now (the sell target); price = starting bid (defaults to
    bin_price); duration in hours. Safety default: dry_run=True (a listing is a
    real market action). The SHM allows at most 10 active listings at once.

    **Arbitrage rule: always sell at the max price.** For an arbitrage plane
    (the 747SP flip especially) set bin_price to the aircraft's `binThreshold`
    from `shm_aircraft` — the game's own ceiling — never a hand-picked lower
    number. Set `price` (the starting bid) to `maxAuctionSellPrice`, which is
    the model's raw value, so a one-bid auction can never close below cost.
    """
    price = price if price is not None else bin_price
    if dry_run:
        return {"ok": True, "dry_run": True, "would_list": aircraft_id,
                "price": price, "bin_price": bin_price, "duration_h": duration}

    def run(cl):
        auc = cl.put_up(aircraft_id, price, bin_price, duration)
        return {"ok": True, "aircraft_id": aircraft_id, "auction_id": auc.get("id"),
                "bin_price": auc.get("binPrice"),
                "fair_value": auc.get("alertThreshold"),
                "time_left_s": auc.get("timeLeft")}
    return _mobile_call(run)


@mcp.tool()
def shm_sell_batch(bin_price: int, ids: Optional[List[int]] = None,
                   name_contains: str = "", skin_id: Optional[int] = None,
                   price: Optional[int] = None, duration: int = 11,
                   limit: int = 10, min_delay: float = 3.0,
                   dry_run: bool = True) -> dict:
    """Bulk-list aircraft on the SHM, respecting the 10-active-listing cap.

    Provide explicit `ids` (e.g. freshly-minted planes, reliable during delivery)
    OR a fleet filter (name_contains / skin_id). Caps the run at the free listing
    slots (10 − current active listings), stops on the auction limit, retries the
    put_up rate-limit (204). Safety default: dry_run=True.

    One bin_price covers the whole batch, so only batch aircraft that share a
    livery — `binThreshold` is per-livery. Same arbitrage rule as `shm_sell`:
    bin_price = that livery's `binThreshold`, price = `maxAuctionSellPrice`.
    """
    price = price if price is not None else bin_price
    from mobile_api import (MAX_ACTIVE_LISTINGS, AMAuctionLimit, AMRateLimited,
                            AMError, AMNotDelivered)

    def run(cl):
        # Resolve candidates.
        if ids:
            candidates = [{"id": int(i), "n": "(by id)"} for i in ids]
        else:
            candidates = []
            for it in cl.fleet(max_pages=None):
                if name_contains and name_contains.lower() not in it.get("n", "").lower():
                    continue
                if skin_id is not None and it.get("as_id") != skin_id:
                    continue
                candidates.append({"id": it["id"], "n": it.get("n")})
        # Claim first: a delivery that has passed its finishAt still sits in
        # the queue, and its plane stays unsellable, until something presses
        # "deliver all finished". That is what stalled the 2026-08-25 run —
        # it only completed when a human opened the app. No-op when there is
        # nothing finished to claim.
        if not dry_run:
            cl.deliver_finished()
        # Drop anything still in delivery — put_up would answer `status=0
        # message=0` and burn retries against an empty message. One read for
        # the whole batch, before the slot cap, so undelivered planes do not
        # eat listing slots in the plan.
        pending = cl.pending_aircraft_ids()
        not_delivered = [c["id"] for c in candidates if c["id"] in pending]
        candidates = [c for c in candidates if c["id"] not in pending]
        # Cap to free listing slots.
        active = cl.my_listings_count() or 0
        free_slots = max(0, MAX_ACTIVE_LISTINGS - active)
        cap = min(limit, free_slots if free_slots else limit)
        planned = candidates[:cap]
        if dry_run:
            return {"ok": True, "dry_run": True, "active_listings": active,
                    "free_slots": free_slots, "would_list": len(planned),
                    "aircraft": planned, "not_delivered": not_delivered,
                    "bin_price": bin_price}
        if free_slots == 0:
            return {"ok": False, "error": "Auction limit reached (10 active). "
                    "List more as current auctions conclude.",
                    "active_listings": active}

        listed, failed, limit_hit = [], [], False
        for c in planned:
            ok = False
            for attempt in range(5):
                try:
                    auc = cl.put_up(c["id"], price, bin_price, duration)
                    listed.append({"aircraft_id": c["id"], "auction_id": auc.get("id")})
                    ok = True
                    break
                except AMRateLimited:
                    _time.sleep(min_delay * (attempt + 2) + random.uniform(0, 1.5))
                except AMAuctionLimit:
                    limit_hit = True
                    break
                except AMNotDelivered:
                    # Delivered between the batch pre-check and this put_up.
                    not_delivered.append(c["id"])
                    break
                except AMError as e:
                    failed.append({"aircraft_id": c["id"], "error": str(e)})
                    break
            if limit_hit:
                break
            if ok:
                _time.sleep(min_delay + random.uniform(0, min_delay))
        return {"ok": True, "listed": len(listed), "auctions": listed,
                "failed": failed, "not_delivered": not_delivered,
                "auction_limit_reached": limit_hit, "active_before": active}
    # min_delay=0 on the client so the fleet scan is fast; the put_up posts are
    # paced explicitly in the loop above.
    return _mobile_call(run, min_delay=0.0)


# ---- daily login rewards ----

_CURRENCY_LABEL = {"d": "money", "rd": "research", "tr": "travel_cards", "t": "tickets"}


def _free_daily_offers(offers):
    return [o for o in offers if o.get("purchaseCost") == 0
            and o.get("subCategoryId") == 308
            and o.get("purchaseCurrency") in ("adv", "free")]


@mcp.tool()
def mobile_daily_status() -> dict:
    """Read-only: what daily rewards are still claimable (currency / wheel / slot)."""
    def run(cl):
        offers = _free_daily_offers(cl.shop_offers())
        wheel = cl.wheel_rules()
        slot = cl.slot_rules()
        return {"ok": True,
                "currency_offers": [{"offer_id": o["id"], "remaining": o.get("remaining"),
                                     "picture": o.get("picturePath", "").rsplit("/", 1)[-1]}
                                    for o in offers],
                "wheel": {"can_play": wheel.get("isAllowToPlay"),
                          "can_replay": wheel.get("isAllowToRePlay")},
                "slot": {"free_games_left": slot.get("nbRemainingGames"),
                         "can_play": slot.get("isAllowToPlay")}}
    return _mobile_call(run)


@mcp.tool()
def mobile_daily_bonuses(dry_run: bool = True, min_delay: float = 1.0) -> dict:
    """Claim the FAST daily rewards: free shop currency (5×/day each) + the wheel.

    Free currency: money/coins/research/tickets. Wheel: spin + always respin
    (server keeps the higher). The slow slot machine is a separate tool
    (mobile_daily_slot) because of its per-spin cooldown. Safety default:
    dry_run=True — pass dry_run=False to actually claim.
    """
    def run(cl):
        offers = _free_daily_offers(cl.shop_offers())
        wheel = cl.wheel_rules()
        plan = {o["id"]: int(o.get("remaining", 0) or 0) for o in offers
                if o.get("isAvailable")}
        if dry_run:
            return {"ok": True, "dry_run": True,
                    "currency_claims": sum(plan.values()),
                    "wheel_available": bool(wheel.get("isAllowToPlay")
                                            or wheel.get("isAllowToRePlay"))}
        before = cl.last_resources or cl.resources()
        claims = 0
        for oid, n in plan.items():
            for _ in range(n):
                try:
                    cl.claim_offer(oid)
                    claims += 1
                    _time.sleep(random.uniform(0, min_delay))
                except Exception:
                    break
        wheel_gain = None
        if wheel.get("isAllowToPlay"):
            r = cl.wheel_play()
            if r.get("isAllowToReplay"):
                _time.sleep(random.uniform(0.5, 1.5))
                r = cl.wheel_replay()
            wheel_gain = r.get("keptGain")
        elif wheel.get("isAllowToRePlay"):
            r = cl.wheel_replay()
            wheel_gain = r.get("keptGain")
        after = cl.last_resources or {}
        gains = {k: (after.get(k, 0) - before.get(k, 0))
                 for k in ("dollar", "amCoins", "researchDollars", "travelCards")
                 if before.get(k) is not None and after.get(k) is not None}
        return {"ok": True, "currency_claims": claims, "gains": gains,
                "wheel_travel_cards": wheel_gain}
    return _mobile_call(run, min_delay=0.0)  # claims paced in-loop


def _slot_event(rules: dict) -> Optional[dict]:
    """Progress toward the running slot event's spin-milestone gift, if any.

    `specialEvent.playCountForGift` is the spin count that earns the event
    reward (a livery); `playCount` is the running total, which the server keeps
    across days for the event window — so the milestone is reached by spinning
    the free daily allowance on enough days, not by paying for extra games.
    """
    ev = rules.get("specialEvent") or {}
    target = int(ev.get("playCountForGift") or 0)
    if not target:
        return None
    done = int(ev.get("playCount") or 0)
    return {"event": ev.get("label"), "spins_done": done,
            "spins_for_gift": target, "spins_to_go": max(0, target - done),
            "ends": (ev.get("endDate") or {}).get("date")}


@mcp.tool()
def mobile_daily_slot(max_spins: Optional[int] = None, spin_delay: float = 9.0,
                      dry_run: bool = True) -> dict:
    """Spin the slot machine for all FREE daily games (never spends tickets).

    Bounded to nbRemainingGames — never spins into paid territory. Paces ~9s/spin
    to clear the reel cooldown (spins under it are rejected but still burn a game,
    so they are never retried). SLOW: a full day (~20 spins) takes ~3 min. Safety
    default: dry_run=True.
    """
    from mobile_api import AMAuthError, AMError
    import httpx

    def run(cl):
        rules = cl.slot_rules()
        free = int(rules.get("nbRemainingGames", 0) or 0)
        event = _slot_event(rules)
        if not rules.get("isAllowToPlay") or free <= 0:
            return {"ok": True, "spun": 0, "note": "no free games left today",
                    "event": event}
        n = free if max_spins is None else min(free, max_spins)
        if dry_run:
            return {"ok": True, "dry_run": True, "free_games": free,
                    "would_spin": n, "eta_seconds": int(n * (spin_delay + 1.5)),
                    "event": event}
        tally, jackpots, spun, ghosts = {}, 0, 0, 0
        expired = False
        net_error = None
        retries = 0                     # consecutive network failures
        i = 0
        while i < n:
            if i > 0:
                _time.sleep(spin_delay + random.uniform(0, 2.0))
            try:
                res = cl.slot_play()
            except AMAuthError:
                # The token rotates out from under long runs. Spins already
                # played are counted server-side, so stop and report the haul
                # instead of losing it to an exception.
                expired = True
                break
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                # TCP/TLS never completed: the spin never reached the server,
                # so no game was burned. Back off and retry the SAME spin.
                retries += 1
                if retries > 3:
                    net_error = f"{type(e).__name__}: {e}"
                    break
                _time.sleep(30 * retries + random.uniform(0, 5))
                continue
            except httpx.HTTPError as e:
                # e.g. ReadTimeout: the server may still have counted the
                # spin, so do NOT retry it (a real retry re-burns a game).
                # Count it as a ghost and move on; bail only on a persistent
                # outage so the caller can retry the remainder later.
                spun += 1
                ghosts += 1
                i += 1
                retries += 1
                if retries > 5:
                    net_error = f"{type(e).__name__}: {e}"
                    break
                continue
            retries = 0
            spun += 1
            i += 1
            if not res:
                ghosts += 1
                continue
            g = res.get("gain", {})
            gt = _CURRENCY_LABEL.get(g.get("gainType"), g.get("gainType", "?"))
            tally[gt] = tally.get(gt, 0) + int(g.get("gainAmount", 0) or 0)
            if g.get("isJackpot"):
                jackpots += 1
            if not res.get("isAllowToPlay"):
                break
        out = {"ok": True, "spun": spun, "unread": ghosts,
               "winnings": tally, "jackpots": jackpots}
        if net_error:
            # Partial haul: report what was spun, flag the rest as retriable.
            out["ok"] = False
            out["error"] = net_error
            out["remaining_games_hint"] = ("Re-run to spin the rest of "
                                           "today's free games.")
        if expired:
            out["auth_expired"] = True
            out["hint"] = ("Token expired mid-run — refresh the mobile session "
                           "and re-run to spin the rest of today's games.")
        if event:
            try:  # authoritative post-run count, if the session still works
                out["event"] = _slot_event(cl.slot_rules()) or event
            except AMError:
                event["spins_done"] += spun
                event["spins_to_go"] = max(0, event["spins_for_gift"]
                                           - event["spins_done"])
                out["event"] = event
        return out
    return _mobile_call(run)


@mcp.tool()
def mobile_catalog() -> dict:
    """Reference data collected from the mobile API so far (models/skins/fleet)."""
    def run(cl):
        return {"ok": True, **cl.store.counts()}
    return _mobile_call(run)


# ── Entry Point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
