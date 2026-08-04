#!/usr/bin/env python3
"""
Alliance donator -- max out the daily treasury donation via Chrome CDP.

The donate box at the bottom of /alliance/profile is a jQuery-UI slider whose
Validate -> Confirm pair POSTs `donation=<new dollars>` to /alliance/donate.
Dragging a slider through CDP is brittle, so this reads exactly the state the
page's own click handler reads and posts the same request:

  #alliance-slider    data-airline-money      cash on hand
                      data-donation-profile   donationMax, airlineDonations, ...
  #donation-validation data-url               the POST target

Amount = donationMax - airlineDonations (what the slider's own max allows),
capped by cash on hand minus --reserve. `donationMax` is the per-day ceiling,
so this is idempotent: run it twice and the second run donates 0.

The airline pays the full amount; the treasury receives it less `dollarTax`.

Requirements:
  - Chrome with --remote-debugging-port=9222 (code/launch_chrome.sh)
  - a logged-in airlines-manager.com tab in that browser

Usage:
  python3 alliance_donator.py --dry-run
  python3 alliance_donator.py
  python3 alliance_donator.py --reserve 5000000000 --json
"""

import argparse
import contextlib
import json
import sys

from cdp import CDP, ensure_am_tab, js_args

PROFILE_URL = "https://www.airlines-manager.com/alliance/profile"


def read_state(cdp) -> dict:
    """The donation parameters the page hands its own handler."""
    raw = cdp.eval("""(() => {
        const s = document.querySelector('#alliance-slider');
        const v = document.querySelector('#donation-validation');
        if (!s) return JSON.stringify({error: 'no donate box on page'});
        return JSON.stringify({
            money: parseInt(s.getAttribute('data-airline-money') || '0', 10),
            profile: JSON.parse(s.getAttribute('data-donation-profile') || '{}'),
            post_url: v ? v.getAttribute('data-url') : null,
        });
    })()""")
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {"error": f"could not read donate box (got {raw!r})"}


def plan(state: dict, reserve: int = 0) -> dict:
    """How much to donate now, and why that number."""
    profile = state.get("profile") or {}
    cap = int(profile.get("donationMax", 0) or 0)
    done = int(profile.get("airlineDonations", 0) or 0)
    money = int(state.get("money", 0) or 0)
    spendable = max(0, money - max(0, reserve))
    amount = max(0, min(cap - done, spendable))
    return {"amount": amount, "daily_cap": cap, "already_donated": done,
            "money": money, "reserve": reserve,
            "tax_rate": profile.get("dollarTax"),
            "to_treasury": int(amount * (1 - float(profile.get("dollarTax", 0) or 0)))}


def post_donation(cdp, url: str, amount: int) -> dict:
    """Fire the same XHR the Confirm button fires, from the page's own origin."""
    raw = cdp.eval(f"""(async (URL, AMOUNT) => {{
        const r = await fetch(URL, {{
            method: 'POST',
            credentials: 'same-origin',
            headers: {{
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'X-Requested-With': 'XMLHttpRequest',
            }},
            body: 'donation=' + AMOUNT,
        }});
        const t = await r.text();
        let body = null;
        try {{ body = JSON.parse(t); }} catch (e) {{ body = {{raw: t.slice(0, 300)}}; }}
        return JSON.stringify({{status: r.status, body: body}});
    }})({js_args(url, int(amount))})""", await_promise=True)
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {"status": None, "body": {"raw": str(raw)[:300]}}


def run(dry_run: bool = True, reserve: int = 0) -> dict:
    """Donate the daily max. Returns a result dict (the routine calls this)."""
    # Starts Chrome if it is not up. connect_cdp() is not used here because it
    # sys.exit()s when there is no tab, which would take the whole daily
    # routine down instead of failing this one task.
    tab = ensure_am_tab()
    if not tab:
        return {"ok": False, "error": "could not get a Chrome CDP tab",
                "hint": "Check that code/launch_chrome.sh runs and port 9222 is free."}
    try:
        cdp = CDP(tab["webSocketDebuggerUrl"])
        cdp.connect()
    except Exception as e:
        return {"ok": False, "error": f"could not attach to the AM tab: {e}"}
    try:
        cdp.navigate_and_wait(PROFILE_URL,
                              "!!document.querySelector('#alliance-slider') "
                              "|| /\\/login/.test(location.pathname)", timeout=25)
        if cdp.eval("/\\/login/.test(location.pathname)"):
            return {"ok": False, "error": "browser session is not logged in",
                    "hint": "Log in to airlines-manager.com in the CDP Chrome window."}

        state = read_state(cdp)
        if state.get("error"):
            return {"ok": False, **state}
        p = plan(state, reserve)
        if p["amount"] <= 0:
            note = ("daily cap already donated" if p["already_donated"] >= p["daily_cap"]
                    else "not enough cash above the reserve")
            return {"ok": True, "donated": 0, "note": note, **p}
        if dry_run:
            return {"ok": True, "dry_run": True, "would_donate": p["amount"], **p}

        url = state.get("post_url") or "/alliance/donate"
        resp = post_donation(cdp, url, p["amount"])
        body = resp.get("body") or {}
        after = int((body.get("donationProfile") or {}).get("airlineDonations", -1))
        ok = resp.get("status") == 200 and after == p["already_donated"] + p["amount"]
        return {"ok": bool(ok), "donated": p["amount"] if ok else 0,
                "message": body.get("message"), "http_status": resp.get("status"),
                "donated_today_after": after if after >= 0 else None,
                "airline_dollars_after": body.get("airlineDollars"),
                "alliance_dollars_after": body.get("allianceDollars"),
                **{k: p[k] for k in ("daily_cap", "already_donated", "to_treasury")}}
    finally:
        with contextlib.suppress(Exception):
            cdp.close()


def main():
    p = argparse.ArgumentParser(
        description="Donate the daily max to the alliance treasury",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--reserve", type=int, default=0,
                   help="dollars to keep on hand (donate only above this)")
    p.add_argument("--json", action="store_true",
                   help="print the result document instead of a human report")
    args = p.parse_args()

    result = run(dry_run=args.dry_run, reserve=args.reserve)
    if args.json:
        json.dump(result, sys.stdout, indent=2)
        print()
    elif result.get("ok"):
        if result.get("dry_run"):
            print(f"DRY RUN: would donate ${result['would_donate']:,} "
                  f"(cap ${result['daily_cap']:,}, already ${result['already_donated']:,})")
        elif result.get("donated"):
            print(f"Donated ${result['donated']:,} "
                  f"(${result.get('to_treasury', 0):,} to the treasury after tax)")
            print(f"  {result.get('message') or ''}".rstrip())
        else:
            print(f"Nothing to donate: {result.get('note')}")
    else:
        print(f"FAILED: {result.get('error') or result}", file=sys.stderr)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
