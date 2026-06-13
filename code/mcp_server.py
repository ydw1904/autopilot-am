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

import json
import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import asdict
from typing import List, Optional, Tuple

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import CDP, get_am_tab, BASE_URL  # noqa: E402
from db import DB as DB_PATH, get_player_hub_id, mark_route_owned  # noqa: E402
from circuit_scheduler import get_lines_at_hub  # noqa: E402
from aircraft_buyer import get_balance as read_balance  # noqa: E402
from aircraft_aliases import (  # noqa: E402
    resolve as resolve_aircraft_name,
    catalog as aircraft_catalog_data,
)
from circuit_route_buyer import (  # noqa: E402
    wait_for_listing, find_country_card, finalize_purchase,
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

    if parse_json and proc.returncode == 0:
        try:
            result["data"] = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            result["json_parse_error"] = "stdout was not valid JSON"

    return result


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


def _get_cdp():
    """Get or create the CDP connection. Finds the AM tab automatically."""
    global _cdp

    if _cdp:
        # Check if WebSocket is still alive with a ping
        try:
            _cdp.eval("1")
            return _cdp
        except Exception:
            _cdp = None

    am_tab = get_am_tab()
    if not am_tab:
        return None

    _cdp = CDP(am_tab["webSocketDebuggerUrl"])
    _cdp.connect()
    return _cdp


def _wait_for_js(cdp, expression: str, timeout: float = 15.0, interval: float = 0.5):
    """Poll a JS expression until it returns a truthy non-error value."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = cdp.eval(expression)
        if last and not isinstance(last, dict):
            return last
        time.sleep(interval)
    return last


def _select_planning_hub(cdp, hub_iata: str) -> bool:
    """Select a hub on /network/planning by its IATA code."""
    hub_iata = hub_iata.upper().strip()
    result = cdp.eval_json(f"""((() => {{
        const btns = document.querySelectorAll('.planninghubBtn');
        for (const btn of btns) {{
            const txt = (btn.textContent || '').trim();
            if (txt.startsWith('{hub_iata} /') || txt.startsWith('{hub_iata}/')) {{
                btn.click();
                return {{found: true, id: btn.id, text: txt}};
            }}
        }}
        return {{found: false, count: btns.length}};
    }})())""")
    if not result or not result.get("found"):
        return False

    loaded = _wait_for_js(
        cdp,
        """((() => {
            const hasAircraft = document.querySelectorAll('#aircraftList .aircraftListMiniBox').length;
            const hasLines = document.querySelectorAll('#lineList .lineList').length;
            return hasAircraft || hasLines || false;
        })())""",
        timeout=15.0,
        interval=0.5,
    )
    return bool(loaded)


def _get_lines_at_selected_hub(cdp):
    """Owned lines at the selected planning hub, remapped to MCP output keys."""
    return [
        {"line_id": str(l["lineId"]), "raw": l.get("name") or "",
         "dest_iata": l.get("dest") or "?"}
        for l in get_lines_at_hub(cdp)
    ]


def _get_aircraft_at_selected_hub(cdp):
    """Return aircraft at the currently selected planning hub."""
    aircraft = cdp.eval_json("""((() => {
        return Array.from(document.querySelectorAll('#aircraftList .aircraftListMiniBox')).map(el => {
            const utilEl = el.querySelector('.content .listBox1 > b');
            const utilStr = utilEl ? utilEl.textContent.trim().replace('%', '') : '0';
            return {
                aircraft_id: el.id.replace('aircraftId_', ''),
                model: el.querySelector('.title img')?.src?.split('/').pop()?.replace('.png', '') || '?',
                name: el.querySelector('.title .bold')?.textContent?.trim() || '',
                utilization_pct: parseFloat(utilStr) || 0
            };
        });
    })())""")
    return aircraft or []


# ── Route purchase helpers (ported from circuit_route_buyer.py) ─────────────
# Two hard-won lessons baked in here:
#   1. The game server silently rejects fetch()-based purchase POSTs (returns
#      200 but does NOT apply the purchase). Native form.submit() is required.
#   2. Some hubs/countries no longer expose /newlinefinalize directly without
#      first going through the country listing page, so we prefer that flow.

def _lookup_dest_country(hub_iata: str, dest_iata: str) -> Optional[str]:
    """Resolve a destination's country slug (lowercase) from the routes table."""
    if not os.path.exists(DB_PATH):
        return None
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT dest_country FROM routes "
            "WHERE UPPER(hub_iata)=? AND UPPER(dest_iata)=? LIMIT 1",
            (hub_iata.upper().strip(), dest_iata.upper().strip()),
        ).fetchone()
        return row[0].lower() if row and row[0] else None
    finally:
        conn.close()


def _mark_route_owned(hub_iata: str, dest_iata: str) -> None:
    """Best-effort: flag a route as owned in the local routes table."""
    try:
        mark_route_owned(hub_iata, dest_iata)
    except Exception:
        pass


# ── Tools ───────────────────────────────────────────────────────────────────


@mcp.tool()
def get_balance() -> dict:
    """Get the player's current dollar balance.

    Returns:
        dict with 'balance' (int, dollars), 'error' if failed.
    """
    cdp = _get_cdp()
    if not cdp:
        return {"error": "No Airlines Manager tab found in Chrome. Open AM and restart."}

    balance = read_balance(cdp)
    if balance is None:
        return {"error": "Could not read balance. Make sure you're on a game page."}
    return {"balance": balance}


@mcp.tool()
def list_hubs() -> dict:
    """List all hubs with their internal IDs and names.

    Returns:
        dict with 'hubs' (list of {name, hub_id, iata}), 'count'.
    """
    cdp = _get_cdp()
    if not cdp:
        return {"error": "No Airlines Manager tab found."}

    cdp.navigate(f"{BASE_URL}/network/newline")
    time.sleep(4)

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
def list_routes(hub_iata: str) -> dict:
    """List owned routes from a hub.

    Uses the planning page hub selector so the result actually reflects the
    requested hub instead of whichever hub happened to be selected already.

    Args:
        hub_iata: IATA code of the hub, e.g. 'HKG', 'MPM', 'FRA'.

    Returns:
        dict with 'routes' (list of {dest_iata, raw, line_id}), 'hub_iata', 'count'.
    """
    cdp = _get_cdp()
    if not cdp:
        return {"error": "No Airlines Manager tab found."}

    hub_iata = hub_iata.upper().strip()
    cdp.navigate(f"{BASE_URL}/network/planning")
    if not _wait_for_js(cdp, "document.querySelectorAll('.planninghubBtn').length", timeout=15.0):
        return {"error": "Planning page did not load hub selector.", "hub_iata": hub_iata, "routes": []}
    if not _select_planning_hub(cdp, hub_iata):
        return {"error": f"Could not select hub {hub_iata} on the planning page.", "hub_iata": hub_iata, "routes": []}

    routes = _get_lines_at_selected_hub(cdp)
    if not routes:
        return {"error": f"No routes found from {hub_iata}.", "hub_iata": hub_iata, "routes": []}

    return {"hub_iata": hub_iata, "routes": routes, "count": len(routes)}


@mcp.tool()
def buy_route(
    hub_iata: str,
    dest_iata: str,
    hub_id: Optional[str] = None,
    country: Optional[str] = None,
    dry_run: bool = False,
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
        cdp.navigate(f"{BASE_URL}/network/newline")
        time.sleep(4)
        hub_id = cdp.eval(f"""((() => {{
            const boxes = document.querySelectorAll('.hubListBox[data-hubid]');
            for (const b of boxes) {{
                const name = (b.querySelector('.title')?.textContent || '').trim().toUpperCase();
                if (name.startsWith('{hub_iata}')) return b.getAttribute('data-hubid');
            }}
            return null;
        }})())""")
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
            cdp.navigate(target)
            time.sleep(3)
            if not cdp.eval('!!document.getElementById("linePurchaseForm")'):
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
def get_aircraft_at_hub(hub_iata: str) -> dict:
    """List owned aircraft at a hub by navigating to the planning page.

    Args:
        hub_iata: Hub IATA code.

    Returns:
        dict with 'aircraft' (list of {aircraft_id, model, name, utilization_pct}),
        'hub_iata', 'count'.
    """
    cdp = _get_cdp()
    if not cdp:
        return {"error": "No Airlines Manager tab found."}

    hub_iata = hub_iata.upper().strip()
    cdp.navigate(f"{BASE_URL}/network/planning")
    if not _wait_for_js(cdp, "document.querySelectorAll('.planninghubBtn').length", timeout=15.0):
        return {"error": "Planning page did not load hub selector.", "hub_iata": hub_iata, "aircraft": []}
    if not _select_planning_hub(cdp, hub_iata):
        return {"error": f"Could not select hub {hub_iata} on the planning page.", "hub_iata": hub_iata, "aircraft": []}

    aircraft = _get_aircraft_at_selected_hub(cdp)
    return {"hub_iata": hub_iata, "aircraft": aircraft, "count": len(aircraft)}


@mcp.tool()
def schedule_flight(
    aircraft_id: str,
    flights: list,
    clear_first: bool = False,
) -> dict:
    """Schedule flights for an aircraft via the pure API endpoint.

    This is a POST to /network/planning/0/ajax -- no UI interaction needed.
    takeOffTime is in seconds from Monday 00:00, must be divisible by 900 (15min).
      Monday 00:00 = 0, Monday 06:00 = 21600, Tuesday 00:00 = 86400.

    Args:
        aircraft_id: Internal aircraft ID (string, from get_aircraft_at_hub).
        flights: List of dicts, each with 'lineId' (str/int) and 'takeOffTime' (int).
        clear_first: If True, wipe existing schedule before adding.

    Returns:
        dict with 'success', 'scheduled' count, or 'error'.
    """
    cdp = _get_cdp()
    if not cdp:
        return {"error": "No Airlines Manager tab found."}

    aircraft_id = str(aircraft_id)

    if clear_first:
        cdp.eval_json(f"""((() => {{
            return fetch('{BASE_URL}/network/planning/0/ajax', {{
                method: 'POST',
                headers: {{
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-Requested-With': 'XMLHttpRequest'
                }},
                body: 'planningData=' + encodeURIComponent(JSON.stringify({{"aircraftId": "{aircraft_id}"}})),
                credentials: 'include'
            }}).then(r => r.json());
        }})())""", await_promise=True)
        time.sleep(1)

    payload = {"aircraftId": aircraft_id, "added": flights}

    result = cdp.eval_json(
        "((() => {\n"
        f"  return fetch('{BASE_URL}/network/planning/0/ajax', {{\n"
        "    method: 'POST',\n"
        "    headers: {\n"
        "      'Content-Type': 'application/x-www-form-urlencoded',\n"
        "      'X-Requested-With': 'XMLHttpRequest'\n"
        "    },\n"
        f"    body: 'planningData=' + encodeURIComponent(JSON.stringify({json.dumps(payload)})),\n"
        "    credentials: 'include'\n"
        "  })\n"
        "  .then(r => r.json())\n"
        "  .catch(e => ({error: e.message}));\n"
        "})())",
        await_promise=True,
    )

    if not result:
        return {"error": "Scheduling request returned no response."}
    if result.get("result"):
        return {
            "success": True,
            "aircraft_id": aircraft_id,
            "scheduled": len(flights),
            "message": result.get("message", "Schedule updated."),
        }
    return {"success": False, "error": result}


@mcp.tool()
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
    cdp.navigate(url)
    time.sleep(3)

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

    result = _run_python_script("circuit_planner.py", args, timeout=600)
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
    dry_run: bool = True,
    list_only: bool = False,
) -> dict:
    """Run the aircraft buyer script.

    Safety default: dry_run=True because this spends in-game money.
    Use either circuit mode or standalone model+hub mode. The 'model' arg accepts
    any spelling (alias/ICAO/colloquial, e.g. 'A380') and is normalized before
    buying; an ambiguous name returns 'candidates' and an unknown one returns
    'suggestions' immediately, without spending money.
    """
    if list_only:
        return _run_python_script("aircraft_buyer.py", ["--list"], timeout=180)

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
    if dry_run:
        args.append("--dry-run")

    result = _run_python_script("aircraft_buyer.py", args, timeout=900)
    result.update({
        "circuit": circuit,
        "model": model,
        "hub": hub.upper().strip() if hub else None,
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
        return _run_python_script("circuit_scheduler.py", ["--list"], timeout=180)

    if not hub:
        return {"error": "hub='HKG'-style input is required unless list_only=True."}

    args = ["--hub", hub.upper().strip()]
    if circuit:
        args.extend(["--circuit", circuit])
    if dry_run:
        args.append("--dry-run")
    if only_new:
        args.append("--only-new")

    result = _run_python_script("circuit_scheduler.py", args, timeout=900)
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

    Safety default: dry_run=True because live pricing mutates the game.
    """
    if mode not in {"ideal", "percent", "raw-ideal"}:
        return {"error": "mode must be one of: ideal, percent, raw-ideal"}

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

    result = _run_python_script("auto_pricer.py", args, timeout=900)
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

    result = _run_python_script("aircraft_numberer.py", args, timeout=900)
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

    result = _run_python_script("aircraft_reconfigurator.py", args, timeout=900)
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

    result = _run_python_script("mass_renamer.py", args, timeout=900)
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


# ── Entry Point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
