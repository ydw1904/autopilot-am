#!/usr/bin/env python3
"""
Circuit Renamer — rename a circuit (DB) and all its in-game aircraft.

Renames the circuits + circuit_routes rows from <OLD> to <NEW>, then walks
/aircraft and renames every aircraft whose name matches:
  <OLD>          -> <NEW>
  <OLD>-<MMM>    -> <NEW>-<MMM>

Usage:
    python3 circuit_renamer.py --old MPM-C003 --new X381
    python3 circuit_renamer.py --old MPM-C003 --new X381 --dry-run
    python3 circuit_renamer.py --old MPM-C003 --new X381 --db-only
"""

import argparse, json, os, re, sys, time
from urllib.parse import quote

from cdp import CDP, get_am_tab  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import circuit_exists, rename_circuit_in_db  # noqa: E402
from aircraft_numberer import (  # noqa: E402
    discover_total_pages, scrape_all_aircraft, get_form_token, rename,
)


def rename_circuit(old: str, new: str, dry_run: bool = False, db_only: bool = False) -> int:
    if old == new:
        print(f"old and new are identical ({old}) — nothing to do")
        return 0
    if not circuit_exists(old):
        print(f"ERROR: source circuit {old!r} not found in DB", file=sys.stderr)
        return 1
    if circuit_exists(new):
        print(f"ERROR: target circuit {new!r} already exists in DB", file=sys.stderr)
        return 1

    print(f"Renaming circuit {old!r} -> {new!r}")

    if dry_run:
        print(f"  [dry-run] would UPDATE circuits.name and circuit_routes.circuit_name")
    else:
        rename_circuit_in_db(old, new)
        print(f"  DB updated.")

    if db_only:
        return 0

    cdp = CDP(get_am_tab()["webSocketDebuggerUrl"], timeout=120)
    cdp.connect()
    try:
        total_pages = discover_total_pages(cdp, name_filter=old)
        print(f"  Scraping {total_pages} filtered /aircraft pages (name~{old!r}) …")
        all_ac = scrape_all_aircraft(cdp, total_pages, name_filter=old)
        print(f"  {len(all_ac)} aircraft total")

        # Match `<OLD>` or `<OLD>-<MMM>` (1-3 digit suffix permissive).
        pat = re.compile(rf"^{re.escape(old)}(-(\d{{1,3}}))?$")
        plan = []
        for ac in all_ac:
            m = pat.match(ac["name"])
            if not m:
                continue
            suffix = m.group(1) or ""
            new_name = new + suffix
            plan.append((ac["id"], ac["name"], new_name))

        print(f"  {len(plan)} aircraft to rename")
        if not plan:
            return 0

        if dry_run:
            for aid, old_name, new_name in plan[:10]:
                print(f"    [dry-run] {aid}  {old_name!r} -> {new_name!r}")
            if len(plan) > 10:
                print(f"    … and {len(plan) - 10} more")
            return 0

        ok = fail = 0
        for idx, (aid, old_name, new_name) in enumerate(plan, 1):
            tok = get_form_token(cdp, aid)
            if not tok:
                print(f"  [{idx:3d}/{len(plan)}] {aid}: NO TOKEN", flush=True)
                fail += 1
                continue
            status = rename(cdp, aid, new_name, tok)
            if status in (200, 302):
                ok += 1
                if idx % 10 == 0 or idx == len(plan):
                    print(f"  [{idx:3d}/{len(plan)}] {old_name!r} -> {new_name!r}", flush=True)
            else:
                fail += 1
                print(f"  [{idx:3d}/{len(plan)}] {old_name!r} -> {new_name!r} FAIL HTTP {status}",
                      flush=True)
            time.sleep(0.2)

        print(f"\nIn-game renamed: {ok}/{len(plan)}  Failed: {fail}")
        return 0 if fail == 0 else 2
    finally:
        cdp.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--old", required=True, help="Current circuit name (e.g. MPM-C003)")
    p.add_argument("--new", required=True, help="New circuit name (e.g. X381)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--db-only", action="store_true",
                   help="Only update the DB; skip in-game aircraft renames.")
    args = p.parse_args()
    sys.exit(rename_circuit(args.old.upper(), args.new.upper(), args.dry_run, args.db_only))


if __name__ == "__main__":
    main()
