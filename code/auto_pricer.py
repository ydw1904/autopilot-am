#!/usr/bin/env python3
"""
Auto Pricer — set route prices to corrected ideal prices in bulk.

The game's displayed "Ideal price" is correct for eco and cargo, but WRONG
for business and first class.  The correct bus/fir prices are derived from
the eco ideal:

    bus = floor(eco * 1.33)
    fir = floor(eco * 2.3)

Flow:
  1. Walk /marketing/pricing/?airport=<id>&page=N to collect line_ids.
  2. For each line_id, fetch /marketing/pricing/<line_id>:
       - parse ideal eco + cargo prices (trusted)
       - parse current prices for all classes
       - derive corrected bus/fir from eco ideal
  3. Submit via a REAL click in the tab (see submit_prices_native): navigate
     to the pricing page, fill the four line[price*] inputs, and dispatch a
     CDP mouse click on the "Confirm these prices" submit button.

Why not POST?  fetch()-POSTs are silently rejected by the server (returns
200/204 but the price never changes), and form.submit() omits the submit
button's name so the server can't tell "Confirm these prices" from "Perform
a simulation".  Only a genuine click works (verified live 2026-07-14).

The game enforces a 24h cooldown per line between price changes; pages in
cooldown render without the form and are reported as skipped, not failed.

Modes:
  --mode ideal           target = corrected ideal price (default)
  --mode percent --pct N target = corrected ideal * N/100
  --mode raw-ideal       target = game's displayed ideal (uncorrected)

Filters:
  --airport <id>         pricing dropdown's internal id (default 0 = all hubs)
  --hub <IATA>           resolve hub IATA -> airport id from dropdown
  --circuit <name>       only price routes belonging to this circuit
  --routes <IATA ...>    only price these specific routes
  --max N                stop after N routes (for testing)
  --skip-unchanged       skip POST if current == target for all classes
  --dry-run              do not POST; print plan

All HTTP goes via fetch() inside the open AM tab so cookies/session are preserved.

Requires Chrome with --remote-debugging-port=9222 and an AM tab open.
"""

import argparse, contextlib, json, math, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import CDP, get_am_tab, BASE_URL, wait_for_js  # noqa: E402
from db import get_db  # noqa: E402

PRICE_RE_IDEAL_ALL = re.compile(
    r"Ideal (?:ticket )?price(?:/Tonne)?\s*:\s*[^$]*?\$([\d,]+)",
    re.DOTALL | re.IGNORECASE,
)
PRICE_RE_CURRENT_ALL = re.compile(
    r"Current (?:ticket )?price(?:/Tonne)?\s*:\s*[^$]*?\$([\d,]+)",
    re.DOTALL | re.IGNORECASE,
)
CLASS_ORDER = ("eco", "bus", "first", "cargo")
TOKEN_RE  = re.compile(r'name="line\[_token\]"\s+value="([^"]+)"')
LINEID_RE = re.compile(r'/marketing/pricing/(\d+)"')

DEST_FROM_DETAIL = re.compile(
    r'(?:arrival|destination|to)\s*(?::\s*)?([A-Z]{3})\b'
    r'|>\s*([A-Z]{3})\s*[-<\s]'
    r'|\((?:([A-Z]{3}))\)'
    r'|\b([A-Z]{3})\s*-\s*(?:International|Airport|Airfield)',
    re.IGNORECASE,
)
TITLE_IATA_RE = re.compile(
    r'(?:→|➔|->|>\s*|to\s+)'
    r'(?:.*?\s)?'
    r'\(([A-Z]{3})\)'
    r'|'
    r'(?:→|➔|->|>\s*)'
    r'\s*([A-Z]{3})\s*(?:[-–—]|$)',
    re.IGNORECASE,
)


def correct_ideal_prices(ideal):
    """Return corrected ideal prices.  Eco and cargo are trusted from the
    game page; bus and fir are derived from eco to match the actual
    zero-demand price:

        bus = floor(eco * 1.33)
        fir = floor(eco * 2.3)
    """
    eco = ideal["eco"]
    return {
        "eco":   eco,
        "bus":   math.floor(eco * 1.33),
        "first": math.floor(eco * 2.3),
        "cargo": ideal["cargo"],
    }


def fetch_text(cdp, path, retries=4):
    # Post-change pages often return an empty body to fetch() for a while —
    # treat empties as transient and retry with backoff.
    js = (f"fetch({json.dumps(path)}, {{credentials:'include', cache:'no-store'}})"
          f".then(r => r.text())")
    for attempt in range(retries):
        out = cdp.eval(js, await_promise=True)
        if out:
            return out
        time.sleep(0.5 * (attempt + 1))
    return None


COOLDOWN_TEXT = "24 hours is required between each price change"
# Raw HTML carries the cooldown expiry as a unix epoch ("Remaining time :
# 1784141738"); the page's JS renders it into a live countdown.
TIME_REMAIN_RE = re.compile(r"Remaining time\s*:\s*(?:<[^>]*>|\s)*(\d+)",
                            re.IGNORECASE)


def cooldown_remaining(html):
    """If the page shows the 24h price-change cooldown, return the remaining
    time as 'XhYYm' (or 'unknown'); otherwise None."""
    if not html:
        return None
    idx = html.find(COOLDOWN_TEXT)
    if idx < 0:
        return None
    m = TIME_REMAIN_RE.search(html[idx:idx + 1000])
    if not m:
        return "unknown"
    val = int(m.group(1))
    if val > 1e9:  # epoch expiry timestamp
        val = max(0, val - int(time.time()))
    return f"{val // 3600}h{(val % 3600) // 60:02d}m"


def read_current_prices_rendered(cdp):
    """Read 'Current ... price' lines from the rendered page's innerText."""
    text = cdp.eval("document.body ? document.body.innerText : ''") or ""
    vals = [int(s.replace(",", "")) for s in PRICE_RE_CURRENT_ALL.findall(text)]
    if len(vals) < 4:
        return None
    return dict(zip(CLASS_ORDER, vals))


def submit_prices_native(cdp, line_id, targets):
    """Set prices on /marketing/pricing/<line_id> via a real in-tab click.

    Navigates the tab to the pricing page, waits for BOTH the URL to settle
    on this line_id AND the price form to exist (submitting early fires
    against the still-loaded previous page and stamps prices onto the wrong
    line), fills the four line[price*] inputs with input+change events, then
    clicks the "Confirm these prices" submit button with a genuine CDP
    Input.dispatchMouseEvent pair at its bounding-rect center.

    Returns (status, detail) where status is one of:
      'ok'       — page verified Current == target on all four classes
      'clicked'  — click sent but rendered verification unavailable
      'cooldown' — form absent, 24h cooldown active (detail = remaining)
      'fail'     — anything else (detail = reason)
    """
    cdp.navigate(f"{BASE_URL}/marketing/pricing/{line_id}")
    ready = (
        f"location.href.endsWith('/pricing/{line_id}') && "
        f"!!document.querySelector('input[name=\"line[priceEco]\"]') ? 'yes' : ''"
    )
    if not wait_for_js(cdp, ready, timeout=20):
        remaining = cooldown_remaining(
            fetch_text(cdp, f"/marketing/pricing/{line_id}", retries=2))
        if remaining:
            return "cooldown", remaining
        return "fail", "pricing form did not appear"

    fill_js = """((() => {
        const vals = %s;
        for (const [name, v] of Object.entries(vals)) {
            const inp = document.querySelector(`input[name="${name}"]`);
            if (!inp) return 'missing input ' + name;
            inp.value = String(v);
            inp.dispatchEvent(new Event('input',  {bubbles: true}));
            inp.dispatchEvent(new Event('change', {bubbles: true}));
        }
        const btn = [...document.querySelectorAll('input[type=submit]')]
            .find(b => /confirm/i.test(b.value || ''));
        if (!btn) return 'missing confirm button';
        btn.scrollIntoView({block: 'center'});
        const r = btn.getBoundingClientRect();
        return JSON.stringify({x: r.left + r.width / 2, y: r.top + r.height / 2});
    })())""" % json.dumps({
        "line[priceEco]":   targets["eco"],
        "line[priceBus]":   targets["bus"],
        "line[priceFirst]": targets["first"],
        "line[priceCargo]": targets["cargo"],
    })
    out = cdp.eval(fill_js)
    if not out or not out.startswith("{"):
        return "fail", out or "fill script returned nothing"
    pt = json.loads(out)

    for etype in ("mousePressed", "mouseReleased"):
        mid = cdp._send("Input.dispatchMouseEvent", {
            "type": etype, "x": pt["x"], "y": pt["y"],
            "button": "left", "clickCount": 1,
        })
        cdp._recv(mid)

    # The click submits the form and reloads the page; verify from the
    # rendered result (fetch often sees empty bodies right after a change).
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        time.sleep(1.0)
        cur = read_current_prices_rendered(cdp)
        if cur:
            if all(cur[k] == targets[k] for k in CLASS_ORDER):
                return "ok", ""
            continue  # may still be the pre-submit render
    cur = read_current_prices_rendered(cdp)
    if cur and all(cur[k] == targets[k] for k in CLASS_ORDER):
        return "ok", ""
    if cur:
        return "fail", f"rendered prices {cur} != targets"
    return "clicked", "could not verify from rendered page"


def collect_line_ids(cdp, airport_id, max_n=None):
    ids = []
    seen = set()
    page = 1
    while True:
        path = f"/marketing/pricing/?airport={airport_id}&page={page}"
        html = fetch_text(cdp, path)
        if not html:
            break
        found_this_page = []
        for m in LINEID_RE.finditer(html):
            lid = m.group(1)
            if lid in seen:
                continue
            seen.add(lid)
            found_this_page.append(lid)
            ids.append(lid)
            if max_n and len(ids) >= max_n:
                return ids
        if not found_this_page:
            break
        page += 1
        if page > 200:
            print(f"WARNING: stopped pagination at page {page}", file=sys.stderr)
            break
    return ids


def extract_dest_iata(html):
    """Try to extract destination IATA from a pricing detail page HTML."""
    if not html:
        return None

    title_m = re.search(r"<title>([^<]+)</title>", html)
    if title_m:
        title = title_m.group(1)
        m = TITLE_IATA_RE.search(title)
        if m:
            return (m.group(1) or m.group(2) or "").upper() or None

    for m in DEST_FROM_DETAIL.finditer(html[:5000]):
        for g in m.groups():
            if g and len(g) == 3:
                return g.upper()

    return None


def parse_price_page(html):
    if not html:
        return None
    out = {"ideal": {}, "current": {}, "token": None}
    ideal_vals = [int(s.replace(",", "")) for s in PRICE_RE_IDEAL_ALL.findall(html)]
    current_vals = [int(s.replace(",", "")) for s in PRICE_RE_CURRENT_ALL.findall(html)]
    if len(ideal_vals) < 4 or len(current_vals) < 4:
        return None
    for i, k in enumerate(CLASS_ORDER):
        out["ideal"][k]   = ideal_vals[i]
        out["current"][k] = current_vals[i]
    tm = TOKEN_RE.search(html)
    if not tm:
        return None
    out["token"] = tm.group(1)
    rm = re.search(r"<title>([^<]+)</title>", html)
    out["title"] = rm.group(1).strip() if rm else ""
    out["dest_iata"] = extract_dest_iata(html)
    return out


def resolve_airport_id_from_iata(cdp, iata):
    html = fetch_text(cdp, "/marketing/pricing/?airport=0&page=1")
    if not html:
        return None
    pat = re.compile(
        rf'<option[^>]+value="[^"]*airport=(\d+)[^"]*"[^>]*>\s*{re.escape(iata.upper())}\b'
    )
    m = pat.search(html)
    return m.group(1) if m else None


def load_circuit_dest_iatas(circuit_name):
    """Load destination IATAs for a circuit from DB."""
    db = get_db()
    row = db.execute(
        "SELECT hub_iata FROM circuits WHERE name=?", (circuit_name.upper(),)
    ).fetchone()
    if not row:
        return None, None
    hub_iata = row[0]
    rows = db.execute(
        "SELECT dest_iata FROM circuit_routes WHERE circuit_name=? ORDER BY route_order",
        (circuit_name.upper(),),
    ).fetchall()
    return hub_iata, set(r[0].upper() for r in rows)


def load_route_iatas_for_hub(hub_iata):
    """Load all destination IATAs for a hub from DB."""
    rows = get_db().execute(
        "SELECT dest_iata FROM routes WHERE hub_iata=? AND is_owned=1",
        (hub_iata,),
    ).fetchall()
    return set(r[0].upper() for r in rows)


def get_line_ids_from_db(hub_iata, dest_iatas=None):
    """Try to load line_ids from DB for a hub. Returns dict {iata: line_id} or None."""
    db = get_db()
    if dest_iatas:
        placeholders = ",".join("?" * len(dest_iatas))
        rows = db.execute(
            f"SELECT dest_iata, line_id FROM routes "
            f"WHERE hub_iata=? AND dest_iata IN ({placeholders}) AND line_id IS NOT NULL",
            (hub_iata.upper(), *dest_iatas)
        ).fetchall()
        result = {r[0].upper(): r[1] for r in rows}
        return result if len(result) == len(dest_iatas) else None
    else:
        rows = db.execute(
            "SELECT dest_iata, line_id FROM routes "
            "WHERE hub_iata=? AND is_owned=1 AND line_id IS NOT NULL",
            (hub_iata.upper(),)
        ).fetchall()
        return {r[0].upper(): r[1] for r in rows} if rows else None


def write_line_ids_to_db(hub_iata, mapping):
    """Write line_ids back to DB as a side effect of scraping. mapping: {iata: line_id}"""
    db = get_db()
    for dest, lid in mapping.items():
        db.execute(
            "UPDATE routes SET line_id = ?, is_owned = 1 "
            "WHERE hub_iata = ? AND dest_iata = ?",
            (lid, hub_iata.upper(), dest.upper())
        )
    db.commit()


def main():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--mode", choices=["ideal", "percent", "raw-ideal"],
                   default="ideal",
                   help="Pricing mode (default: ideal with corrected bus/fir)")
    p.add_argument("--pct", type=float, default=100.0,
                   help="Percent of ideal (only with --mode percent)")
    p.add_argument("--airport", default="0",
                   help="Airport id from hubDropdown (0 = all)")
    p.add_argument("--hub", help="Hub IATA — resolved to airport id at runtime")
    p.add_argument("--circuit", help="Only price routes in this circuit (e.g. MPM-C007)")
    p.add_argument("--routes", nargs="+", metavar="IATA",
                   help="Only price these destination IATAs")
    p.add_argument("--max", type=int, help="Stop after N routes (for testing)")
    p.add_argument("--skip-unchanged", action="store_true",
                   help="Skip POST when current already equals target")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true",
                   help="Print one JSON document to stdout instead of the "
                        "human report (which moves to stderr)")
    args = p.parse_args()

    if not args.json:
        _price(args, {"routes": [], "totals": {}})
        return

    # stdout must carry exactly one JSON document, so the report _price prints
    # goes to stderr. _price fills `doc` in place and exits from inside its
    # try/finally, so emit whatever it filled either way.
    doc = {"routes": [], "totals": {}}
    code = 0
    with contextlib.redirect_stdout(sys.stderr):
        try:
            _price(args, doc)
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
    json.dump(doc, sys.stdout, indent=2)
    print()
    sys.exit(code)


def _price(args, doc):
    """Run the pricer, printing the human-readable report to stdout.

    Fills `doc` (the --json result document) in place as it goes, so callers
    still get structured output when this exits early.
    """
    target_iatas = None
    hub_iata = None

    if args.circuit:
        hub_iata, circuit_iatas = load_circuit_dest_iatas(args.circuit)
        if circuit_iatas is None:
            print(f"ERROR: circuit '{args.circuit}' not found in DB.", file=sys.stderr)
            doc["error"] = f"circuit '{args.circuit}' not found"
            sys.exit(1)
        target_iatas = circuit_iatas
        print(f"Circuit {args.circuit}: hub={hub_iata}, {len(target_iatas)} routes: "
              f"{' '.join(sorted(target_iatas))}")
        if not args.hub and hub_iata:
            args.hub = hub_iata

    if args.routes:
        target_iatas = set(i.upper() for i in args.routes)
        print(f"Filtering to {len(target_iatas)} routes: {' '.join(sorted(target_iatas))}")

    print("Connecting to Chrome…")
    cdp = CDP(get_am_tab()["webSocketDebuggerUrl"], timeout=60)
    cdp.connect()
    try:
        airport_id = args.airport
        if args.hub:
            airport_id = resolve_airport_id_from_iata(cdp, args.hub)
            if not airport_id:
                print(f"ERROR: couldn't resolve hub {args.hub} in dropdown.",
                      file=sys.stderr)
                sys.exit(1)
            print(f"Hub {args.hub} → airport id {airport_id}")

        # Try DB-first line_ids if hub is known
        db_line_ids = None
        effective_hub = hub_iata or args.hub
        if effective_hub:
            db_line_ids = get_line_ids_from_db(effective_hub, target_iatas)
            if db_line_ids:
                print(f"Using {len(db_line_ids)} line_ids from DB (skipping scrape)")
                ids = [str(db_line_ids[i]) for i in sorted(target_iatas or db_line_ids.keys())
                       if i in db_line_ids] if target_iatas else [str(v) for v in db_line_ids.values()]
            else:
                print(f"Line_ids not in DB for {effective_hub}, falling back to scraping")

        if not db_line_ids:
            print(f"Collecting line ids (airport={airport_id})…")
            ids = collect_line_ids(cdp, airport_id, max_n=args.max)
            print(f"  {len(ids)} routes found")
            if not ids:
                print("Nothing to do.")
                return

        ok = fail = skipped = cooldown = not_matched = 0
        for i, lid in enumerate(ids, 1):
            html = fetch_text(cdp, f"/marketing/pricing/{lid}")
            data = parse_price_page(html)
            if not data:
                remaining = cooldown_remaining(html)
                if remaining:
                    label = lid
                    if db_line_ids:
                        for d, d_lid in db_line_ids.items():
                            if str(d_lid) == str(lid):
                                label = d
                                break
                    print(f"  [{i:4d}/{len(ids)}] {label}: "
                          f"skipped (cooldown, {remaining})")
                    cooldown += 1
                    doc["routes"].append({"route": label, "old": None, "new": None,
                                          "changed": None, "status": "cooldown",
                                          "detail": remaining})
                    continue
                hl = len(html) if html else 0
                print(f"  [{i:4d}/{len(ids)}] {lid}: PARSE FAIL (html len={hl})")
                fail += 1
                doc["routes"].append({"route": str(lid), "old": None, "new": None,
                                      "changed": None, "status": "fail",
                                      "detail": f"parse fail (html len={hl})"})
                continue

            dest = data.get("dest_iata")
            if db_line_ids and (not dest or (target_iatas and dest not in target_iatas)):
                for d, d_lid in db_line_ids.items():
                    if str(d_lid) == str(lid):
                        dest = d
                        break
            label = dest or lid

            if target_iatas:
                if not dest:
                    not_matched += 1
                    continue
                if dest not in target_iatas:
                    not_matched += 1
                    continue

            game_ideal = data["ideal"]
            cur = data["current"]

            if args.mode == "raw-ideal":
                tgt = dict(game_ideal)
            elif args.mode == "ideal":
                tgt = correct_ideal_prices(game_ideal)
            else:
                base = correct_ideal_prices(game_ideal)
                tgt = {k: max(1, int(round(v * args.pct / 100.0)))
                       for k, v in base.items()}

            changed = any(tgt[k] != cur[k] for k in tgt)

            bus_diff = tgt["bus"] - game_ideal["bus"]
            fir_diff = tgt["first"] - game_ideal["first"]
            corrections = ""
            if bus_diff or fir_diff:
                corrections = f" [bus{bus_diff:+d} fir{fir_diff:+d}]"

            tag = (f"e:{cur['eco']}→{tgt['eco']} "
                   f"b:{cur['bus']}→{tgt['bus']} "
                   f"f:{cur['first']}→{tgt['first']} "
                   f"c:{cur['cargo']}→{tgt['cargo']}")

            if args.skip_unchanged and not changed:
                skipped += 1
                doc["routes"].append({"route": label, "old": dict(cur), "new": dict(tgt),
                                      "changed": False, "status": "skipped"})
                if i % 25 == 0:
                    print(f"  [{i:4d}/{len(ids)}] {label}: unchanged (skip)")
                continue

            if args.dry_run:
                print(f"  [{i:4d}/{len(ids)}] {label:>4s}  {tag}{corrections}")
                doc["routes"].append({"route": label, "old": dict(cur), "new": dict(tgt),
                                      "changed": changed, "status": "dry_run"})
                continue

            status, detail = submit_prices_native(cdp, lid, tgt)
            entry = {"route": label, "old": dict(cur), "new": dict(tgt),
                     "changed": changed, "status": status}
            if detail:
                entry["detail"] = detail
            doc["routes"].append(entry)
            if status == "ok":
                ok += 1
                print(f"  [{i:4d}/{len(ids)}] {label:>4s}  {tag}{corrections}")
            elif status == "clicked":
                ok += 1
                print(f"  [{i:4d}/{len(ids)}] {label:>4s}  {tag}{corrections} "
                      f"(unverified: {detail})")
            elif status == "cooldown":
                cooldown += 1
                print(f"  [{i:4d}/{len(ids)}] {label}: skipped (cooldown, {detail})")
            else:
                fail += 1
                print(f"  [{i:4d}/{len(ids)}] {label}: FAIL ({detail})")

        print(f"\nDone. ok={ok} fail={fail} skipped={skipped} cooldown={cooldown} "
              f"not_in_filter={not_matched}")
        doc["totals"] = {"ok": ok, "fail": fail, "skipped": skipped,
                         "cooldown": cooldown, "not_in_filter": not_matched}
        sys.exit(0 if fail == 0 else 2)
    finally:
        cdp.close()


if __name__ == "__main__":
    main()
