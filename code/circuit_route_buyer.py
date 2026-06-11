#!/usr/bin/env python3
"""
Circuit Route Buyer — Buy routes from a circuit plan.

Two flows:

  (A) Country-listing flow [new, default]
      For each route in the circuit:
        - Group by destination country (from `routes.dest_country`)
        - Navigate to /network/newline/<player_hub_id>/<country>
        - Find the .hubListBox card whose IATA matches
        - Click that card's per-route purchase action

  (B) Direct finalize flow [legacy]
        - Navigate per-IATA to /network/newlinefinalize/<player_hub_id>/<iata>
        - Submit form via fetch()

The country-listing flow is preferred because some hubs/countries no longer
expose newlinefinalize directly without going through the listing first.

Usage:
    python3 circuit_route_buyer.py --circuit MPM-C007 --hub-id 10127635 --dry-run
    python3 circuit_route_buyer.py --circuit MPM-C007 --hub-id 10127635
    python3 circuit_route_buyer.py --hub-id 10087991 DSS NKC LOS ABV    # raw IATAs
    python3 circuit_route_buyer.py --circuit MPM-C007 --hub-id 10127635 --legacy

Requirements: Chrome with --remote-debugging-port=9222 --remote-allow-origins=*
"""

import argparse, json, os, re, sqlite3, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import CDP, get_am_tab, connect_cdp, BASE_URL
from db import DB
from aircraft_buyer import get_balance  # noqa: E402
from db import mark_route_owned  # noqa: E402



# ── DB ──────────────────────────────────────────────────────────────────────

def load_circuit_routes(db, name):
    """Return list[(iata, country_lower, name)]. country may be None."""
    name = name.upper()
    hub_row = db.execute("SELECT hub_iata FROM circuits WHERE name=?", (name,)).fetchone()
    if not hub_row:
        return None, None
    hub_iata = hub_row[0]
    rows = db.execute("""
        SELECT cr.dest_iata, r.dest_country, cr.dest_name
        FROM circuit_routes cr
        LEFT JOIN routes r ON r.hub_iata=? AND r.dest_iata=cr.dest_iata
        WHERE cr.circuit_name=?
        ORDER BY cr.route_order
    """, (hub_iata, name)).fetchall()
    out = []
    for iata, country, dest_name in rows:
        country_l = country.lower() if country else None
        out.append((iata.upper(), country_l, dest_name))
    return hub_iata, out


# ── Country-listing flow ────────────────────────────────────────────────────

# JS arrow function: resolve a .hubListBox card's IATA from data attributes,
# finalize/newline hrefs, or a loose innerText match (multi-signal, in that order).
CARD_IATA_JS = r"""(c) => {
    const attr = c.getAttribute('data-iata') || c.getAttribute('data-iatacode');
    if (attr) return attr.toUpperCase();
    for (const a of c.querySelectorAll('a[href]')) {
        const h = a.getAttribute('href') || '';
        const m = h.match(/\/(?:newlinefinalize|newline)\/\d+\/([a-zA-Z]{3})(?:\/|$|\?)/);
        if (m) return m[1].toUpperCase();
    }
    for (const el of c.querySelectorAll('[data-iata],[data-iatacode]')) {
        const v = el.getAttribute('data-iata') || el.getAttribute('data-iatacode');
        if (v && v.length === 3) return v.toUpperCase();
    }
    const m2 = (c.innerText || '').match(/\b([A-Z]{3})\b/);
    return m2 ? m2[1] : null;
}"""

CARD_RECON_JS = r"""
((wanted) => {
    // Identify each .hubListBox's IATA by checking several places:
    //   1. data-iata / data-iatacode attribute
    //   2. anchor href ending in /<iata> (newlinefinalize or newline)
    //   3. innerText regex (loose): standalone uppercase 3-letter token
    const cards = document.querySelectorAll('.hubListBox');
    const out = { totalCards: cards.length, matches: [], unmatchedSample: null };
    const want = new Set(wanted.map(s => s.toUpperCase()));

    const cardIata = __CARDIATA__;

    out.allDetected = [];
    let firstUnmatched = null;
    for (let i = 0; i < cards.length; i++) {
        const c = cards[i];
        const iata = cardIata(c);
        out.allDetected.push(iata);
        if (!want.has(iata)) {
            if (!firstUnmatched) firstUnmatched = { index: i, detectedIata: iata, outerHTMLHead: c.outerHTML.slice(0, 800) };
            continue;
        }
        const anchor = c.querySelector('a[href*="newlinefinalize"], a[href*="/newline/"]');
        const buyBtn = c.querySelector('.purchaseButton, .openLineButton, .openLine, [data-action="buy"]');
        const massSel = c.querySelector('.massSelect, input.massSelect, input[type=checkbox]');
        const onclick = c.getAttribute('onclick') || '';
        const txt = c.innerText || '';
        const price = (txt.match(/Gross price\s*:\s*\$\s*([\d,]+)/) || [,null])[1];
        out.matches.push({
            iata: iata,
            cardIndex: i,
            href: anchor ? anchor.getAttribute('href') : null,
            buyBtnSel: buyBtn ? (buyBtn.className || buyBtn.tagName) : null,
            buyBtnText: buyBtn ? (buyBtn.textContent || '').trim().slice(0, 40) : null,
            massSelTag: massSel ? massSel.tagName + '.' + (massSel.className || '') : null,
            cardOnclick: onclick.slice(0, 120),
            price: price,
            outerHTMLHead: c.outerHTML.slice(0, 600),
        });
    }
    if (!out.matches.length) out.unmatchedSample = firstUnmatched;
    return JSON.stringify(out);
})(__WANTED__)
"""


def wait_for_listing(cdp, country, timeout=20.0):
    """Wait until location.pathname ends with /<country> AND cards rendered."""
    needle = f"/network/newline/".lower()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.4)
        state = cdp.eval_json("""(() => ({
            path: window.location.pathname,
            cards: document.querySelectorAll('.hubListBox').length,
            ready: document.readyState
        }))()""")
        if not state: continue
        path = (state.get("path") or "").lower()
        if needle in path and path.endswith(f"/{country.lower()}") \
                and state.get("cards", 0) > 0 \
                and state.get("ready") == "complete":
            return True
    return False


def recon_country(cdp, hub_id, country, wanted_iatas):
    url = f"{BASE_URL}/network/newline/{hub_id}/{country}"
    cdp.navigate(url)
    if not wait_for_listing(cdp, country):
        return {"error": "navigation_timeout", "url": url}
    js = (CARD_RECON_JS.replace("__CARDIATA__", CARD_IATA_JS)
          .replace("__WANTED__", json.dumps(wanted_iatas)))
    raw = cdp.eval(js)
    if not raw:
        return {"error": "no_response", "url": url}
    try:
        return {"url": url, **json.loads(raw)}
    except (json.JSONDecodeError, TypeError):
        return {"error": "parse_fail", "url": url, "raw": str(raw)[:300]}


def find_country_card(cdp, iata):
    """On a loaded country listing page, return {href, price} for an IATA card."""
    iata = iata.upper().strip()
    js = """(() => {
        const want = '%s';
        const cardIata = %s;
        for (const c of document.querySelectorAll('.hubListBox')) {
            if (cardIata(c) === want) {
                const a = c.querySelector('a[href*="newlinefinalize"], a[href*="/newline/"]');
                const txt = c.innerText || '';
                const price = (txt.match(/Gross price\\s*:\\s*\\$\\s*([\\d,]+)/) || [null,null])[1];
                return JSON.stringify({href: a ? a.getAttribute('href') : null, price: price});
            }
        }
        return JSON.stringify(null);
    })()""" % (iata, CARD_IATA_JS)
    return cdp.eval_json(js)


def click_buy_via_country(cdp, hub_id, country, iata):
    """Trigger purchase from the country listing page for a single IATA.
    Returns (success: bool|None, message: str). None = unknown, treat as soft-fail.

    If the card has a finalize/newline href, submit that; otherwise fall back
    to the legacy direct-finalize URL.
    """
    cdp.navigate(f"{BASE_URL}/network/newline/{hub_id}/{country}")
    wait_for_listing(cdp, country)
    card = find_country_card(cdp, iata) or {}
    href = card.get("href")
    if href:
        target = href if href.startswith("http") else f"{BASE_URL}{href}"
    else:
        target = f"{BASE_URL}/network/newlinefinalize/{hub_id}/{iata.lower()}"
    return finalize_purchase(cdp, target)


def finalize_purchase(cdp, url):
    """Navigate to a finalize page and submit the purchase form natively.

    Uses form.submit() instead of fetch() — the game server silently
    rejects fetch()-based POSTs (returns 200 but doesn't apply the purchase).
    """
    cdp.navigate(url)
    time.sleep(3)
    form_exists = cdp.eval('!!document.getElementById("linePurchaseForm")')
    if not form_exists:
        return False, "no_form"
    page_text = (cdp.eval('document.body.innerText') or '').lower()
    if 'successfully added' in page_text:
        return True, "already_purchased"
    cdp.eval('window.onbeforeunload = null; $(window).off("beforeunload"); document.getElementById("linePurchaseForm").submit()')
    time.sleep(4)
    final_url = (cdp.eval('window.location.href') or '').lower()
    final_text = (cdp.eval('document.body.innerText') or '').lower()
    if 'successfully added' in final_text:
        return True, "ok"
    if 'newlinefinalize' in final_url:
        return False, "still_on_finalize (rejected)"
    if '/network' in final_url:
        return True, "ok"
    return None, f"unknown url={final_url}"


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Buy circuit routes via Chrome CDP")
    p.add_argument("iatas", nargs="*", metavar="IATA", help="Airport IATAs (optional if --circuit)")
    p.add_argument("--circuit", help="Circuit name (e.g. MPM-C007) — loads routes from DB")
    p.add_argument("--hub-id", dest="hub_id", required=False, default=None,
                   help="Player's hub ID from AM URL (e.g. 10127635 for MPM)")
    p.add_argument("--hub", dest="hub_id_legacy", default=None,
                   help="Alias for --hub-id (back-compat with old GUI)")
    p.add_argument("--dry-run", action="store_true", help="Recon only — dump card structure, no purchase")
    p.add_argument("--legacy", action="store_true",
                   help="Skip country-listing flow, go straight to /newlinefinalize per IATA")
    args = p.parse_args()

    hub_id = args.hub_id or args.hub_id_legacy
    if not hub_id:
        print("ERROR: --hub-id required (your player hub_id from AM URL).", file=sys.stderr)
        sys.exit(2)

    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    routes = []  # list of (iata, country_lower, dest_name)
    hub_iata = None

    if args.circuit:
        hub_iata, db_routes = load_circuit_routes(db, args.circuit)
        if not db_routes:
            print(f"ERROR: circuit {args.circuit} not found.", file=sys.stderr)
            sys.exit(1)
        print(f"Circuit {args.circuit}: hub={hub_iata}, {len(db_routes)} routes")
        routes = db_routes
        # Warn about missing countries
        missing = [r for r in routes if not r[1]]
        if missing:
            print(f"  WARNING: {len(missing)} route(s) have no country in DB: "
                  f"{[r[0] for r in missing]}")
    elif args.iatas:
        for iata in args.iatas:
            routes.append((iata.upper(), None, None))
        # Try to resolve hub_iata from hub_id
        row = db.execute("SELECT hub_iata FROM player_hubs WHERE hub_id=?", (hub_id,)).fetchone()
        if row:
            hub_iata = row["hub_iata"]
            print(f"Buying raw IATAs for hub {hub_iata} (id {hub_id})")
        else:
            print(f"WARNING: hub_id {hub_id} not found in player_hubs table. DB ownership won't be updated.")
    else:
        p.error("provide --circuit NAME or positional IATAs")

    print(f"Mode: {'DRY-RUN (recon)' if args.dry_run else ('LEGACY' if args.legacy else 'BUY')}")
    print(f"Hub id: {hub_id}")
    print()

    print("Connecting to Chrome…")
    cdp = connect_cdp()

    balance_before = get_balance(cdp)
    if balance_before:
        print(f"Balance: ${balance_before:,.0f}\n")

    # Group by country (None → unknown bucket)
    by_country = {}
    for iata, country, _name in routes:
        by_country.setdefault(country, []).append(iata)

    ok, fail, skip = [], [], []

    if args.legacy:
        for iata, _country, _name in routes:
            url = f"{BASE_URL}/network/newlinefinalize/{hub_id}/{iata.lower()}"
            print(f"  [{iata}] legacy → {url}")
            if args.dry_run:
                continue
            success, msg = finalize_purchase(cdp, url)
            tag = "OK " if success else ("FAIL" if success is False else "SKIP")
            print(f"        {tag}: {msg}")
            if success and hub_iata:
                mark_route_owned(hub_iata, iata)
            (ok if success else (skip if success is None else fail)).append((iata, "???"))
            time.sleep(1)
    else:
        for country, ilist in by_country.items():
            print(f"── country: {country or '???'}  ({len(ilist)} IATA: {' '.join(ilist)})")
            if not country:
                print("   skipping — no country code in DB. Use --legacy or fix routes table.")
                skip.extend(ilist)
                continue

            recon = recon_country(cdp, hub_id, country, ilist)
            url = recon.get("url", "?")
            print(f"   url: {url}")
            if "error" in recon:
                print(f"   ERROR: {recon['error']}")
                fail.extend(ilist)
                continue
            print(f"   listing has {recon.get('totalCards', '?')} cards")
            all_det = recon.get("allDetected") or []
            print(f"   page IATAs: {' '.join(str(x) for x in all_det)}")
            if not recon.get("matches") and recon.get("unmatchedSample"):
                s = recon["unmatchedSample"]
                print(f"   no target matched. First card detected as {s.get('detectedIata')!r}")
                print(f"   first card outerHTML head:\n{s.get('outerHTMLHead', '')}\n")
            found = {m["iata"]: m for m in recon.get("matches", [])}
            for iata in ilist:
                m = found.get(iata)
                if not m:
                    print(f"   [{iata}] NOT FOUND on country page")
                    fail.append(iata)
                    continue
                print(f"   [{iata}] card#{m['cardIndex']}  price=${m.get('price') or '?'}")
                print(f"           href     : {m.get('href')}")
                print(f"           buyBtn   : {m.get('buyBtnSel')!r} txt={m.get('buyBtnText')!r}")
                print(f"           massSel  : {m.get('massSelTag')!r}")
                print(f"           onclick  : {m.get('cardOnclick')!r}")
                if args.dry_run:
                    # Dump first 500 chars of outerHTML for the first IATA only (noise control)
                    if iata == ilist[0]:
                        print(f"           outerHTML head:\n{m.get('outerHTMLHead', '')}\n")
                    continue
                success, msg = click_buy_via_country(cdp, hub_id, country, iata)
                tag = "OK " if success else ("FAIL" if success is False else "SKIP")
                price_str = f" (${m.get('price')})" if m.get('price') else ""
                print(f"           {tag}: {msg}{price_str}")
                if success:
                    mark_route_owned(hub_iata, iata)
                (ok if success else (skip if success is None else fail)).append((iata, m.get('price')))
                time.sleep(1)

    if not args.dry_run:
        balance_after = get_balance(cdp)
        print(f"\n{'─' * 50}")
        print(f"  Bought: {len(ok)}  Skipped: {len(skip)}  Failed: {len(fail)}")
        if ok:   print(f"  OK:   {' '.join(f'{i}(${p})' for i,p in ok)}")
        if skip: print(f"  SKIP: {' '.join(str(x) for x in skip)}")
        if fail: print(f"  FAIL: {' '.join(str(x) for x in fail)}")
        if balance_before and balance_after:
            print(f"  Balance: ${balance_before:,.0f} → ${balance_after:,.0f} "
                  f"(spent ${balance_before - balance_after:,.0f})")

    cdp.close()
    sys.exit(0 if not fail else 2)


if __name__ == "__main__":
    main()
