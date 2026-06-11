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

import argparse, os, sqlite3, sys, time

from colorama import init, Fore, Style

init(autoreset=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import CDP, get_am_tab, BASE_URL
from db import DB, upsert_fleet
from circuit_scheduler import select_hub, get_aircraft_at_hub



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

    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row

    if args.summary:
        print_summary(db)
        db.close()
        return

    tab = get_am_tab()
    if not tab:
        print(f"{Fore.RED}No AM tab found. Open Chrome with --remote-debugging-port=9222")
        sys.exit(1)

    cdp = CDP(tab["webSocketDebuggerUrl"])
    cdp.connect()
    try:
        print(f"{Fore.CYAN}Navigating to planning page...")
        cdp.navigate(f"{BASE_URL}/network/planning")
        cdp.wait(4)

        for attempt in range(15):
            count = cdp.eval(
                "document.querySelectorAll('#aircraftList .aircraftListMiniBox').length || 0"
            )
            if count and count > 0:
                print(f"  Page loaded ({count} aircraft visible)")
                break
            cdp.wait(1)

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

            ac_list = get_aircraft_at_hub(cdp)
            for ac in ac_list:
                ac["hub"] = hub
            all_fleet.extend(ac_list)
            idle = sum(1 for a in ac_list if a["util"] == 0)
            print(f"  {len(ac_list)} aircraft ({idle} idle)")

        upsert_fleet(all_fleet)
        print(f"\n{Fore.GREEN}Synced {len(all_fleet)} aircraft to DB")

        print_summary(db)
    finally:
        cdp.close()
        db.close()


if __name__ == "__main__":
    main()
