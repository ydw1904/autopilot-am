#!/usr/bin/env python3
"""Route details — the numbers only the game's own /network/showline page has.

The mobile API covers a line's price, demand and audit, but has **no**
per-line financials: `line/{id}/statistics|stats|finance|accounting|turnover`
all answer "error 99". Turnover, the D-5..today history, the D+5 forecast, the
7-day financial summary, airport taxes, accepted categories, flights per week
and the aircraft actually flying the route exist on one HTML page and nowhere
else, so this is CDP-only by necessity, not by choice.

    python3 code/route_details.py FRA PVG [--json]
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import BASE_URL, CDP, get_am_tab
from db import get_db

CLASSES = ("eco", "bus", "first", "cargo")
# Class rows in #resultTable, in the order the page prints them.
RESULT_ROWS = {"Demand": "demand", "Offer": "offer", "Average price": "price",
               "Ancillary revenue": "ancillary", "Turnover": "turnover"}
# Rows of the AM+ 6-day history table (#table2), same idea.
HISTORY_ROWS = {"Demand": "demand", "Offer": "offer", "Average price": "price",
                "Ticket sales": "ticket_sales", "Cargo": "cargo",
                "Ancillary revenue": "ancillary", "Turnover": "turnover",
                "Cost": "cost", "Result": "result",
                "Cost of incidents": "incidents"}
# The "Financial summary over 7 days" lines, keyed by their visible label.
WEEK_ROWS = {"Turnover": "turnover",
             "including ticket sale turnover": "ticket_turnover",
             "including ancillary revenue": "ancillary",
             "Flight costs": "flight_costs",
             "including fuel costs": "fuel_costs",
             "including airport taxes": "airport_taxes",
             "including other costs": "other_costs",
             "Flight results": "flight_result",
             "Cost of incidents": "incidents",
             "Economy class": "pax_eco", "Business class": "pax_bus",
             "First class": "pax_first", "Cargo": "cargo_tons"}

TAG_RE = re.compile(r"<[^>]+>")
CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
DASH_RE = re.compile(r'<div class="dashMachine[^"]*">\s*<span[^>]*>(.*?)</span>\s*'
                     r'<span[^>]*>(.*?)</span>', re.S)
AIRPORT_RE = re.compile(r'([A-Z]{3})\s*/\s*<img src="/images/flags/(\w+)\.png"[^>]*/>\s*([^<]+)')


def _text(html: str) -> str:
    """Tags out, entities and non-breaking spaces normalised, one space between."""
    plain = TAG_RE.sub(" ", html).replace("&#149;", "").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", plain).strip()


def _number(text):
    """`$17,285,834` / `24 514 Pax` / `- $0` / `97%` -> int, or None if there is none."""
    digits = re.sub(r"[^\d.-]", "", _text(str(text)).replace(" ", ""))
    digits = digits.rstrip(".").lstrip("-") or ""
    if not digits:
        return None
    value = int(float(digits))
    return -value if "-" in _text(str(text)) else value


def _rows(html: str, start: int, end: int = -1) -> list[list[str]]:
    """Cells of every <tr> between `start` and `end` (default: the next </table>)."""
    if start < 0:
        return []
    if end < 0:
        end = html.find("</table>", start)
    return [[_text(cell) for cell in CELL_RE.findall(row)]
            for row in ROW_RE.findall(html[start:end if end > 0 else None])]


def _class_rows(rows, labels) -> dict:
    """Rows whose first cell names a metric -> {metric: {eco, bus, first, cargo}}."""
    out = {}
    for cells in rows:
        key = labels.get(cells[0].rstrip(" :") if cells else "")
        if key and len(cells) >= 5:
            out[key] = dict(zip(CLASSES, (_number(c) for c in cells[1:5])))
    return out


def _series(rows, labels) -> dict:
    """The 6-column history/forecast rows -> {metric: [d-5 … today]}."""
    out = {}
    for cells in rows:
        key = labels.get(cells[0].rstrip(" :") if cells else "")
        if key and len(cells) >= 7:
            out[key] = [_number(c) for c in cells[1:7]]
    return out


def parse_showline(html: str) -> dict:
    """Everything /network/showline/{id} shows, as plain JSON-able values."""
    # box1/box2 (purchase date, aircraft, flights, taxes, distance) sit between
    # the route title and the statistics block, and nowhere else on the page.
    info = _text(html[html.find('<div class="lineTitle"'):html.find('<div id="statistics"')])
    airports = AIRPORT_RE.findall(html)
    result_at = html.find('<table id="resultTable"')
    table2_at = html.find('<div id="table2"')
    week_at = html.find("Financial summary over 7 days")
    fleet_at = html.find('<div class="aircraftListView"')

    # Both days live in the SAME table as two tbodies, so today's rows have to
    # stop where yesterday's begin or the later "Offer" row overwrites it.
    today_at = html.find('id="todayResultsBody"', result_at)
    yesterday_at = html.find('id="yesterdayResultsBody"', result_at)
    per_class = {
        "today": _class_rows(_rows(html, today_at, yesterday_at), RESULT_ROWS),
        "yesterday": _class_rows(_rows(html, yesterday_at), RESULT_ROWS),
    }

    # The five "Turnover / Cost / …" pairs above the class table, in page order.
    headline_at = html.find('<div id="statistics"')
    headline = [(_text(a), _number(b)) for a, b in
                DASH_RE.findall(html[headline_at:result_at])]
    totals = {"today": {}, "yesterday": {}}
    labels = ["turnover", "cost", "ancillary", "flight_profit", "incidents"]
    for index, (when, value) in enumerate(headline[:len(labels) * 2]):
        totals["today" if when.lower() == "today" else "yesterday"][labels[index // 2]] = value

    # History and forecast share one table too, split by the "Forecast" header
    # — and both have a "Turnover :" row, so the split is what keeps the
    # history from being overwritten by the forecast.
    forecast_head = re.search(r">\s*Forecast\s*<", html[table2_at:week_at]) \
        if table2_at > 0 else None
    split_at = table2_at + forecast_head.start() if forecast_head else -1
    dates = re.findall(r"(\d{2}/\d{2}/\d{4})", html[table2_at:week_at]) if table2_at > 0 else []
    history = _series(_rows(html, table2_at, split_at), HISTORY_ROWS)
    forecast = _series(_rows(html, split_at), {"Turnover": "turnover"}).get("turnover")

    week = {}
    for label, value in DASH_RE.findall(html[week_at:fleet_at] if week_at > 0 else ""):
        key = WEEK_ROWS.get(_text(label))
        if key:
            week[key] = _number(value)

    return {
        "route": _text(html[html.find('<div class="lineTitle"'):html.find('<ul id="box1"')]).removeprefix("Route ").strip(),
        "purchased_at": _first(info, r"Purchase date : ([\d/]+ at \d+h\d+)"),
        "aircraft": _first(info, r"Assigned aircraft : (\d+)", int),
        "flights_per_week": _first(info, r"Number of flights per week : (\d+)", int),
        "distance_km": _first(info, r"Distance : ([\d,]+) km", _number),
        "taxes": _first(info, r"Taxes : (\$[\d,]+)", _number),
        "categories": [int(n) for n in re.findall(
            r'alt="cat(\d+)"', html[html.find("Categories accepted"):result_at])],
        "departure": _airport(airports, 0),
        "arrival": _airport(airports, 1),
        "totals": totals,
        "per_class": per_class,
        "history": {"dates": dates[:6], "rows": history},
        "forecast": {"dates": dates[6:12], "turnover": forecast},
        "week": week,
        "aircraft_list": _aircraft(html[fleet_at:]) if fleet_at > 0 else [],
    }


def _first(text, pattern, cast=str):
    match = re.search(pattern, text)
    return cast(match.group(1)) if match else None


def _airport(matches, index):
    if len(matches) <= index:
        return None
    iata, country, name = matches[index]
    return {"iata": iata, "country_code": country, "country": name.strip()}


AIRCRAFT_RE = re.compile(
    r'<div class="aircraftListBox.*?data-url="/aircraft/edit/(\d+)".*?</div>\s*</div>',
    re.S)


def _aircraft(html: str) -> list[dict]:
    """The "Aircraft on this route" boxes — the same figures the game lists."""
    out = []
    for box in re.findall(r'<div class="aircraftListBox.*?(?=<div class="aircraftListBox|\Z)',
                          html, re.S):
        aircraft_id = _first(box, r'/aircraft/(?:edit|show)/(\d+)', int)
        if not aircraft_id:
            continue
        head = box[:box.find('<div class="content"')]
        title = _text(head)
        seats = re.search(r"Seats : (\d+) \((\d+)/(\d+)/(\d+)\)", _text(box))
        out.append({
            "aircraft_id": aircraft_id,
            "model": _first(title, r"^(?:cat\d+ )?([^/]+?) /"),
            "name": (_first(title, r"/ ([^/]+?)(?: In flight)?(?: Aircraft details)?$") or "").rstrip(" -") or None,
            "in_flight": 'alt="In flight"' in head,
            "range_km": _first(_text(box), r"Range : ([\d,]+) km", _number),
            "use_pct": _first(_text(box), r"Use : (\d+)%", int),
            "cargo_t": _first(_text(box), r"Cargo : ([\d,]+) T", _number),
            "seats": {"total": int(seats.group(1)), "eco": int(seats.group(2)),
                      "bus": int(seats.group(3)), "first": int(seats.group(4))}
            if seats else None,
            "hub": _first(_text(box), r"Hub ([A-Z]{3})"),
            "result": _first(_text(box), r"Result : (\$[\d,]+)", _number),
            "wear_pct": _first(_text(box), r"Wear : (\d+) %", int),
            "age": _first(_text(box), r"Age : (\d+/\d+)"),
        })
    return out


def fetch_showline(line_id: int, cdp=None) -> dict:
    """Fetch and parse one route's details page through a logged-in Chrome tab."""
    own = cdp is None
    if own:
        tab = get_am_tab()
        if not tab:
            raise RuntimeError(
                "No AM tab open in Chrome (--remote-debugging-port=9222)")
        cdp = CDP(tab["webSocketDebuggerUrl"], timeout=60)
        cdp.connect()
    try:
        html = cdp.eval(
            f"fetch('{BASE_URL}/network/showline/{int(line_id)}',"
            f"{{credentials:'include'}}).then(r=>r.text())", await_promise=True)
    finally:
        if own:
            cdp.close()
    if not html or "resultTable" not in html:
        raise RuntimeError(f"Route {line_id} details page did not load "
                           "(is the Chrome tab signed in?)")
    return parse_showline(html)


def main():
    import argparse
    import json

    p = argparse.ArgumentParser(description="Scrape one route's details page")
    p.add_argument("hub", help="Hub IATA")
    p.add_argument("dest", help="Destination IATA")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    row = get_db().execute(
        "SELECT line_id FROM routes WHERE hub_iata = ? AND dest_iata = ?",
        (args.hub.upper(), args.dest.upper())).fetchone()
    if not row or not row["line_id"]:
        sys.exit(f"No line_id cached for {args.hub}/{args.dest}")

    data = fetch_showline(row["line_id"])
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(data["route"])
        print(f"  {data['aircraft']} aircraft, {data['flights_per_week']} flights/week, "
              f"taxes ${data['taxes']:,}")
        week = data["week"]
        print(f"  7 days: turnover ${week.get('turnover', 0):,}, "
              f"result ${week.get('flight_result', 0):,}")
        for item in data["aircraft_list"]:
            print(f"  {item['name']:<24} {item['model']:<14} use {item['use_pct']}%  "
                  f"result ${item['result']:,}")


if __name__ == "__main__":
    main()
