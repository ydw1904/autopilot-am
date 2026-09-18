#!/usr/bin/env python3
"""
Mass Unscheduler — clear flight schedules for aircraft matching name prefixes.

Usage:
    python3 mass_unscheduler.py MPM-C007
    python3 mass_unscheduler.py MPM-C007 SHOP-A350-900ULR
    python3 mass_unscheduler.py MPM-C007 --dry-run

Matches aircraft whose name STARTS WITH any provided prefix (case-insensitive).
So "MPM-C007" hits "MPM-C007-001", "MPM-C007-099", etc.

Mobile API first (bfa/paged/aircraft discovery, GET planning/delete/<id>);
falls back to Chrome (/aircraft?page=N + the web planning endpoint) only when
there is no mobile session on disk. --cdp forces the Chrome path.
"""

import argparse, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import CDP, get_am_tab
from circuit_scheduler import clear_schedule, _mobile_client  # noqa: E402
from aircraft_numberer import discover_total_pages, scrape_all_aircraft  # noqa: E402
from mobile_renamer import list_fleet  # noqa: E402


def match_mobile(client, prefixes):
    """The fleet listing ignores name filters, so page it once and match here."""
    return list_fleet(client, lambda n: n.upper().startswith(tuple(prefixes)))


def match_cdp(cdp, prefixes):
    # Server-side filter per prefix. Dedupe by id since one aircraft could in
    # principle match two prefixes.
    matched_by_id = {}
    for pref in prefixes:
        pages = discover_total_pages(cdp, name_filter=pref)
        print(f"  {pref}: {pages} filtered pages, scraping…")
        for ac in scrape_all_aircraft(cdp, pages, name_filter=pref):
            if (ac.get("name") or "").upper().startswith(pref):
                matched_by_id[ac["id"]] = ac
    return list(matched_by_id.values())


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("prefixes", nargs="+",
                   help="Name prefixes to match (case-insensitive, startswith)")
    p.add_argument("--dry-run", action="store_true",
                   help="List matches without clearing")
    p.add_argument("--cdp", action="store_true",
                   help="Use Chrome instead of the mobile API")
    args = p.parse_args()

    prefixes = [s.upper() for s in args.prefixes]
    print(f"Matching prefixes: {' '.join(prefixes)}")

    backend = None if args.cdp else _mobile_client()
    if backend:
        print("Backend: mobile API")
    else:
        print("Connecting to Chrome…")
        backend = CDP(get_am_tab()["webSocketDebuggerUrl"], timeout=120)
        backend.connect()
    try:
        found = (match_cdp(backend, prefixes) if isinstance(backend, CDP)
                 else match_mobile(backend, prefixes))
        matched = sorted(found, key=lambda a: a["id"])
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
            res = clear_schedule(backend, ac["id"])
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
        backend.close()


if __name__ == "__main__":
    main()
