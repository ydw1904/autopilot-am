#!/usr/bin/env python3
"""
Fetch line_ids for every owned route and store them in the DB.

For each hub in player_hubs, reads the owned routes with their lineIds from the
mobile API (hub/<id>/lines/pricing), and upserts into the routes table. Without
a mobile session (or with --cdp) it scrapes /network/planning in Chrome.

Usage:
    python3 scrape_line_ids.py
    python3 scrape_line_ids.py --hub MPM
    python3 scrape_line_ids.py --dry-run

    python3 scrape_line_ids.py --cdp
"""

import argparse, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import CDP, get_am_tab
from db import get_db, close_db, load_player_hubs
from planning_page import navigate_to_planning, select_hub, get_lines_at_hub
from circuit_scheduler import _mobile_client



def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--hub", help="Only scrape this hub (default: all)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--cdp", action="store_true", help="Scrape via Chrome instead of mobile")
    args = p.parse_args()

    db = get_db()
    hubs = load_player_hubs(db, args.hub)
    if not hubs:
        print("No hubs found in player_hubs table")
        close_db()
        return

    client = None if args.cdp else _mobile_client()
    if client:
        print("Backend: mobile API")
        backend = client
    else:
        print(f"Connecting to Chrome...")
        backend = CDP(get_am_tab()["webSocketDebuggerUrl"], timeout=120)
        backend.connect()
        navigate_to_planning(backend)
    try:

        total_matched = 0
        total_stored = 0

        for hub_iata, hub_id in hubs:
            print(f"\n{'='*60}")
            print(f"Hub: {hub_iata} (id={hub_id})")
            print(f"{'='*60}")

            if client:
                lines = [{"lineId": int(ln["id"]), "dest": ln.get("aTwoName") or ""}
                         for ln in client.hub_pricing(int(hub_id))]
            elif not select_hub(backend, hub_iata):
                print(f"  SKIP: could not select hub")
                continue
            else:
                lines = get_lines_at_hub(backend, hub_iata)
            print(f"  Found {len(lines)} owned routes")

            for line in lines:
                lid = line.get("lineId")
                dest = line.get("dest", "").upper()
                if not lid or not dest:
                    print(f"  SKIP: lineId={lid} dest={dest}")
                    continue
                total_matched += 1

                row = db.execute(
                    "SELECT dest_iata, line_id, is_owned FROM routes "
                    "WHERE hub_iata = ? AND dest_iata = ?",
                    (hub_iata, dest)
                ).fetchone()

                if row:
                    old_lid = row[1]
                    if args.dry_run:
                        action = "would update" if old_lid != lid else "unchanged"
                    else:
                        db.execute(
                            "UPDATE routes SET line_id = ?, is_owned = 1 "
                            "WHERE hub_iata = ? AND dest_iata = ?",
                            (lid, hub_iata, dest)
                        )
                        action = "updated" if old_lid != lid else "unchanged"
                    if old_lid != lid:
                        print(f"  {dest}: line_id {old_lid} → {lid} ({action})")
                    total_stored += 1
                else:
                    print(f"  {dest}: NOT IN DB (line_id={lid})")

            if not args.dry_run:
                db.commit()

        print(f"\n{'='*60}")
        print(f"Summary: {total_matched} routes matched, {total_stored} stored")
        if args.dry_run:
            print("[dry-run] no changes made")
    finally:
        close_db()
        backend.close()


if __name__ == "__main__":
    main()
