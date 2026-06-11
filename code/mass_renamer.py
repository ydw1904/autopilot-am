#!/usr/bin/env python3
"""
Mass Aircraft Renamer — rename in-game aircraft from one prefix to another.

Use case: repurpose aircraft from a defunct circuit to a new one without
touching the DB. Aircraft named "<OLD>" or "<OLD>-<NNN>" become "<NEW>" or
"<NEW>-<NNN>" respectively.

Usage:
    python3 mass_renamer.py --old MPM-C003 --new MPM-C012
    python3 mass_renamer.py --old MPM-C003 --new MPM-C012 --dry-run

Match is case-insensitive on the OLD prefix. The trailing "-<NNN>" suffix (if
present) is preserved verbatim. Aircraft whose name is exactly the OLD prefix
get renamed to just the NEW prefix (no suffix).

Requires Chrome with --remote-debugging-port=9222 and an open AM tab.
"""

import argparse, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import CDP, get_am_tab  # noqa: E402
from aircraft_numberer import (  # noqa: E402
    discover_total_pages, scrape_all_aircraft, get_form_token, rename,
)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--old", required=True, help="Current name prefix (e.g. MPM-C003)")
    p.add_argument("--new", required=True, help="Target name prefix (e.g. MPM-C012)")
    p.add_argument("--limit", type=int, default=0,
                   help="Cap the number of aircraft renamed (0 = all)")
    p.add_argument("--strip-suffix", action="store_true",
                   help="Drop the trailing -NNN; rename all matches to bare --new")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    old, new = args.old.strip(), args.new.strip()
    if not old or not new:
        print("ERROR: empty prefix", file=sys.stderr); sys.exit(2)
    if old.upper() == new.upper():
        print(f"old and new are identical ({old}) — nothing to do"); return

    # Matches: "<OLD>" exactly OR "<OLD>-<digits>"
    match_re = re.compile(rf"^{re.escape(old)}(-\d+)?$", re.IGNORECASE)

    print(f"Connecting to Chrome…")
    cdp = CDP(get_am_tab()["webSocketDebuggerUrl"], timeout=120)
    cdp.connect()
    try:
        print(f"Discovering /aircraft pagination (filtered to {old!r})…")
        total = discover_total_pages(cdp, name_filter=old)
        print(f"  {total} pages — scraping aircraft…")
        all_ac = scrape_all_aircraft(cdp, total, name_filter=old)
        print(f"  {len(all_ac)} aircraft total")

        plan = []  # (id, old_name, new_name)
        for ac in all_ac:
            name = ac.get("name") or ""
            m = match_re.match(name)
            if not m:
                continue
            suffix = "" if args.strip_suffix else (m.group(1) or "")
            plan.append((ac["id"], name, f"{new}{suffix}"))

        # Deterministic order then optional cap
        plan.sort(key=lambda x: x[0])
        if args.limit > 0 and len(plan) > args.limit:
            print(f"  capping {len(plan)} → {args.limit} (lowest aircraft IDs)")
            plan = plan[:args.limit]

        print(f"\nMatched {len(plan)} aircraft for rename:")
        for aid, on, nn in plan[:15]:
            print(f"  {aid}  {on!r} -> {nn!r}")
        if len(plan) > 15:
            print(f"  … and {len(plan) - 15} more")

        if not plan:
            print("Nothing to do."); return

        if args.dry_run:
            print("\n[dry-run] no changes made.")
            return

        ok = fail = 0
        for i, (aid, on, nn) in enumerate(plan, 1):
            tok = get_form_token(cdp, aid)
            if not tok:
                print(f"  [{i:4d}/{len(plan)}] {aid}: NO TOKEN")
                fail += 1
                continue
            status = rename(cdp, aid, nn, tok)
            if status in (200, 302):
                ok += 1
                if i % 10 == 0 or i == len(plan):
                    print(f"  [{i:4d}/{len(plan)}] {on!r} -> {nn!r}")
            else:
                fail += 1
                print(f"  [{i:4d}/{len(plan)}] {on!r} -> {nn!r} FAIL HTTP {status}")
            time.sleep(0.15)

        print(f"\nRenamed {ok}/{len(plan)}  Failed: {fail}")
        sys.exit(0 if fail == 0 else 2)
    finally:
        cdp.close()


if __name__ == "__main__":
    main()
