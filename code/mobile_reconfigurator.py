#!/usr/bin/env python3
"""
Mobile Aircraft Reconfigurator — the mobile-API equivalent of
`aircraft_reconfigurator.py`: bring all aircraft assigned to a circuit into
line with the circuit's planned hub and seat configuration.

Same contract as the web tool (same DB, same naming convention, same CLI), but
it talks to the mobile JSON API instead of driving Chrome:

  - discovery      GET  bfa/paged/aircraft   (50 aircraft/request, and each row
                                              already carries seats + hub id, so
                                              the skip check costs no extra call)
  - seats          POST aircraft/reconfigure
  - hub            POST aircraft/<id>/assignHub
  - verification   GET  aircraft/<id>

Differences from the web path that matter operationally:
  * Writes report `status: 1` + a semantic message, and `_request` raises on
    `status: 0` — so a rejected action is an exception, not a silent no-op that
    has to be caught by reading the value back. We still verify, but the
    verification is a backstop rather than the only signal.
  * The livery is not in the reconfigure payload and is preserved, so there is
    no checked-skin guard to get wrong.
  * No CSRF token, no sliders, no settle delay.

Usage:
    python3 mobile_reconfigurator.py --circuit MPM-C003
    python3 mobile_reconfigurator.py --circuit MPM-C003 --dry-run

Requirements: a valid mobile session (~/.airlines_manager/session.json).
Refresh with tools/mobile-capture/refresh_mobile_session.sh.
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import get_db  # noqa: E402
from mobile_api import (  # noqa: E402
    AMClient, AMSession, AMError, AMAuthError, fmt_money,
)

# Paid actions are rejected when the balance is non-positive. The web tool
# refuses up front for the same reason; here the server would at least tell us
# why, but burning a request per aircraft to be told so is still pointless.
MIN_BALANCE = 1

# Aircraft per fleet-listing request. The API has no name filter, so discovery
# always pages the whole fleet; a large page size is what keeps that cheap.
FLEET_PAGE_SIZE = 500


def _target_from_db(circuit_name: str):
    """(hub_iata, seats dict) for a circuit, or None if it isn't in the DB."""
    db = get_db()
    row = db.execute(
        "SELECT hub_iata, eco_seats, bus_seats, fir_seats, cargo_seats "
        "FROM circuits WHERE name = ?", (circuit_name,)
    ).fetchone()
    if not row:
        return None
    return row["hub_iata"].upper(), {
        "eco": row["eco_seats"] or 0,
        "bus": row["bus_seats"] or 0,
        "first": row["fir_seats"] or 0,
        "cargo": row["cargo_seats"] or 0,
    }


def _hub_id_for(iata: str):
    """Resolve an IATA code to the player's hub id.

    player_hubs is populated by the web-side tooling, but the ids are the same
    id space the mobile API uses (spot-checked: FRA -> 9480309 is exactly the
    hubId the app posts), so one table serves both surfaces.
    """
    row = get_db().execute(
        "SELECT hub_id FROM player_hubs WHERE hub_iata = ?", (iata,)
    ).fetchone()
    return row["hub_id"] if row else None


def _matching_fleet(client: AMClient, circuit_name: str):
    """Owned aircraft named <CIRCUIT> or <CIRCUIT>-<NNN>, as compact records.

    The fleet listing already includes seats (se/sb/sf/sp) and hub (h_id), so
    the "is it already correct?" decision needs no per-aircraft request.
    """
    prefix_re = re.compile(rf"^{re.escape(circuit_name)}(?:-\d{{1,3}})?$")
    out = []
    # The endpoint ignores every name-filter param tried (name/search/filterName/q),
    # so the whole fleet has to be paged. itemPerPage scales, though: 500/page
    # turns a ~2.8k-aircraft fleet into 6 requests instead of 56.
    for it in client.fleet(per_page=FLEET_PAGE_SIZE):
        name = (it.get("n") or "").strip()
        if not prefix_re.match(name):
            continue
        out.append({
            "id": it["id"],
            "name": name,
            "eco": it.get("se") or 0,
            "bus": it.get("sb") or 0,
            "first": it.get("sf") or 0,
            "cargo": it.get("sp") or 0,
            "hub_id": it.get("h_id"),
            "skin_id": it.get("as_id"),
        })
    return out


def _verify(client: AMClient, aircraft_id: int, target, target_hub_id,
            expect_name: str, expect_skin):
    """Re-read the aircraft and confirm seats, hub, name and livery.

    Name and livery are checked because the reconfigure payload carries the
    name (a bad echo would rename the plane) and because the livery is the one
    thing that cannot be undone if it ever did get dropped.
    """
    p = client.aircraft(aircraft_id) or {}
    seats = p.get("seats") or {}
    got = {
        "eco": seats.get("eco"),
        "bus": seats.get("business"),
        "first": seats.get("first"),
        "cargo": p.get("payload"),
    }
    problems = []
    if got != {k: target[k] for k in ("eco", "bus", "first", "cargo")}:
        problems.append(f"seats {got} != {target}")
    hub_id = (p.get("hub") or {}).get("id")
    if target_hub_id is not None and hub_id != target_hub_id:
        problems.append(f"hub {hub_id} != {target_hub_id}")
    if p.get("name") != expect_name:
        problems.append(f"name {p.get('name')!r} != {expect_name!r}")
    skin_id = (p.get("skins") or {}).get("id")
    if expect_skin is not None and skin_id != expect_skin:
        problems.append(f"LIVERY CHANGED {expect_skin} -> {skin_id}")
    return problems


def reconfigure_circuit(circuit_name: str, dry_run: bool = False,
                        limit: int | None = None) -> int:
    target = _target_from_db(circuit_name)
    if not target:
        print(f"ERROR: circuit {circuit_name} not in DB", file=sys.stderr)
        return 1
    target_iata, target_seats = target

    target_hub_id = _hub_id_for(target_iata)
    if target_hub_id is None:
        print(f"ERROR: hub {target_iata} not in player_hubs — cannot resolve a "
              "hub id to relocate to.", file=sys.stderr)
        return 1

    print(f"Target for {circuit_name}: hub={target_iata} (id {target_hub_id}) "
          f"seats=eco={target_seats['eco']} bus={target_seats['bus']} "
          f"first={target_seats['first']} cargo={target_seats['cargo']}t")

    try:
        session = AMSession.load()
    except AMAuthError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    client = AMClient(session)

    try:
        balance = (client.resources() or {}).get("dollar")
        if balance is not None and balance < MIN_BALANCE and not dry_run:
            print(f"ERROR: balance is {fmt_money(balance)} — reconfigure and "
                  "relocate are paid actions. Aborting.", file=sys.stderr)
            return 1

        print("Listing fleet …")
        matched = _matching_fleet(client, circuit_name)
        print(f"  {len(matched)} match {circuit_name} prefix")
        if not matched:
            print("Nothing to do.")
            return 0

        if limit is not None:
            # Trim to aircraft that actually need work, so --limit 1 is a real
            # write test and not a no-op on an already-correct aircraft.
            needs = [a for a in matched
                     if a["hub_id"] != target_hub_id
                     or any(a[k] != target_seats[k]
                            for k in ("eco", "bus", "first", "cargo"))]
            matched = needs[:limit]
            print(f"  --limit {limit}: acting on {len(matched)} of "
                  f"{len(needs)} needing changes")

        relocated = reconfigured = skipped = failed = 0
        for idx, ac in enumerate(matched, 1):
            aid, name = ac["id"], ac["name"]
            label = f"[{idx:3d}/{len(matched)}] {aid} {name!r}"

            need_seats = any(ac[k] != target_seats[k]
                             for k in ("eco", "bus", "first", "cargo"))
            need_hub = ac["hub_id"] != target_hub_id

            if not need_seats and not need_hub:
                skipped += 1
                continue

            if dry_run:
                if need_hub:
                    print(f"  {label} relocate hub {ac['hub_id']} -> "
                          f"{target_hub_id} ({target_iata})")
                if need_seats:
                    print(f"  {label} reconfigure "
                          f"e{ac['eco']} b{ac['bus']} f{ac['first']} c{ac['cargo']}"
                          f" -> e{target_seats['eco']} b{target_seats['bus']}"
                          f" f{target_seats['first']} c{target_seats['cargo']}")
                if need_seats:
                    reconfigured += 1
                if need_hub:
                    relocated += 1
                continue

            # Order matches the app's own: seats first, then hub.
            try:
                if need_seats:
                    client.reconfigure(
                        aid, name=name,
                        eco=target_seats["eco"], bus=target_seats["bus"],
                        first=target_seats["first"],
                        payload=target_seats["cargo"])
                if need_hub:
                    client.assign_hub(aid, target_hub_id)
            except AMError as e:
                print(f"  {label} FAIL: {e}")
                failed += 1
                continue

            problems = _verify(client, aid, target_seats, target_hub_id,
                               expect_name=name, expect_skin=ac["skin_id"])
            if problems:
                print(f"  {label} VERIFY FAILED: {'; '.join(problems)}")
                failed += 1
                continue

            if need_seats:
                reconfigured += 1
            if need_hub:
                relocated += 1

            if idx % 10 == 0 or idx == len(matched):
                print(f"  {label} done", flush=True)

        print(f"\nRelocated: {relocated}  Reconfigured: {reconfigured}  "
              f"Skipped (already correct): {skipped}  Failed: {failed}")
        return 0 if failed == 0 else 2
    finally:
        client.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--circuit", required=True, help="Circuit name, e.g. MPM-C003")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=None,
                   help="Act on at most N aircraft that need changes "
                        "(use --limit 1 to smoke-test the write path).")
    args = p.parse_args()
    sys.exit(reconfigure_circuit(args.circuit.upper(), args.dry_run, args.limit))


if __name__ == "__main__":
    main()
