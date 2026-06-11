#!/usr/bin/env python3
"""
Scrape line_ids from the game's /network/planning page and store them in the DB.

For each hub in player_hubs, navigates to the planning page, selects the hub,
reads all owned routes with their lineIds, and upserts into the routes table.

Usage:
    python3 scrape_line_ids.py
    python3 scrape_line_ids.py --hub MPM
    python3 scrape_line_ids.py --dry-run

Requires Chrome with --remote-debugging-port=9222 and a logged-in AM tab.
"""

import argparse, os, sqlite3, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import CDP, get_am_tab, BASE_URL
from db import DB, load_player_hubs
from circuit_scheduler import navigate_to_planning, select_hub, get_lines_at_hub



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

    print(f"Connecting to Chrome...")
    cdp = CDP(get_am_tab()["webSocketDebuggerUrl"], timeout=120)
    cdp.connect()
    try:
        navigate_to_planning(cdp)

        total_matched = 0
        total_stored = 0

        for hub_iata, hub_id in hubs:
            print(f"\n{'='*60}")
            print(f"Hub: {hub_iata} (id={hub_id})")
            print(f"{'='*60}")

            if not select_hub(cdp, hub_iata):
                print(f"  SKIP: could not select hub")
                continue

            lines = get_lines_at_hub(cdp)
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
        db.close()
        cdp.close()


if __name__ == "__main__":
    main()
