#!/usr/bin/env python3
"""
Mobile Aircraft Renamer — the mobile-API equivalent of `mass_renamer.py`:
rename in-game aircraft from one name prefix to another.

Same contract as the web tool (same CLI, same matching rules), but it talks to
the mobile JSON API instead of driving Chrome:

  - discovery   GET  bfa/paged/aircraft   (500/request, and each row already
                                           carries the seat config the rename
                                           has to echo back)
  - rename      POST aircraft/reconfigure

There is no dedicated rename endpoint on the mobile API — `aircraft/reconfigure`
carries `name` and writes it. Echoing the aircraft's CURRENT seats back makes
the seat change a no-op, and a no-op reconfigure is **free**: verified on a live
aircraft, balance delta $0. That is also why the seats must be read first and
passed verbatim — a reconfigure with wrong seats would both cost money and
reconfigure the plane.

Differences from the web path that matter operationally:
  * No per-aircraft form token to fetch, so a rename is one request, not two.
  * A refused write raises (`status: 0`) instead of returning an HTTP code that
    has to be interpreted.
  * The livery is not in the payload and is preserved.

Usage:
    python3 mobile_renamer.py --old MPM-C003 --new MPM-C012
    python3 mobile_renamer.py --old MPM-C003 --new MPM-C012 --dry-run

Requirements: a valid mobile session (~/.airlines_manager/session.json).
Refresh with tools/mobile-capture/refresh_mobile_session.sh.
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mobile_api import AMClient, AMError, AMSession  # noqa: E402

# The fleet listing ignores every name-filter param, so discovery always pages
# the whole fleet; a large page size is what keeps that cheap (6 requests for
# a ~2.8k fleet rather than 56).
FLEET_PAGE_SIZE = 500


def list_fleet(client: AMClient, match=None) -> list:
    """Owned aircraft as {id, name, eco, bus, first, cargo}.

    `match` is an optional predicate on the name, applied while paging so a
    big fleet does not have to be materialised twice.
    """
    out = []
    for it in client.fleet(per_page=FLEET_PAGE_SIZE):
        name = (it.get("n") or "").strip()
        if match and not match(name):
            continue
        out.append({"id": it["id"], "name": name,
                    "eco": it.get("se") or 0, "bus": it.get("sb") or 0,
                    "first": it.get("sf") or 0, "cargo": it.get("sp") or 0})
    return out


def rename(client: AMClient, aircraft: dict, new_name: str) -> dict:
    """Rename one aircraft, leaving its seat configuration untouched."""
    return client.reconfigure(aircraft["id"], name=new_name,
                              eco=aircraft["eco"], bus=aircraft["bus"],
                              first=aircraft["first"], payload=aircraft["cargo"])


def build_plan(fleet: list, old: str, new: str, *, strip_suffix: bool = False,
               limit: int = 0) -> list:
    """[(aircraft, new_name)] for every name matching `<OLD>` or `<OLD>-<NNN>`.

    Same rules as the web tool: case-insensitive on the old prefix, the
    trailing `-<NNN>` preserved verbatim unless `strip_suffix`, ordered by
    aircraft id so a capped run is deterministic (oldest planes first).
    """
    match_re = re.compile(rf"^{re.escape(old)}(-\d+)?$", re.IGNORECASE)
    plan = []
    for ac in fleet:
        m = match_re.match(ac["name"])
        if not m:
            continue
        suffix = "" if strip_suffix else (m.group(1) or "")
        plan.append((ac, f"{new}{suffix}"))
    plan.sort(key=lambda pair: pair[0]["id"])
    return plan[:limit] if limit > 0 else plan


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
        sys.exit("ERROR: empty prefix")
    if old.upper() == new.upper():
        print(f"old and new are identical ({old}) — nothing to do")
        return

    client = AMClient(AMSession.load())
    try:
        print(f"Listing fleet (matching {old!r}) …")
        prefix_re = re.compile(rf"^{re.escape(old)}(-\d+)?$", re.IGNORECASE)
        fleet = list_fleet(client, match=prefix_re.match)
        plan = build_plan(fleet, old, new,
                          strip_suffix=args.strip_suffix, limit=args.limit)

        print(f"\nMatched {len(plan)} aircraft for rename:")
        for ac, nn in plan[:15]:
            print(f"  {ac['id']}  {ac['name']!r} -> {nn!r}")
        if len(plan) > 15:
            print(f"  … and {len(plan) - 15} more")

        if not plan:
            print("Nothing to do.")
            return
        if args.dry_run:
            print("\n[dry-run] no changes made.")
            return

        ok = fail = 0
        for i, (ac, nn) in enumerate(plan, 1):
            try:
                rename(client, ac, nn)
                ok += 1
                if i % 10 == 0 or i == len(plan):
                    print(f"  [{i:4d}/{len(plan)}] {ac['name']!r} -> {nn!r}")
            except AMError as exc:
                fail += 1
                print(f"  [{i:4d}/{len(plan)}] {ac['name']!r} -> {nn!r} FAIL {exc}")

        print(f"\nRenamed {ok}/{len(plan)}  Failed: {fail}")
        sys.exit(0 if fail == 0 else 2)
    finally:
        client.close()


if __name__ == "__main__":
    main()
