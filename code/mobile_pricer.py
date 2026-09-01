#!/usr/bin/env python3
"""
Mobile Pricer — the mobile-API equivalent of `auto_pricer.py`: set route
prices in bulk, without Chrome.

Same contract as the web tool (same DB, same modes, same `--json` document),
but it talks to the mobile JSON API:

  - route data   GET  hub/<hub_id>/lines/pricing   (one call per hub page)
  - write        POST line/price

Why this is the cleaner path, and now the default:

  * The web tool cannot POST the pricing form at all — every scripted route to
    /marketing/pricing/<line_id> answers 204 and silently discards the change,
    so it has to go through the AM+ masstool's bulk endpoint (or a real mouse
    click in a focused window). `line/price` is a plain POST that answers
    `line.updatePrice.success`, and a refusal raises instead of no-op'ing.
  * One request per hub replaces one page fetch per route, plus the regexes
    over AM+ HTML that broke whenever the markup moved.
  * The recommended price arrives already corrected. The web page's displayed
    ideal is wrong for business and first, which is why `auto_pricer` derives
    them as eco*1.33 / eco*2.3; the mobile `audit.price` already equals those
    derived values (verified across hubs), so there is nothing to correct.
  * `lockedUntil` states the 24h cooldown per line up front, so a locked route
    is skipped without spending a request to discover it.

Modes (identical maths to the web tool — the pure helpers are imported from
it, not reimplemented):
    --mode ideal            target = the audit's recommended price (default)
    --mode percent --pct N  target = recommended * N/100
    --mode fill             target = price that drives remaining demand to 0

Usage:
    python3 mobile_pricer.py --hub LAX --dry-run
    python3 mobile_pricer.py --circuit MPM-C003 --mode fill
    python3 mobile_pricer.py --hub LAX --routes PVG SEA --json

Requirements: a valid mobile session (~/.airlines_manager/session.json).
Refresh with tools/mobile-capture/refresh_mobile_session.sh.
"""

import argparse
import datetime as dt
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from auto_pricer import (  # noqa: E402
    CLASS_ORDER, fill_prices, fill_revenue, load_circuit_dest_iatas,
)
from db import get_player_hub_id  # noqa: E402
from masstool import fetch_masstool_hub_mobile  # noqa: E402
from mobile_api import AMClient, AMError, AMSession  # noqa: E402


def is_locked(locked_until: str | None, now: dt.datetime | None = None) -> bool:
    """True while a line is inside its 24h price-change cooldown.

    `lockedUntil` is a naive UTC timestamp; an unparseable one counts as
    unlocked so a format change degrades into "try and let the server refuse"
    rather than "silently price nothing".
    """
    if not locked_until:
        return False
    try:
        until = dt.datetime.strptime(locked_until, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return False
    return until > (now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None))


def target_prices(route: dict, mode: str, pct: float) -> dict | None:
    """Per-class target prices for one route, or None if it can't be priced."""
    ideal = route.get("audit_price") or {}
    if not any(ideal.get(c) for c in CLASS_ORDER):
        return None  # never audited — nothing to aim at
    if mode == "ideal":
        return dict(ideal)
    if mode == "percent":
        return {c: max(1, int(round(ideal[c] * pct / 100.0))) for c in CLASS_ORDER}
    if mode == "fill":
        return fill_prices(ideal, route["price"], route,
                           route.get("audit_demand"))
    raise ValueError(f"unknown mode {mode!r}")


def price_hub(hub_iata: str, *, mode: str = "ideal", pct: float = 100.0,
              only: set | None = None, max_routes: int | None = None,
              skip_unchanged: bool = True, dry_run: bool = True,
              client: AMClient | None = None) -> dict:
    """Price every route at a hub. Returns the `--json` document."""
    hub_id = get_player_hub_id(hub_iata)
    if not hub_id:
        return {"error": f"No player_hubs entry for {hub_iata}", "routes": []}

    own = client is None
    client = client or AMClient(AMSession.load())
    try:
        data = fetch_masstool_hub_mobile(hub_id, client=client)
        results = []
        applied = 0
        for iata in sorted(data):
            if only and iata not in only:
                continue
            if max_routes is not None and len(results) >= max_routes:
                break
            route = data[iata]
            row = {"iata": iata, "line_id": route["line_id"],
                   "current": route["price"]}
            target = target_prices(route, mode, pct)
            if target is None:
                row.update(status="skipped", detail="no audit data")
                results.append(row)
                continue
            row["target"] = target
            if skip_unchanged and all(
                    route["price"][c] == target[c] for c in CLASS_ORDER):
                row.update(status="skipped", detail="unchanged")
                results.append(row)
                continue
            if is_locked(route.get("locked_until")):
                row.update(status="cooldown", detail=route["locked_until"])
                results.append(row)
                continue
            if dry_run:
                row.update(status="dry-run", detail="")
                results.append(row)
                continue
            try:
                client.set_price(route["line_id"], **{c: target[c]
                                                      for c in CLASS_ORDER})
                row.update(status="ok", detail="")
                applied += 1
            except AMError as exc:
                row.update(status="fail", detail=str(exc)[:200])
            results.append(row)

        doc = {"hub": hub_iata.upper(), "hub_id": hub_id, "backend": "mobile",
               "mode": mode, "dry_run": dry_run, "applied": applied,
               "routes": results,
               "counts": {s: sum(1 for r in results if r["status"] == s)
                          for s in {r["status"] for r in results}}}
        if mode == "fill":
            now = tgt = 0.0
            for r in results:
                if "target" not in r:
                    continue
                route = data[r["iata"]]
                a, b = fill_revenue(route["audit_price"], route["price"],
                                    r["target"], route)
                now += a
                tgt += b
            doc["fill_revenue"] = {"current": math.floor(now),
                                   "target": math.floor(tgt)}
        return doc
    finally:
        if own:
            client.close()


def main():
    p = argparse.ArgumentParser(description="Set route prices over the mobile API")
    p.add_argument("--hub", help="Hub IATA")
    p.add_argument("--circuit", help="Only price this circuit's routes")
    p.add_argument("--routes", nargs="+", help="Only price these destination IATAs")
    p.add_argument("--mode", choices=("ideal", "percent", "fill"), default="ideal")
    p.add_argument("--pct", type=float, default=100.0, help="percent mode target")
    p.add_argument("--max", type=int, help="Stop after N routes")
    p.add_argument("--skip-unchanged", action="store_true", default=True,
                   help="Skip lines already at target (the default; kept for "
                        "CLI parity with auto_pricer.py)")
    p.add_argument("--no-skip-unchanged", dest="skip_unchanged",
                   action="store_false",
                   help="POST even when current == target")
    p.add_argument("--dry-run", action="store_true", help="Plan only, no writes")
    p.add_argument("--json", action="store_true", help="Machine-readable output")
    args = p.parse_args()

    only = {r.upper() for r in args.routes} if args.routes else None
    hub = args.hub
    if args.circuit:
        circuit_hub, circuit_routes = load_circuit_dest_iatas(args.circuit)
        if not circuit_routes:
            sys.exit(f"circuit '{args.circuit}' not found in DB")
        hub = hub or circuit_hub
        only = circuit_routes if only is None else (only & circuit_routes)
    if not hub:
        sys.exit("need --hub or --circuit")

    doc = price_hub(hub, mode=args.mode, pct=args.pct, only=only,
                    max_routes=args.max,
                    skip_unchanged=args.skip_unchanged,
                    dry_run=args.dry_run)

    out = sys.stderr if args.json else sys.stdout
    if doc.get("error"):
        print(doc["error"], file=out)
    else:
        print(f"{doc['hub']} — {len(doc['routes'])} routes, mode={doc['mode']}"
              f"{' (dry run)' if doc['dry_run'] else ''}", file=out)
        for r in doc["routes"]:
            tgt = r.get("target")
            shown = ("/".join(str(tgt[c]) for c in CLASS_ORDER) if tgt else "-")
            print(f"  {r['iata']:>4} {r['status']:<9} "
                  f"{'/'.join(str(r['current'][c]) for c in CLASS_ORDER):>24} "
                  f"-> {shown:<24} {r['detail']}", file=out)
        print(f"  {doc['counts']}", file=out)
    if args.json:
        print(json.dumps(doc, indent=2))


if __name__ == "__main__":
    main()
