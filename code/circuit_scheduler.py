#!/usr/bin/env python3
"""
Circuit Scheduler - Schedule flights for aircraft in circuits at a given hub.

Reads circuit config from DB, navigates to the planning page via Chrome CDP,
resolves game IDs (aircraft + routes), then submits flight schedules using
the planning AJAX API.

Offset rotation pattern:
  Each aircraft in the circuit flies ALL routes, but offset by (index - 1) days.
  Aircraft 01 flies route A on Monday 00:00, Aircraft 02 flies route A on
  Tuesday 00:00, etc.  Routes are scheduled sequentially within each day,
  accounting for round-trip flight times.

Usage:
    python3 circuit_scheduler.py --hub HKG
    python3 circuit_scheduler.py --hub HKG --circuit HKG-C001
    python3 circuit_scheduler.py --hub HKG --dry-run
    python3 circuit_scheduler.py --list

Requirements: Chrome running with --remote-debugging-port=9222 --remote-allow-origins=*
              httpx, websocket-client, colorama pip packages
"""

import argparse, json, math, re, sqlite3, sys, time
from urllib.parse import quote

from colorama import init, Fore, Style

from cdp import CDP, get_am_tab, connect_cdp, BASE_URL  # noqa: F401
from db import DB

init(autoreset=True)

PLANNING_API = f"{BASE_URL}/network/planning/0/ajax"
WEEK_SECONDS = 7 * 86400   # 604800
GRANULARITY = 900           # 15 minutes in seconds
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _get_line_ids_from_db(hub_iata, dest_iatas, db):
    """Try to load line_ids from DB. Returns {dest_iata: line_id} or None."""
    placeholders = ",".join("?" * len(dest_iatas))
    rows = db.execute(
        f"SELECT dest_iata, line_id FROM routes "
        f"WHERE hub_iata=? AND dest_iata IN ({placeholders}) AND line_id IS NOT NULL",
        (hub_iata.upper(), *dest_iatas)
    ).fetchall()
    if rows and len(rows) == len(dest_iatas):
        return {r[0].upper(): r[1] for r in rows}
    return None


def _write_line_ids_to_db(hub_iata, hub_lines, db):
    """Write scraped line_ids back to DB."""
    for line in hub_lines:
        dest = (line.get("dest") or "").upper()
        lid = line.get("lineId")
        if dest and lid:
            db.execute(
                "UPDATE routes SET line_id = ?, is_owned = 1 "
                "WHERE hub_iata = ? AND dest_iata = ?",
                (lid, hub_iata.upper(), dest)
            )
    db.commit()


# ── Planning page helpers ─────────────────────────────────────────────────

def navigate_to_planning(cdp):
    """Navigate to /network/planning and wait for the page to fully load."""
    print(f"{Fore.CYAN}Navigating to planning page...")
    cdp.navigate(f"{BASE_URL}/network/planning")
    cdp.wait(4)

    # Wait for the aircraft list to populate
    for attempt in range(15):
        count = cdp.eval(
            "document.querySelectorAll('#aircraftList .aircraftListMiniBox').length || 0"
        )
        if count and count > 0:
            print(f"  Planning page loaded ({count} aircraft visible)")
            return True
        print(f"  Waiting for page load... ({attempt + 1}/15)")
        cdp.wait(1)

    print(f"  {Fore.YELLOW}Page loaded but no aircraft list found")
    return False


def select_hub(cdp, hub_iata):
    """Click the .planninghubBtn whose text starts with '<IATA> /'."""
    print(f"{Fore.CYAN}Selecting hub {hub_iata}...")
    result = cdp.eval_json(f"""(() => {{
        const btns = document.querySelectorAll('.planninghubBtn');
        for (const btn of btns) {{
            const txt = btn.textContent.trim();
            if (txt.startsWith('{hub_iata} /') || txt.startsWith('{hub_iata}/')) {{
                btn.click();
                return {{found: true, id: btn.id, text: txt.replace(/\\s+/g, ' ').slice(0, 60)}};
            }}
        }}
        return {{found: false, count: btns.length}};
    }})()""")
    if result and result.get("found"):
        print(f"  Hub selected: {result.get('text')} ({result.get('id')})")
        cdp.wait(3)  # AJAX reloads aircraft + lines
        return True
    print(f"  {Fore.RED}Could not find hub {hub_iata} (saw {result.get('count', 0)} hub buttons)")
    return False


def get_aircraft_at_hub(cdp):
    """
    Extract all aircraft at the currently selected hub from the planning page.

    Returns list of dicts: [{id: int, name: str, model: str}, ...]
    The `id` is the game's aircraftId (from aircraftId_XXXXXXX).
    """
    data = cdp.eval_json("""(() => {
        const result = [];
        const boxes = document.querySelectorAll('#aircraftList .aircraftListMiniBox');
        for (const box of boxes) {
            const idMatch = box.id && box.id.match(/aircraftId_(\\d+)/);
            if (!idMatch) continue;
            const id = parseInt(idMatch[1]);
            const boldEl = box.querySelector('.title .bold');
            const raw = boldEl ? boldEl.textContent.trim() : '';
            const model = raw.split('/')[0].trim();
            // Utilization: "<N>%" in .content .listBox1 > b. 0% = empty schedule.
            const utilEl = box.querySelector('.content .listBox1 > b');
            const utilStr = utilEl ? utilEl.textContent.trim().replace('%','') : '0';
            const util = parseFloat(utilStr) || 0;
            result.push({id, name: raw, model, util});
        }
        return result;
    })()""")
    return data or []


def get_lines_at_hub(cdp):
    """
    Extract all lines (routes) at the currently selected hub.

    Returns list of dicts: [{lineId: int, name: str, dest: str}, ...]
    """
    data = cdp.eval_json("""(() => {
        const result = [];
        const items = document.querySelectorAll('#lineList .lineList');
        for (const item of items) {
            const idEl = item.querySelector('.lineId');
            if (!idEl) continue;
            const lineId = parseInt(idEl.textContent.trim());
            if (!lineId || isNaN(lineId)) continue;
            // Strip the hidden lineId text from the display name.
            // Remaining text looks like "HND / KEF - 21h30".
            const fullText = item.textContent.trim();
            const display = fullText.replace(idEl.textContent.trim(), '').trim();
            // Dest IATA = the second 3-letter code (first is hub).
            const codes = display.match(/[A-Z]{3}/g) || [];
            const dest = codes.length >= 2 ? codes[1] : (codes[0] || '');
            result.push({lineId, name: display, dest});
        }
        return result;
    })()""")
    return data or []


# ── Schedule API helpers ──────────────────────────────────────────────────

def planning_api_call(cdp, payload):
    """
    POST to the planning AJAX API using the browser's fetch (auto-includes
    session cookies).

    payload: dict — e.g. {"aircraftId": 123} to clear, or
             {"aircraftId": 123, "added": [...]} to add flights.

    Returns the parsed JSON response, or None on failure.
    """
    encoded = quote(json.dumps(payload))
    js = (
        f"fetch('{PLANNING_API}', {{"
        f"  method: 'POST',"
        f"  headers: {{"
        f"    'Content-Type': 'application/x-www-form-urlencoded',"
        f"    'X-Requested-With': 'XMLHttpRequest'"
        f"  }},"
        f"  body: 'planningData={encoded}'"
        f"}}).then(r => r.json())"
    )
    return cdp.eval_json(js, await_promise=True)


def clear_schedule(cdp, aircraft_id):
    """Clear all flights for an aircraft."""
    return planning_api_call(cdp, {"aircraftId": aircraft_id})


def submit_flights(cdp, aircraft_id, flights):
    """Submit (add) flights for an aircraft."""
    if not flights:
        return {"result": True, "message": "No flights to add"}
    return planning_api_call(cdp, {"aircraftId": aircraft_id, "added": flights})


# ── Schedule builder ──────────────────────────────────────────────────────

def build_flight_schedule(routes, aircraft_index):
    """
    Build the list of flights for one aircraft in a circuit.

    Args:
        routes: list of (dest_iata, flight_time_rt_hours, game_line_id)
        aircraft_index: 0-based index determining the day offset

    Returns:
        list of {"takeOffTime": int, "lineId": int}
    """
    flights = []
    current_time = (aircraft_index % 7) * 86400  # day offset in seconds, wraps Mon..Sun
    total_rt = 0  # cumulative round-trip seconds — guard against >168h circuits

    for dest_iata, ft_hours, line_id in routes:
        if line_id is None:
            print(f"    {Fore.YELLOW}WARNING: No game lineId for {dest_iata}, skipping")
            continue

        # Snap to 15-minute granularity (round up), then wrap into [0, week).
        takeoff = current_time
        if takeoff % GRANULARITY != 0:
            takeoff = math.ceil(takeoff / GRANULARITY) * GRANULARITY
        takeoff %= WEEK_SECONDS

        flights.append({"takeOffTime": takeoff, "lineId": line_id})

        # Advance current_time by round-trip duration, rounded up to 15 min.
        # The schedule is weekly-recurring — takeOffTime wraps modulo WEEK_SECONDS,
        # so a Sunday-start plane simply continues into the next Monday slot.
        rt_seconds = math.ceil(ft_hours * 3600 / GRANULARITY) * GRANULARITY
        total_rt += rt_seconds
        if total_rt >= WEEK_SECONDS:
            print(f"    {Fore.YELLOW}  cumulative {total_rt}s ≥ 1 week — circuit "
                  f"too long, stopping after {len(flights)} flights")
            break
        current_time = takeoff + rt_seconds

    return flights


def fmt_time(seconds):
    """Format seconds-since-Monday as 'Day HH:MM'."""
    day = min(int(seconds // 86400), 6)
    secs = seconds % 86400
    hour = int(secs // 3600)
    minute = int((secs % 3600) // 60)
    return f"{DAY_NAMES[day]} {hour:02d}:{minute:02d}"


# ── DB helpers ────────────────────────────────────────────────────────────

def load_circuits(db, hub_iata, circuit_name=None):
    """
    Load circuits from the database.

    Returns list of dicts with keys: name, hub, model, total_hours, waves,
    routes (list of tuples), eco, bus, fir, cargo.
    """
    if circuit_name:
        circuit_name = circuit_name.upper()
        rows = db.execute(
            "SELECT name, hub_iata, aircraft_model, total_hours, waves, "
            "eco_seats, bus_seats, fir_seats, cargo_seats "
            "FROM circuits WHERE name=? AND hub_iata=?",
            (circuit_name, hub_iata),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT name, hub_iata, aircraft_model, total_hours, waves, "
            "eco_seats, bus_seats, fir_seats, cargo_seats "
            "FROM circuits WHERE hub_iata=? AND status IN ('planned','bought') "
            "ORDER BY name",
            (hub_iata,),
        ).fetchall()

    circuits = []
    for row in rows:
        routes = db.execute(
            "SELECT dest_iata, dest_name, distance_km, flight_time_rt, route_order "
            "FROM circuit_routes WHERE circuit_name=? ORDER BY route_order",
            (row[0],),
        ).fetchall()
        circuits.append({
            "name": row[0],
            "hub": row[1],
            "model": row[2],
            "total_hours": row[3],
            "waves": row[4] or math.ceil(row[3] / 168),
            "eco": row[5],
            "bus": row[6],
            "fir": row[7],
            "cargo": row[8],
            "routes": routes,
        })
    return circuits


def match_aircraft_by_name(hub_aircraft, circuit_name, max_waves=None):
    """Find aircraft whose set-name matches "<circuit_name>-<MMM>".

    Returns (matched, error). `matched` is a list of (mmm, aircraft_id) sorted
    by mmm, capped at ``max_waves * 7`` if ``max_waves`` is given.
    `error` is None on success, or a human-readable string if no matches.
    """
    pat = re.compile(rf"^{re.escape(circuit_name)}-(\d{{3}})$")
    matched = []
    for box in hub_aircraft:
        raw = box.get("name", "")
        parts = raw.split("/", 1)
        sn = parts[1].strip() if len(parts) == 2 else raw.strip()
        m = pat.match(sn)
        if m:
            matched.append((int(m.group(1)), box["id"]))
    matched.sort(key=lambda t: t[0])

    if not matched:
        return matched, (f"No aircraft named {circuit_name}-NNN at this hub. "
                         f"Buy + number them first (run aircraft_numberer.py).")

    # Cap to the circuit's planned wave count so excess prefix-matching aircraft
    # (e.g. 100 numbered ac for a 7-wave / 49-ac circuit) are NOT scheduled.
    if max_waves is not None and max_waves > 0:
        limit = max_waves * 7
        if len(matched) > limit:
            extra = matched[limit:]
            matched = matched[:limit]
            extra_labels = ", ".join(f"-{m:03d}" for m, _ in extra[:8])
            more = f" (+{len(extra) - 8} more)" if len(extra) > 8 else ""
            print(f"  {Fore.YELLOW}NOTE: {circuit_name} has {len(extra)} aircraft "
                  f"beyond planned {max_waves} waves ({extra_labels}{more}) — "
                  f"left unscheduled.")

    rem = len(matched) % 7
    if rem:
        # Drop the leftovers — only schedule complete waves of 7.
        leftovers = matched[-rem:]
        matched = matched[:-rem]
        leftover_labels = ", ".join(f"-{m:03d}" for m, _ in leftovers)
        print(f"  {Fore.YELLOW}NOTE: {circuit_name} has {rem} leftover aircraft "
              f"({leftover_labels}) — not enough for a full wave; left unscheduled.")
    return matched, None


def match_aircraft_to_circuits(hub_aircraft, circuits):
    """
    Assign game aircraft IDs to circuits based on model matching.

    Returns dict: circuit_name -> list of game aircraft IDs.
    """
    # Index available aircraft by normalised model name
    available = {}  # normalised_model -> [aircraft_id, ...]
    for ac in hub_aircraft:
        model_raw = ac.get("model", "") or ac.get("name", "")
        if not model_raw:
            continue
        # Normalise: uppercase, strip spaces, remove dashes
        norm = re.sub(r"[\s\-]+", "", model_raw).upper()
        if norm not in available:
            available[norm] = []
        available[norm].append(ac["id"])

    assignments = {}
    for circuit in circuits:
        model = circuit["model"].upper()
        norm_model = re.sub(r"[\s\-]+", "", model)
        needed = circuit["waves"] * 7

        # Find best matching pool
        matched_ids = []
        for ac_norm, ac_ids in available.items():
            if norm_model in ac_norm or ac_norm in norm_model:
                matched_ids = ac_ids
                break

        # Fallback: looser substring match on individual tokens
        if not matched_ids:
            tokens = [t for t in re.split(r"[\s\-]+", norm_model) if t]
            for ac_norm, ac_ids in available.items():
                ac_tokens = [t for t in re.split(r"[\s\-]+", ac_norm) if t]
                if all(any(tok in at for at in ac_tokens) for tok in tokens):
                    matched_ids = ac_ids
                    break

        assigned = matched_ids[:needed]
        if len(assigned) < needed:
            print(f"  {Fore.YELLOW}WARNING: {circuit['name']} needs {needed}x "
                  f"{model}, found {len(assigned)}")

        assignments[circuit["name"]] = assigned

        # Remove assigned aircraft from the pool so they aren't double-booked
        for aid in assigned:
            for ac_norm, ac_ids in available.items():
                if aid in ac_ids:
                    ac_ids.remove(aid)
                    if not ac_ids:
                        del available[ac_norm]
                    break

    return assignments


def match_routes_to_lines(hub_lines, circuits):
    """
    Match each circuit route's dest_iata to a game lineId.

    Returns dict: (circuit_name, dest_iata) -> game lineId (int or None).
    """
    # Build index of dest IATA -> lineId. The scraper already isolates the
    # destination code from "HUB / DEST - 21h00" into line["dest"].
    line_index = {}
    for line in hub_lines:
        dest = (line.get("dest") or "").upper()
        if dest and dest not in line_index:
            line_index[dest] = line["lineId"]

    mapping = {}
    for circuit in circuits:
        for route in circuit["routes"]:
            dest_iata = route[0].upper()
            line_id = line_index.get(dest_iata)
            mapping[(circuit["name"], dest_iata)] = line_id
            if line_id is None:
                print(f"  {Fore.YELLOW}WARNING: No game lineId for route to {dest_iata} "
                      f"(circuit {circuit['name']})")

    return mapping


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Circuit Scheduler - schedule flights for circuits at a hub"
    )
    p.add_argument("--hub", required=False, help="Hub IATA code (e.g. HKG)")
    p.add_argument("--circuit", help="Specific circuit name (default: all bought)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print schedule without submitting to game")
    p.add_argument("--list", action="store_true",
                   help="List bought circuits and exit")
    p.add_argument("--only-new", action="store_true",
                   help="Skip aircraft that already have flights scheduled "
                        "(preserves in-progress weeks).")
    args = p.parse_args()

    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row

    # ── --list mode ─────────────────────────────────────────────────────
    if args.list:
        rows = db.execute(
            "SELECT name, hub_iata, aircraft_model, total_hours, waves, status "
            "FROM circuits ORDER BY hub_iata, name"
        ).fetchall()
        if not rows:
            print("No circuits found in database.")
            return

        # Group by hub
        by_hub = {}
        for r in rows:
            hub = r["hub_iata"]
            by_hub.setdefault(hub, []).append(r)

        for hub in sorted(by_hub):
            print(f"\n{Fore.CYAN}Hub: {hub}")
            print(f"  {'Circuit':<14} {'Model':<14} {'Hours':>6} {'Waves':>5} {'Status'}")
            print(f"  {'-'*56}")
            for c in by_hub[hub]:
                status = c["status"]
                marker = f"{Fore.GREEN}{status}{Style.RESET_ALL}" if status == "bought" else status
                print(f"  {c['name']:<14} {c['aircraft_model']:<14} {c['total_hours']:>6.1f} "
                      f"{c['waves'] or '-':>5} {marker}")
        return

    if not args.hub:
        p.error("--hub is required (or use --list)")
        return

    hub_iata = args.hub.upper()

    # ── Load circuits ───────────────────────────────────────────────────
    circuits = load_circuits(db, hub_iata, args.circuit)
    if not circuits:
        print(f"{Fore.RED}No bought circuits found for hub {hub_iata}", file=sys.stderr)
        if args.circuit:
            print(f"  (looking for: {args.circuit})", file=sys.stderr)
        db.close()
        sys.exit(1)

    # ── Summary ─────────────────────────────────────────────────────────
    total_ac = sum(c["waves"] for c in circuits)
    print(f"\n{'=' * 62}")
    print(f"  Hub: {hub_iata}  |  Circuits: {len(circuits)}  |  Aircraft: {total_ac}")
    print(f"{'=' * 62}")
    for c in circuits:
        print(f"  {Fore.GREEN}{c['name']}{Style.RESET_ALL}: "
              f"{c['model']}, {len(c['routes'])} routes, "
              f"{c['waves']} waves, {c['total_hours']:.1f}h total")
        for r in c["routes"]:
            print(f"    {r[0]:<5} {str(r[1])[:30]:<30} {r[2]:>7}km  "
                  f"rt={r[3]:.2f}h")
    print(f"{'=' * 62}\n")

    # ── Dry run: just print the schedule and exit ───────────────────────
    if args.dry_run:
        for c in circuits:
            total_ac = c["waves"] * 7
            print(f"{Fore.CYAN}{c['name']} ({c['model']}) — {total_ac} aircraft "
                  f"({c['waves']} wave{'s' if c['waves'] != 1 else ''} × 7):")
            for ac_idx in range(total_ac):
                day = ac_idx % 7
                wave = ac_idx // 7 + 1
                print(f"  {Fore.WHITE}Aircraft {ac_idx + 1}/{total_ac} "
                      f"(wave {wave}/{c['waves']}, offset: {DAY_NAMES[day]}):")
                current = day * 86400
                for r in c["routes"]:
                    dest_iata = r[0]
                    ft_rt = r[3]
                    takeoff = current
                    if takeoff % GRANULARITY != 0:
                        takeoff = math.ceil(takeoff / GRANULARITY) * GRANULARITY
                    print(f"    {dest_iata:<5}  depart {fmt_time(takeoff)}"
                          f"  (rt={ft_rt:.2f}h)")
                    rt_sec = math.ceil(ft_rt * 3600 / GRANULARITY) * GRANULARITY
                    current = takeoff + rt_sec
                if current > WEEK_SECONDS:
                    print(f"    {Fore.YELLOW}WARNING: Schedule exceeds 1 week boundary")
            print()
        print(f"{Fore.YELLOW}DRY RUN - no changes made to the game.\n")
        db.close()
        return

    # ── Live scheduling ─────────────────────────────────────────────────
    print(f"{Fore.CYAN}Connecting to Chrome...")
    cdp = connect_cdp()

    # Step 1: Navigate to planning page
    if not navigate_to_planning(cdp):
        print(f"{Fore.YELLOW}Planning page may not have loaded fully, continuing...")

    # Step 2: Select hub
    if not select_hub(cdp, hub_iata):
        print(f"{Fore.RED}ERROR: Cannot select hub {hub_iata}", file=sys.stderr)
        cdp.close()
        db.close()
        sys.exit(1)

    # Step 3: Read aircraft at this hub
    hub_aircraft = get_aircraft_at_hub(cdp)
    if not hub_aircraft:
        print(f"{Fore.RED}ERROR: No aircraft found at hub {hub_iata} on planning page",
              file=sys.stderr)
        print(f"  Make sure the correct hub is selected.", file=sys.stderr)
        cdp.close()
        db.close()
        sys.exit(1)
    print(f"  {len(hub_aircraft)} aircraft at hub:")
    for ac in hub_aircraft:
        print(f"    ID {ac['id']}:  {ac.get('model', ac.get('name', '?'))}")

    # Step 4: Read lines (routes) at this hub — try DB first
    all_dests = set()
    for c in circuits:
        for r in c["routes"]:
            all_dests.add(r[0].upper())

    db_line_map = _get_line_ids_from_db(hub_iata, all_dests, db)
    if db_line_map and len(db_line_map) == len(all_dests):
        print(f"  Using {len(db_line_map)} line_ids from DB (skipping game scrape)")
        hub_lines = [{"lineId": lid, "dest": dest, "name": ""}
                     for dest, lid in db_line_map.items()]
    else:
        hub_lines = get_lines_at_hub(cdp)
        if not hub_lines:
            print(f"{Fore.RED}ERROR: No lines found at hub {hub_iata} on planning page",
                  file=sys.stderr)
            cdp.close()
            db.close()
            sys.exit(1)
        # Write scraped line_ids back to DB
        _write_line_ids_to_db(hub_iata, hub_lines, db)
    print(f"  {len(hub_lines)} lines at hub")
    # Show a few lines for debugging
    for line in hub_lines[:5]:
        print(f"    lineId {line['lineId']}:  {line.get('name', '?')[:60]}")
    if len(hub_lines) > 5:
        print(f"    ... and {len(hub_lines) - 5} more")

    # Step 5: Match aircraft to circuits — by canonical name <HUB>-C<NNN>-<MMM>
    print(f"\n{Fore.CYAN}Matching aircraft by name…")
    util_by_id = {ac["id"]: ac.get("util", 0) for ac in hub_aircraft}
    matched_per_circuit = {}
    for circuit in circuits:
        cname = circuit["name"]
        matched, err = match_aircraft_by_name(hub_aircraft, cname,
                                              max_waves=circuit.get("waves"))
        if err:
            print(f"  {Fore.RED}{cname}: {err}")
            matched_per_circuit[cname] = []
            continue
        print(f"  {cname}: {len(matched)} aircraft "
              f"(MMM {matched[0][0]:03d}–{matched[-1][0]:03d})")
        matched_per_circuit[cname] = matched

    # Step 6: Match routes to game line IDs
    print(f"{Fore.CYAN}Matching routes to game lines...")
    line_mapping = match_routes_to_lines(hub_lines, circuits)
    print()

    # Step 7: Build and submit schedules
    success_count = 0
    error_count = 0
    skipped_count = 0

    from db import update_circuit_progress

    for circuit in circuits:
        cname = circuit["name"]
        matched = matched_per_circuit.get(cname, [])

        print(f"{Fore.GREEN}{'─' * 58}")
        print(f"  Circuit: {cname}  ({circuit['model']})")
        print(f"{'─' * 58}")

        if not matched:
            print(f"  {Fore.RED}No matching aircraft — skipping")
            error_count += 1
            continue

        routes_with_ids = []
        for r in circuit["routes"]:
            dest_iata = r[0]
            ft_rt = r[3]
            line_id = line_mapping.get((cname, dest_iata))
            routes_with_ids.append((dest_iata, ft_rt, line_id))
        missing = [d for d, _, lid in routes_with_ids if lid is None]
        if missing:
            print(f"  {Fore.YELLOW}WARNING: {len(missing)} routes missing lineId: "
                  f"{', '.join(missing)}")

        preserved = 0   # aircraft we left alone because they were already flying
        successes = 0   # aircraft we successfully scheduled this run

        for mmm, ac_id in matched:
            day = (mmm - 1) % 7
            wave = (mmm - 1) // 7 + 1
            label = f"{cname}-{mmm:03d}"
            already_flying = util_by_id.get(ac_id, 0) > 0
            print(f"\n  {Fore.WHITE}{label} (wave {wave}, {DAY_NAMES[day]}) "
                  f"util={util_by_id.get(ac_id, 0):.0f}%")

            if args.only_new and already_flying:
                print(f"    {Fore.CYAN}--only-new: skipping (already scheduled)")
                preserved += 1
                continue

            flights = build_flight_schedule(routes_with_ids, mmm - 1)
            if not flights:
                print(f"    {Fore.YELLOW}No flights generated — skipping")
                skipped_count += 1
                continue

            print(f"    Schedule ({len(flights)} flights):")
            for f in flights:
                print(f"      {fmt_time(f['takeOffTime']):>12}  ->  line {f['lineId']}")

            clear_result = clear_schedule(cdp, ac_id)
            if clear_result and clear_result.get("result"):
                print(f"    {Fore.GREEN}Cleared")
            elif clear_result:
                print(f"    {Fore.YELLOW}Clear: {clear_result.get('message', clear_result)}")
            cdp.wait(0.3)

            result = submit_flights(cdp, ac_id, flights)
            if result and result.get("result"):
                print(f"    {Fore.GREEN}SUCCESS")
                successes += 1
                success_count += 1
            else:
                msg = result.get("message", str(result)) if result else "No response"
                print(f"    {Fore.RED}FAILED: {msg}")
                error_count += 1
            cdp.wait(0.5)

        # Update DB progress: total scheduled = preserved + new successes
        scheduled_total = preserved + successes
        waves_scheduled = scheduled_total // 7
        update_circuit_progress(cname, waves_scheduled=waves_scheduled)
        print(f"\n  {Fore.CYAN}{cname}: scheduled_total={scheduled_total} "
              f"→ waves_scheduled={waves_scheduled}")

    # ── Final summary ───────────────────────────────────────────────────
    print(f"\n{'=' * 62}")
    print(f"  Scheduling complete")
    print(f"  {Fore.GREEN}Success: {success_count}{Style.RESET_ALL}  "
          f"{Fore.RED}Errors: {error_count}{Style.RESET_ALL}  "
          f"{Fore.YELLOW}Skipped: {skipped_count}{Style.RESET_ALL}")
    print(f"{'=' * 62}\n")

    cdp.close()
    db.close()


if __name__ == "__main__":
    main()
