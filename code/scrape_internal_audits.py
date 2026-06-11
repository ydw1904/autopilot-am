#!/usr/bin/env python3
"""
Internal Audit Scraper — refresh demand for owned routes via
/marketing/pricing/{line_id} (free for owned lines, no audit coupon spent).

Snapshots old values into `routes_demand_snapshot` before overwriting
`routes.eco/bus/fir/cargo_demand` with the audit demand reported on the
per-route pricing page.

Usage:
    python3 scrape_internal_audits.py --hub MPM
    python3 scrape_internal_audits.py --hub MPM --dry-run
"""

import os
import re
import sys
import time
import sqlite3
import argparse
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import CDP, get_am_tab
from db import DB


AUDIT_RE = re.compile(
    r"Ideal\s*(?:ticket\s*)?price[^$]*\$([\d,\s]+)[\s\S]*?"
    r"Demand\s*:\s*([\d\s]+)\s*(?:Pax|T)",
    re.IGNORECASE,
)


def ensure_snapshot_table(db):
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS routes_demand_snapshot (
            hub_iata     TEXT NOT NULL,
            dest_iata    TEXT NOT NULL,
            line_id      INTEGER,
            snapshot_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            old_eco      INTEGER, old_bus   INTEGER,
            old_fir      INTEGER, old_cargo INTEGER,
            new_eco      INTEGER, new_bus   INTEGER,
            new_fir      INTEGER, new_cargo INTEGER
        )
        """
    )
    db.commit()


def parse_audit(text):
    """Return dict {eco,bus,fir,cargo} of audit-demand ints, or None."""
    # The audit block is the FIRST set of four (Economy/Business/First/Cargo)
    # before the "INFORMATION ABOUT THE ROUTE" header (current-price block).
    cutoff = text.find("INFORMATION ABOUT THE ROUTE")
    block = text[:cutoff] if cutoff > 0 else text

    out = {}
    for cls, key in [
        ("Economy class", "eco"),
        ("Business class", "bus"),
        ("First class", "fir"),
        ("Cargo", "cargo"),
    ]:
        i = block.find(cls)
        if i < 0:
            return None
        seg = block[i : i + 400]
        m = re.search(r"Demand\s*:\s*([\d\s]+)", seg)
        if not m:
            return None
        out[key] = int(m.group(1).replace(" ", "").replace(",", ""))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, help="cap routes processed (debug)")
    ap.add_argument("--sleep", type=float, default=1.5,
                    help="seconds between page loads")
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    ensure_snapshot_table(conn)

    rows = conn.execute(
        "SELECT dest_iata, line_id, eco_demand, bus_demand, fir_demand, cargo_demand "
        "FROM routes WHERE hub_iata=? AND is_owned=1 AND line_id IS NOT NULL "
        "ORDER BY dest_iata",
        (args.hub.upper(),),
    ).fetchall()
    if args.limit:
        rows = rows[: args.limit]
    print(f"Found {len(rows)} owned routes with line_id for {args.hub}")

    tab = get_am_tab()
    if not tab:
        sys.exit("No AM tab open in Chrome (need --remote-debugging-port=9222)")
    cdp = CDP(tab["webSocketDebuggerUrl"], timeout=30)
    cdp.connect()

    snapshot_at = None
    deltas = []
    failed = []
    for i, r in enumerate(rows, 1):
        url = f"https://www.airlines-manager.com/marketing/pricing/{r['line_id']}"
        cdp.navigate(url)
        time.sleep(args.sleep)
        text = cdp.eval("document.body ? document.body.innerText : ''") or ""
        new = parse_audit(text)
        if not new:
            failed.append(r["dest_iata"])
            print(f"  [{i:2}/{len(rows)}] {r['dest_iata']:>3}  FAIL (no parse)")
            continue

        old = {
            "eco":   r["eco_demand"]   or 0,
            "bus":   r["bus_demand"]   or 0,
            "fir":   r["fir_demand"]   or 0,
            "cargo": r["cargo_demand"] or 0,
        }
        d_eco = new["eco"] - old["eco"]
        pct = (d_eco / old["eco"] * 100) if old["eco"] else 0
        print(
            f"  [{i:2}/{len(rows)}] {r['dest_iata']:>3}  "
            f"eco {old['eco']:>5} → {new['eco']:>5} ({d_eco:+5d}, {pct:+5.1f}%)  "
            f"bus {old['bus']:>4} → {new['bus']:>4}  "
            f"fir {old['fir']:>3} → {new['fir']:>3}  "
            f"cargo {old['cargo']:>4} → {new['cargo']:>4}"
        )
        deltas.append((r["dest_iata"], r["line_id"], old, new))

        if not args.dry_run:
            conn.execute(
                "INSERT INTO routes_demand_snapshot "
                "(hub_iata, dest_iata, line_id, "
                " old_eco, old_bus, old_fir, old_cargo, "
                " new_eco, new_bus, new_fir, new_cargo) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (args.hub.upper(), r["dest_iata"], r["line_id"],
                 old["eco"], old["bus"], old["fir"], old["cargo"],
                 new["eco"], new["bus"], new["fir"], new["cargo"]),
            )
            conn.execute(
                "UPDATE routes SET eco_demand=?, bus_demand=?, "
                "fir_demand=?, cargo_demand=? "
                "WHERE hub_iata=? AND dest_iata=?",
                (new["eco"], new["bus"], new["fir"], new["cargo"],
                 args.hub.upper(), r["dest_iata"]),
            )

    if not args.dry_run:
        conn.commit()

    print()
    print(f"Updated: {len(deltas)}  Failed: {len(failed)}")
    if failed:
        print(f"  failed iatas: {' '.join(failed)}")

    if deltas:
        eco_pcts = []
        for _, _, o, n in deltas:
            if o["eco"]:
                eco_pcts.append((n["eco"] - o["eco"]) / o["eco"] * 100)
        if eco_pcts:
            eco_pcts.sort()
            mid = eco_pcts[len(eco_pcts) // 2]
            print(f"  eco demand drift — median {mid:+.1f}%  "
                  f"min {eco_pcts[0]:+.1f}%  max {eco_pcts[-1]:+.1f}%")


if __name__ == "__main__":
    main()
