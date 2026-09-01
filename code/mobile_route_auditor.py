#!/usr/bin/env python3
"""Audit unpurchased routes with the mobile API and store their demand."""

import argparse
import json
import math
import sys

import httpx

from db import get_db, get_player_hub_id
from mobile_api import AMAuthError, AMClient, AMSession
from scrape_internal_audits import ensure_snapshot_table


def candidates(db, hub, routes=None, country=None, limit=None):
    where = ["hub_iata=?", "is_owned=0", "COALESCE(eco_demand, 0)<=0"]
    args = [hub.upper()]
    if routes:
        marks = ",".join("?" for _ in routes)
        where.append(f"UPPER(dest_iata) IN ({marks})")
        args.extend(i.upper() for i in routes)
    if country:
        where.append("LOWER(dest_country)=?")
        args.append(country.lower())
    sql = ("SELECT dest_iata, dest_country FROM routes WHERE "
           + " AND ".join(where) + " ORDER BY dest_iata")
    if limit is not None:
        sql += " LIMIT ?"
        args.append(max(0, limit))
    return db.execute(sql, args).fetchall()


# Great-circle radius fitted against 10,953 route distances the game itself
# reported: median error 0 km, max 1 km. The textbook 6371 is ~2 km short over
# a 10,000 km leg, and distance drives flight time, so keep the fitted value.
EARTH_KM = 6372.46


def great_circle_km(a, b):
    la1, lo1, la2, lo2 = (math.radians(float(x)) for x in
                          (a["lt"], a["lg"], b["lt"], b["lg"]))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return round(2 * math.asin(math.sqrt(h)) * EARTH_KM)


def sync_world_rows(db, world, hub):
    """Add the hub's missing destinations, and repair country codes everywhere.

    The `routes` table came from an HTML sweep that both missed airports (880
    of the game's 2666 were absent for CGK) and mangled `dest_country` into
    things like "FinlandSavonlinna". `bfa/world` is the game's own airport
    list, so it fixes both: a row per available airport, and the real country
    code keyed by IATA — the latter for every hub at once, not just this one.
    """
    countries = {c["id"]: c["c"] for c in world.get("countryList", [])}

    def country_of(airport):
        # `cty` is the country; `ty` is the REGION, which for big countries is
        # an id countryList does not carry at all (LAX ty=229 California,
        # PEK ty=276) — 557 of 2666 airports. Keying off `ty` silently leaves
        # those without a country code, and db.get_dest_country feeds the
        # route-buying URL, so they would fail to buy.
        return countries.get(airport.get("cty", airport.get("ty")))
    airports = {a["i"].upper(): a for a in world.get("airportList", [])
                if a.get("i") and a.get("av")}
    origin = airports.get(hub)
    if not origin:
        raise SystemExit(f"{hub} is not an available airport in bfa/world")

    have = {r[0] for r in db.execute(
        "SELECT dest_iata FROM routes WHERE hub_iata=?", (hub,))}
    new = [(hub, iata, f"{a.get('ct', '')} - {a.get('n', '')}".strip(" -"),
            country_of(a), great_circle_km(origin, a), a.get("cat"))
           for iata, a in sorted(airports.items())
           if iata != hub and iata not in have]
    db.executemany(
        "INSERT OR IGNORE INTO routes (hub_iata, dest_iata, dest_name, "
        "dest_country, distance_km, dest_category) VALUES (?,?,?,?,?,?)", new)
    # The mangled rows kept their city in `dest_country` ("FinlandSavonlinna")
    # with `dest_name` empty, so repairing the code alone would drop the only
    # copy of the name. Fill the blanks from the game's own names — never
    # overwriting a name a row already has.
    fixed = named = 0
    for iata, a in airports.items():
        code = country_of(a)
        if code:
            fixed += db.execute(
                "UPDATE routes SET dest_country=? WHERE dest_iata=? AND "
                "COALESCE(dest_country,'') <> ?", (code, iata, code)).rowcount
        name = f"{a.get('ct', '')} - {a.get('n', '')}".strip(" -")
        if name:
            named += db.execute(
                "UPDATE routes SET dest_name=? WHERE dest_iata=? AND "
                "COALESCE(dest_name,'') = ''", (name, iata)).rowcount
    db.commit()
    return {"rows_added": len(new), "countries_fixed": fixed, "names_filled": named}


# Coupons and cash both ride on the hub pricing page, so one read covers both.
# Internal audits use coupons; external audits spend cash and require a
# separate explicit opt-in.
COUPON_CHECK_EVERY = 100


def audit_budget(client, hub_id):
    """(free audit coupons, dollars) — both from one hub pricing read."""
    body = client.hub_pricing_page(int(hub_id), 1)
    return body.get("freeAudits"), (body.get("ressources") or {}).get("dollar")


def demand_from_audit(audit):
    demand = audit.get("demand") or {}
    try:
        return tuple(int(demand[key]) for key in ("eco", "bus", "first", "cargo"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"audit has no complete demand: {demand!r}") from exc


def retry_once(call, *args):
    """Retry a free read once when its response is lost in transit."""
    try:
        return call(*args)
    except httpx.TransportError:
        return call(*args)


def audit_routes(db, client, hub, hub_id, rows, allow_paid=False):
    if not allow_paid:
        raise ValueError("external route audits spend cash; pass --allow-paid")
    airports = {a.get("i", "").upper(): a["id"]
                for a in retry_once(client.world).get("airportList", [])
                if a.get("i")}
    updated, failed = [], []
    stopped = None
    for index, row in enumerate(rows, 1):
        iata = row["dest_iata"].upper()
        try:
            airport_id = airports[iata]
            demand = demand_from_audit(
                client.external_route_audit(airport_id, hub_id))
            db.execute(
                "UPDATE routes SET eco_demand=?, bus_demand=?, fir_demand=?, "
                "cargo_demand=? WHERE hub_iata=? AND dest_iata=? AND is_owned=0",
                (*demand, hub, iata),
            )
            db.commit()  # each audit survives a later API failure
            updated.append(iata)
            print(f"[{index}/{len(rows)}] {iata}: {demand}", file=sys.stderr)
        except AMAuthError:
            raise  # a dead session would fail every remaining route in a row
        except Exception as exc:  # keep the batch moving and report every miss
            failed.append({"iata": iata, "error": str(exc)})
            print(f"[{index}/{len(rows)}] {iata}: FAIL {exc}", file=sys.stderr)
    return {"updated": updated, "failed": failed, "stopped": stopped}


def audit_lines(db, client, hub, hub_id, lines):
    """Fresh internal audits for owned lines: one coupon each, no cash.

    The line list and its *existing* audit demand come free from the hub
    pricing page — this only spends coupons to refresh them. A fresh audit
    resets the line's audit reliability to 0 and leaves audit.price untouched;
    demand is the thing that moves (verified live on CGK-FIH).
    """
    ensure_snapshot_table(db)
    updated, failed = [], []
    stopped = None
    for index, line in enumerate(lines, 1):
        iata = (line.get("aTwoName") or "").upper()
        line_id = line.get("id")
        try:
            if index > 1 and index % COUPON_CHECK_EVERY == 1:
                coupons, _ = audit_budget(client, hub_id)
                if coupons is not None and coupons < len(lines) - index + 1:
                    # Out of coupons the game bills `audit.cost` per line in
                    # cash ($252k on CGK-FIH), so stop rather than spend it.
                    stopped = f"only {coupons} coupons left after {len(updated)}"
                    print(stopped, file=sys.stderr)
                    break
            old = (line.get("audit") or {}).get("demand") or {}
            if not db.execute("SELECT 1 FROM routes WHERE hub_iata=? AND "
                              "dest_iata=? LIMIT 1", (hub, iata)).fetchone():
                # No row to store the result in, so the coupon would buy
                # nothing — skip before spending it, not after.
                raise ValueError("no routes row for this line — not audited")
            new = demand_from_audit(client.internal_line_audit(line_id))
            db.execute(
                "INSERT INTO routes_demand_snapshot (hub_iata, dest_iata, line_id,"
                " old_eco, old_bus, old_fir, old_cargo,"
                " new_eco, new_bus, new_fir, new_cargo) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (hub, iata, line_id, old.get("eco"), old.get("bus"),
                 old.get("first"), old.get("cargo"), *new),
            )
            changed = db.execute(
                "UPDATE routes SET eco_demand=?, bus_demand=?, fir_demand=?, "
                "cargo_demand=?, is_owned=1, line_id=? "
                "WHERE hub_iata=? AND dest_iata=?",
                (*new, line_id, hub, iata),
            ).rowcount
            db.commit()
            assert changed, "route row vanished mid-audit"
            updated.append(iata)
            print(f"[{index}/{len(lines)}] {iata}: "
                  f"eco {old.get('eco')} -> {new[0]}", file=sys.stderr)
        except AMAuthError:
            raise
        except Exception as exc:
            failed.append({"iata": iata, "line_id": line_id, "error": str(exc)})
            print(f"[{index}/{len(lines)}] {iata}: FAIL {exc}", file=sys.stderr)
    return {"updated": updated, "failed": failed, "stopped": stopped}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hub", required=True)
    parser.add_argument("--routes", nargs="+")
    parser.add_argument("--country")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--sync-rows", action="store_true",
                        help="add missing destinations + fix country codes first")
    parser.add_argument("--internal", action="store_true",
                        help="refresh OWNED lines instead (one coupon each)")
    parser.add_argument("--apply", action="store_true",
                        help="buy audits and update SQLite (default: preview only)")
    parser.add_argument("--allow-paid", action="store_true",
                        help="confirm external audits may spend cash")
    args = parser.parse_args()

    hub = args.hub.upper().strip()
    db = get_db()
    hub_id = get_player_hub_id(hub)
    client = None
    result = {"hub": hub, "mode": "internal" if args.internal else "external",
              "dry_run": not args.apply, "updated": [], "failed": [],
              "stopped": None}
    rows = []
    if args.sync_rows:
        client = AMClient(AMSession.load())
        result["sync"] = sync_world_rows(db, retry_once(client.world), hub)
    if args.internal:
        if hub_id is None:
            raise SystemExit(f"No player hub id stored for {hub}")
        client = client or AMClient(AMSession.load())
        rows = client.hub_pricing(hub_id)          # free: lines + current audit
        if args.routes:
            wanted = {i.upper() for i in args.routes}
            rows = [ln for ln in rows if (ln.get("aTwoName") or "").upper() in wanted]
        rows = rows[:args.limit] if args.limit is not None else rows
        result["routes"] = [(ln.get("aTwoName") or "").upper() for ln in rows]
    else:
        rows = candidates(db, hub, args.routes, args.country, args.limit)
        result["routes"] = [row["dest_iata"] for row in rows]
    result["candidates"] = len(rows)

    if args.apply and rows:
        if hub_id is None:
            raise SystemExit(f"No player hub id stored for {hub}")
        client = client or AMClient(AMSession.load())
        try:
            coupons, dollars = audit_budget(client, hub_id)
            result.update(free_audits_before=coupons, dollar_before=dollars)
            if args.internal:
                result.update(audit_lines(db, client, hub, hub_id, rows))
            else:
                result.update(audit_routes(
                    db, client, hub, hub_id, rows, allow_paid=args.allow_paid))
            coupons, dollars = audit_budget(client, hub_id)
            result.update(free_audits_after=coupons, dollar_after=dollars)
        finally:
            client.close()
    elif client:
        client.close()

    print(json.dumps(result))
    if result["failed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
