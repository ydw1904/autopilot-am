#!/usr/bin/env python3
"""
Scrape line_ids from the game's /marketing/internalaudit/linelist page
and store them in the DB.

For each hub in player_hubs, fetches the audit linelist, parses line_ids
and dest IATAs from the HTML table, and upserts into the routes table.

Usage:
    python3 scrape_audit_line_ids.py
    python3 scrape_audit_line_ids.py --hub MPM
    python3 scrape_audit_line_ids.py --dry-run

Requires Chrome with --remote-debugging-port=9222 and a logged-in AM tab.
"""

import argparse, json, os, re, sqlite3, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import CDP, get_am_tab, BASE_URL
from db import DB, load_player_hubs

AUDIT_URL = f"{BASE_URL}/marketing/internalaudit/linelist"

TR_RE = re.compile(r'<tr\s+id="(\d+)"', re.DOTALL)
IATA_RE = re.compile(r'([A-Z]{3})/([A-Z]{3})\s*-\s*')
AIRPORT_OPTION_RE = re.compile(
    r'airport=(\d+)&amp;page=1[^>]*>\s*([A-Z]{3})\s*-\s*[^<]+\((\d+)\)'
)


def resolve_airport_id(cdp, hub_iata):
    html = cdp.eval(
        f"""
        (async () => {{
            const r = await fetch('{AUDIT_URL}', {{credentials:'include'}});
            return await r.text();
        }})()
        """,
        await_promise=True,
    )
    for m in AIRPORT_OPTION_RE.finditer(html or ""):
        aid, iata, count = m.group(1), m.group(2), m.group(3)
        if iata == hub_iata:
            return int(aid), int(count)
    return None, 0


def scrape_audit_page(cdp, airport_id, page=1):
    url = f"{AUDIT_URL}?airport={airport_id}&page={page}"
    html = cdp.eval(
        f"""
        (async () => {{
            const r = await fetch('{url}', {{credentials:'include'}});
            return await r.text();
        }})()
        """,
        await_promise=True,
    )
    if not html:
        return [], False

    routes = []
    tr_matches = list(TR_RE.finditer(html))
    for i, tr_m in enumerate(tr_matches):
        line_id = int(tr_m.group(1))
        start = tr_m.end()
        end = tr_matches[i + 1].start() if i + 1 < len(tr_matches) else start + 2000
        chunk = html[start:end]

        iata_m = IATA_RE.search(chunk)
        if not iata_m:
            continue
        routes.append({
            "line_id": line_id,
            "hub": iata_m.group(1),
            "dest": iata_m.group(2),
        })

    has_next = len(routes) >= 20
    return routes, has_next


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--hub", help="Only scrape this hub (default: all)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    db = sqlite3.connect(DB)
    hubs = load_player_hubs(db, args.hub)
    if not hubs:
        print("No hubs found in player_hubs table")
        db.close()
        return

    print("Connecting to Chrome...")
    cdp = CDP(get_am_tab()["webSocketDebuggerUrl"], timeout=60)
    cdp.connect()
    try:
        total_new = 0
        total_updated = 0

        for hub_iata, hub_id in hubs:
            print(f"\n{'='*60}")
            print(f"Hub: {hub_iata}")
            print(f"{'='*60}")

            airport_id, route_count = resolve_airport_id(cdp, hub_iata)
            if not airport_id:
                print(f"  SKIP: could not resolve airport id")
                continue
            print(f"  Airport id: {airport_id}, expected routes: {route_count}")

            page = 1
            hub_total = 0
            while True:
                routes, has_next = scrape_audit_page(cdp, airport_id, page)
                if not routes:
                    break

                for r in routes:
                    lid = r["line_id"]
                    dest = r["dest"]
                    hub_total += 1

                    row = db.execute(
                        "SELECT line_id FROM routes WHERE hub_iata = ? AND dest_iata = ?",
                        (hub_iata, dest)
                    ).fetchone()

                    if row:
                        old_lid = row[0]
                        if old_lid != lid:
                            if not args.dry_run:
                                db.execute(
                                    "UPDATE routes SET line_id = ?, is_owned = 1 "
                                    "WHERE hub_iata = ? AND dest_iata = ?",
                                    (lid, hub_iata, dest)
                                )
                            total_updated += 1
                            print(f"  {dest}: line_id {old_lid} → {lid}")
                        else:
                            if not args.dry_run:
                                db.execute(
                                    "UPDATE routes SET is_owned = 1 "
                                    "WHERE hub_iata = ? AND dest_iata = ? AND (is_owned IS NULL OR is_owned = 0)",
                                    (hub_iata, dest)
                                )
                    else:
                        total_new += 1
                        print(f"  {dest}: NEW route line_id={lid} (not in DB)")

                if not has_next:
                    break
                page += 1

            if not args.dry_run:
                db.commit()
            print(f"  Total: {hub_total} routes from audit page")

        print(f"\n{'='*60}")
        print(f"Summary: {total_new} new routes, {total_updated} updated")
        if args.dry_run:
            print("[dry-run] no changes made")
    finally:
        db.close()
        cdp.close()


if __name__ == "__main__":
    main()
