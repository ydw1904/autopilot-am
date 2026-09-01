#!/usr/bin/env python3
"""
Warehouse Sync CLI — scrape aircraft fleet from the game and upsert into DB.

Replicates the GUI warehouse "Sync Fleet" button as a standalone CLI tool.

Usage:
    python3 code/warehouse_sync.py                    # sync all hubs
    python3 code/warehouse_sync.py --hub MPM          # sync only MPM
    python3 code/warehouse_sync.py --summary          # just print DB summary

Requirements: Chrome running with --remote-debugging-port=9222 --remote-allow-origins=*
"""

import argparse, os, sys

from colorama import init, Fore, Style

init(autoreset=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import CDP, get_am_tab
from db import get_db, close_db, resolve_skin_ids, upsert_fleet
from planning_page import navigate_to_planning, select_hub, get_aircraft_at_hub



def print_summary(db):
    rows = db.execute(
        "SELECT hub_iata, COUNT(*) as cnt, "
        "SUM(CASE WHEN utilization = 0 THEN 1 ELSE 0 END) as idle "
        "FROM fleet GROUP BY hub_iata ORDER BY hub_iata"
    ).fetchall()
    print(f"\n{Fore.CYAN}Fleet Summary (from DB):")
    total = 0
    for r in rows:
        print(f"  {r['hub_iata']}: {r['cnt']} aircraft ({r['idle']} idle)")
        total += r["cnt"]
    print(f"  {Style.BRIGHT}Total: {total}")

    skins = db.execute(
        "SELECT COUNT(DISTINCT skin_id) AS liveries, "
        "SUM(CASE WHEN skin_id IS NULL THEN 1 ELSE 0 END) AS unknown FROM fleet"
    ).fetchone()
    if skins and skins["liveries"]:
        print(f"  {Fore.CYAN}Liveries: {skins['liveries']} distinct "
              f"({skins['unknown']} aircraft unidentified)")

    model_rows = db.execute(
        "SELECT hub_iata, model, COUNT(*) as cnt, "
        "SUM(CASE WHEN utilization = 0 THEN 1 ELSE 0 END) as idle "
        "FROM fleet GROUP BY hub_iata, model "
        "ORDER BY hub_iata, cnt DESC"
    ).fetchall()
    current_hub = None
    for r in model_rows:
        if r["hub_iata"] != current_hub:
            current_hub = r["hub_iata"]
            print(f"\n  {Fore.WHITE}{current_hub}:")
        print(f"    {r['model']:<25} {r['cnt']:>4}  ({r['idle']} idle)")


def main():
    p = argparse.ArgumentParser(description="Warehouse Sync — scrape fleet from game")
    p.add_argument("--hub", help="Only sync this hub (default: all)")
    p.add_argument("--summary", action="store_true", help="Print DB summary and exit")
    args = p.parse_args()

    db = get_db()

    if args.summary:
        print_summary(db)
        close_db()
        return

    tab = get_am_tab()
    if not tab:
        print(f"{Fore.RED}No AM tab found. Open Chrome with --remote-debugging-port=9222")
        sys.exit(1)

    cdp = CDP(tab["webSocketDebuggerUrl"])
    cdp.connect()
    try:
        print(f"{Fore.CYAN}Navigating to planning page...")
        if not navigate_to_planning(cdp):
            print(f"  {Fore.YELLOW}Page loaded but no aircraft list found")

        if args.hub:
            hubs = [args.hub.upper()]
        else:
            hubs = [r[0] for r in db.execute("SELECT hub_iata FROM player_hubs").fetchall()]
            if not hubs:
                print(f"{Fore.RED}No hubs in player_hubs table")
                sys.exit(1)

        all_fleet = []
        for i, hub in enumerate(hubs, 1):
            print(f"\n{Fore.CYAN}[{i}/{len(hubs)}] Scraping hub {hub}...")
            if not select_hub(cdp, hub):
                continue

            ac_list = get_aircraft_at_hub(cdp, hub)
            for ac in ac_list:
                ac["hub"] = hub
            all_fleet.extend(ac_list)
            idle = sum(1 for a in ac_list if a["util"] == 0)
            print(f"  {len(ac_list)} aircraft ({idle} idle)")

        resolved, unresolved = resolve_skin_ids(all_fleet)
        upsert_fleet(all_fleet)
        print(f"\n{Fore.GREEN}Synced {len(all_fleet)} aircraft to DB")
        note = f" ({unresolved} unidentified)" if unresolved else ""
        print(f"{Fore.GREEN}Livery ids resolved for {resolved} aircraft{note}")

        print_summary(db)
    finally:
        cdp.close()
        close_db()


if __name__ == "__main__":
    main()
