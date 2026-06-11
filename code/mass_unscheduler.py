#!/usr/bin/env python3
"""
Mass Unscheduler — clear flight schedules for aircraft matching name prefixes.

Usage:
    python3 mass_unscheduler.py MPM-C007
    python3 mass_unscheduler.py MPM-C007 SHOP-A350-900ULR
    python3 mass_unscheduler.py MPM-C007 --dry-run

Matches aircraft whose name STARTS WITH any provided prefix (case-insensitive).
So "MPM-C007" hits "MPM-C007-001", "MPM-C007-099", etc.

Walks the global /aircraft?page=N listing and POSTs clear-schedule for each
matched id.

Requires Chrome with --remote-debugging-port=9222 and an open AM tab.
"""

import argparse, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import CDP, get_am_tab
from circuit_scheduler import clear_schedule  # noqa: E402
from aircraft_numberer import discover_total_pages, scrape_all_aircraft  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("prefixes", nargs="+",
                   help="Name prefixes to match (case-insensitive, startswith)")
    p.add_argument("--dry-run", action="store_true",
                   help="List matches without clearing")
    args = p.parse_args()

    prefixes = [s.upper() for s in args.prefixes]
    print(f"Matching prefixes: {' '.join(prefixes)}")

    print("Connecting to Chrome…")
    cdp = CDP(get_am_tab()["webSocketDebuggerUrl"], timeout=120)
    cdp.connect()
    try:
        # Server-side filter per prefix — much faster than scanning the full
        # fleet, especially as it grows. Dedupe by id since one aircraft
        # could in principle match two prefixes.
        matched_by_id = {}
        for pref in prefixes:
            pages = discover_total_pages(cdp, name_filter=pref)
            print(f"  {pref}: {pages} filtered pages — scraping…")
            for ac in scrape_all_aircraft(cdp, pages, name_filter=pref):
                up = (ac.get("name") or "").upper()
                if up.startswith(pref):
                    matched_by_id[ac["id"]] = ac
        matched = sorted(matched_by_id.values(), key=lambda a: a["id"])
        print(f"  {len(matched)} aircraft matched after dedupe")

        print(f"\nMatched {len(matched)} aircraft:")
        # Group counts by which prefix matched (for sanity)
        per_pref = {}
        for ac in matched:
            up = (ac.get("name") or "").upper()
            for pref in prefixes:
                if up.startswith(pref):
                    per_pref[pref] = per_pref.get(pref, 0) + 1
                    break
        for pref, n in per_pref.items():
            print(f"  {pref}: {n}")
        for pref in prefixes:
            if pref not in per_pref:
                print(f"  {pref}: 0  (no match)")

        if not matched:
            print("Nothing to do.")
            return

        if args.dry_run:
            print("\n[dry-run] would clear:")
            for ac in matched[:20]:
                print(f"  {ac['id']}  {ac['name']!r}")
            if len(matched) > 20:
                print(f"  … and {len(matched) - 20} more")
            return

        ok = fail = 0
        for i, ac in enumerate(matched, 1):
            res = clear_schedule(cdp, ac["id"])
            if res and res.get("result") is True:
                ok += 1
                if i % 10 == 0 or i == len(matched):
                    print(f"  [{i:4d}/{len(matched)}] cleared {ac['name']!r}")
            else:
                fail += 1
                print(f"  [{i:4d}/{len(matched)}] FAIL {ac['name']!r}: {res}")
            time.sleep(0.15)

        print(f"\nDone. cleared={ok} fail={fail}")
        sys.exit(0 if fail == 0 else 2)
    finally:
        cdp.close()


if __name__ == "__main__":
    main()
