#!/usr/bin/env python3
"""Masstool scraper — fetch live route data (current prices, remaining demand)
from /masstool/pricingAjax/<hub_id>.

Returns a dict keyed by destination IATA so it can be joined with circuit/route data.
"""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import CDP, get_am_tab
from db import get_player_hub_id


LINE_HEADER_RE = re.compile(
    r'<tr class="lines lineId(\d+)"[^>]*?'
    r'data-name="([A-Z]{3})"[^>]*?'
    r'data-fullName="([^"]+)"',
    re.S,
)
PRICE_INPUT_RE = re.compile(
    r'id="defaultPrice(\d+)_(eco|bus|first|cargo)"[^>]*?value="(\d+)"'
)
SIM_PAX_RE = re.compile(
    r'id="simulationPax(\d+)_(eco|bus|first|cargo)"[^>]*?'
    r'data-actualDemand="(\d+)"[^>]*?'
    r'data-chosenDayCarriedPaxValue="(\d+)"[^>]*>\s*([^<]+?)\s*</span>',
    re.S,
)


def parse_masstool(html: str) -> dict[str, dict]:
    """Parse masstool ajax HTML; return {iata: {line_id, price{}, demand{}, carried{}, remaining{}}}."""
    result: dict[str, dict] = {}
    line_to_iata: dict[str, str] = {}

    for m in LINE_HEADER_RE.finditer(html):
        line_id, iata, full = m.groups()
        result[iata] = {
            "line_id": int(line_id),
            "iata": iata,
            "full_name": full,
            "price": {},
            "demand": {},       # actual per-day demand
            "carried": {},      # pax already carried (chosen day)
            "remaining": {},    # demand - carried (chosen day)
        }
        line_to_iata[line_id] = iata

    for m in PRICE_INPUT_RE.finditer(html):
        line_id, cls, value = m.groups()
        iata = line_to_iata.get(line_id)
        if iata:
            result[iata]["price"][cls] = int(value)

    for m in SIM_PAX_RE.finditer(html):
        line_id, cls, actual, carried, rem_text = m.groups()
        iata = line_to_iata.get(line_id)
        if not iata:
            continue
        try:
            rem = int(rem_text.replace(",", "").replace(" ", ""))
        except ValueError:
            rem = max(0, int(actual) - int(carried))
        result[iata]["demand"][cls] = int(actual)
        result[iata]["carried"][cls] = int(carried)
        result[iata]["remaining"][cls] = rem
    return result


def fetch_masstool_hub(cdp: CDP, hub_id: int | str) -> dict[str, dict]:
    """Fetch and parse the masstool pricing page for one hub."""
    js = (
        f"fetch('/masstool/pricingAjax/{hub_id}', "
        f"{{credentials:'include', headers:{{'X-Requested-With':'XMLHttpRequest'}}}})"
        f".then(r=>r.text())"
    )
    html = cdp.eval(js, await_promise=True)
    if not html:
        return {}
    return parse_masstool(html)


def compute_route_summary(route: dict) -> dict:
    """Derive per-route summary numbers from a parsed masstool entry."""
    price = route.get("price") or {}
    carried = route.get("carried") or {}
    remaining = route.get("remaining") or {}
    demand = route.get("demand") or {}
    daily_rev = sum(price.get(c, 0) * carried.get(c, 0) for c in ("eco", "bus", "first", "cargo"))
    return {
        "current_price_eco":   price.get("eco", 0),
        "current_price_bus":   price.get("bus", 0),
        "current_price_first": price.get("first", 0),
        "current_price_cargo": price.get("cargo", 0),
        "remaining_eco":   remaining.get("eco", 0),
        "remaining_bus":   remaining.get("bus", 0),
        "remaining_first": remaining.get("first", 0),
        "remaining_cargo": remaining.get("cargo", 0),
        "carried_eco":   carried.get("eco", 0),
        "carried_bus":   carried.get("bus", 0),
        "carried_first": carried.get("first", 0),
        "carried_cargo": carried.get("cargo", 0),
        "demand_eco":   demand.get("eco", 0),
        "demand_bus":   demand.get("bus", 0),
        "demand_first": demand.get("first", 0),
        "demand_cargo": demand.get("cargo", 0),
        "daily_revenue":  daily_rev,
        "weekly_revenue": daily_rev * 7,
    }


def main():
    import argparse
    p = argparse.ArgumentParser(description="Fetch masstool live data for a hub")
    p.add_argument("hub", help="Hub IATA")
    p.add_argument("--routes", nargs="+", help="Only print these IATAs")
    p.add_argument("--json", action="store_true", help="Output JSON")
    args = p.parse_args()

    hub_id = get_player_hub_id(args.hub)
    if not hub_id:
        sys.exit(f"No player_hubs entry for {args.hub}")

    tab = get_am_tab()
    if not tab:
        sys.exit("No AM tab open in Chrome (--remote-debugging-port=9222)")
    cdp = CDP(tab["webSocketDebuggerUrl"], timeout=60)
    cdp.connect()
    try:
        data = fetch_masstool_hub(cdp, hub_id)
    finally:
        cdp.close()

    if args.routes:
        want = {r.upper() for r in args.routes}
        data = {k: v for k, v in data.items() if k in want}

    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(f"{len(data)} routes for {args.hub} (hub_id={hub_id})")
        for iata in sorted(data):
            r = data[iata]
            s = compute_route_summary(r)
            print(
                f"  {iata}  "
                f"P:{s['current_price_eco']:>5}/{s['current_price_bus']:>5}/"
                f"{s['current_price_first']:>5}/{s['current_price_cargo']:>5}  "
                f"REM:{s['remaining_eco']:>5}/{s['remaining_bus']:>4}/"
                f"{s['remaining_first']:>3}/{s['remaining_cargo']:>4}  "
                f"$/d={s['daily_revenue']:>10,}"
            )


if __name__ == "__main__":
    main()
