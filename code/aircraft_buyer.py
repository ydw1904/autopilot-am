#!/usr/bin/env python3
"""
Aircraft Buyer -- Purchase aircraft via Chrome CDP.

Two modes:
  Circuit mode: reads config from a saved circuit in DB
    python3 aircraft_buyer.py MPM-C001
    python3 aircraft_buyer.py MPM-C001 --dry-run

  Standalone mode: specify aircraft, hub, seats directly
    python3 aircraft_buyer.py --model B742 --hub MPM --eco 293 --bus 16 --first 11 --cargo 35 --quantity 7
    python3 aircraft_buyer.py --model B742 --hub MPM --eco 293 --bus 16 --first 11 --cargo 35 --quantity 7 --dry-run

  List saved circuits:
    python3 aircraft_buyer.py --list

Flow (verified against the live page):
  1. Navigate to /aircraft/buy/new/{haul}; wait for the list to STABILISE
     (boxes load async after document ready — see navigate_to_list).
  2. Find the target aircraft box by game_id (.aircraftJson id) or title.
     NB: some models have a separate "Cargo" freighter variant with its own
     id; matching the wrong one yields a cargo-only config form.
  3. Submit the box form to load the AJAX configure step into #buyAircraft_bucket.
  4. In the configure form:
     - Set hub via #aircraft_hub (match option by data-iata).
     - Set seats: zero all four, then cargo→first→bus→eco (eco LAST) because
       each slider clamps against the others' current values.
     - Set quantity (.aircraftQuantity, max 99) and optional name.
     - GUARD: read the seats back; abort if they don't match the request.
  5. Click "Personal purchase" (input[data-purchaseassistance="false"]). The
     game serialises the config into the hidden `aircrafts` field in that
     button's CLICK handler, then POSTs to /aircraft/buy/new/buyMultiple.
  6. Verify success by checking the page navigates to /buyMultiple.

Requirements:
  - Chrome with --remote-debugging-port=9222 --remote-allow-origins=*
  - httpx and websocket-client pip packages
"""

import argparse, json, math, re, sqlite3, sys, time

from cdp import CDP, get_am_tab, connect_cdp, BASE_URL  # noqa: F401
from db import DB

ALIASES = {
    "B722": "727-200",
    "B741": "747-100B", "B742": "747-200B", "B743": "747-300",
    "B744": "747-400", "B748": "747-8I", "B74S": "747-SP",
    "A388": "A380-800", "IL96": "Il-96-300",
}

AIRCRAFT_GAME_IDS = {
    "747-200B": 114, "777-200": 148, "777-300": 149, "747-400": 153,
    "747-100B": 131, "747-SP": 136, "767-200": 141, "767-300": 142,
    "A310": 143, "A300-600": 145, "A340-300": 151, "A340-600": 152,
    "MD-11": 146, "DC8-55": 123, "707-320C": 124, "777F": 154, "747-400F": 155,
    "L-1049G": 179, "A321neo": 93, "737 MAX 8": 112,
}

CATEGORY_TO_HAUL = {
    1: "short", 2: "short", 3: "short",
    4: "middle", 5: "middle", 6: "middle",
    7: "long", 8: "long", 9: "long", 10: "long",
}

PER_PURCHASE_LIMIT = 99


def resolve_model(name):
    """Resolve alias or model name to full model name."""
    if name in ALIASES:
        return ALIASES[name]
    return name


def get_balance(cdp):
    """Player's dollar balance from the header resource bar, or None."""
    val = cdp.eval(
        "document.querySelector('#ressource3[title=Dollars]')?.textContent")
    if val and not isinstance(val, dict):
        digits = re.sub(r"[^0-9]", "", str(val))
        if digits:
            return int(digits)
    return None


# ── Page interaction ────────────────────────────────────────────────────────

def wait_for_aircraft_list(cdp, timeout=15):
    """Wait until .aircraftPurchaseBox elements are on the page."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.5)
        count = cdp.eval("document.querySelectorAll('.aircraftPurchaseBox').length")
        if count and count > 0:
            return count
    return 0


def navigate_to_list(cdp, haul, timeout=15):
    """Navigate to a haul list page and wait for a FRESH document to load.

    Guards against a stale-DOM race: right after Page.navigate the previous
    page's `.aircraftPurchaseBox` elements are still in the DOM, so a bare
    count check returns immediately and the caller queries the OLD list (e.g.
    matching the wrong aircraft variant). We tag the current document with a
    marker, navigate, and wait until that marker is gone (= new document). The
    box list then populates ASYNChronously after the document loads (it starts
    with ~2 placeholder boxes and grows to the full ~50), so we also wait for
    the count to STABILISE before returning — otherwise the caller searches a
    partial list and misses the aircraft. Returns the box count, or 0.
    """
    cdp.eval("window.__acStale = true;")
    cdp.navigate(f"{BASE_URL}/aircraft/buy/new/{haul}")
    deadline = time.monotonic() + timeout
    prev = -1
    while time.monotonic() < deadline:
        time.sleep(0.5)
        state = cdp.eval_json("""(() => ({
            stale: !!window.__acStale,
            count: document.querySelectorAll('.aircraftPurchaseBox').length,
        }))()""")
        if not state or state.get("stale"):
            continue  # still on the old document
        count = state.get("count") or 0
        if count > 0 and count == prev:
            return count  # unchanged for one poll → list finished loading
        prev = count
    return prev if prev > 0 else 0


def find_aircraft_on_page(cdp, model_name, game_id=None):
    """Find an aircraft box on the current page. Returns box index or -1.

    Tries multiple strategies:
      1. Match by .aircraftJson data.id (if game_id known)
      2. Match by input[name="aircraft[id]"] value (if game_id known)
      3. Match by .title span text containing model_name
    """
    return cdp.eval(f"""(() => {{
        const boxes = document.querySelectorAll('.aircraftPurchaseBox');
        const want = '{model_name}';
        const wantId = {game_id or 'null'};
        for (let i = 0; i < boxes.length; i++) {{
            const box = boxes[i];
            // Strategy 1: .aircraftJson JSON data
            const jsonDiv = box.querySelector('.aircraftJson');
            if (jsonDiv && wantId) {{
                try {{
                    const data = JSON.parse(jsonDiv.textContent.trim());
                    if (data.id === wantId) return i;
                }} catch(e) {{}}
            }}
            // Strategy 2: hidden input with aircraft id
            if (wantId) {{
                const inp = box.querySelector('input[name="aircraft[id]"]');
                if (inp && parseInt(inp.value) === wantId) return i;
            }}
            // Strategy 3: title text match
            const titleEl = box.querySelector('.title span, .aircraftTitle, h3, h4');
            if (titleEl && titleEl.textContent.trim().includes(want)) return i;
        }}
        return -1;
    }})()""")


def search_all_haul_pages(cdp, model_name, game_id=None):
    """Navigate haul pages and find the aircraft. Returns (haul, box_index) or (None, -1)."""
    for haul in ["long", "middle", "short"]:
        count = navigate_to_list(cdp, haul)
        if not count:
            continue
        idx = find_aircraft_on_page(cdp, model_name, game_id)
        if idx is not None and idx >= 0:
            print(f"  Found {model_name} on /{haul} page (box #{idx})")
            return haul, idx
    return None, -1


def scrape_game_id(cdp, model_name):
    """Search haul pages for the aircraft and extract its game_id from the form."""
    for haul in ["long", "middle", "short"]:
        count = navigate_to_list(cdp, haul)
        if not count:
            continue
        game_id = cdp.eval(f"""(() => {{
            const boxes = document.querySelectorAll('.aircraftPurchaseBox');
            for (const box of boxes) {{
                const t = box.querySelector('.title span, .aircraftTitle, h3, h4');
                if (t && t.textContent.trim().includes('{model_name}')) {{
                    const id = box.querySelector('input[name="aircraft[id]"]');
                    return id ? parseInt(id.value) : null;
                }}
            }}
            return null;
        }})()""")
        if game_id:
            print(f"  Found {model_name} game_id={game_id} on /{haul} page")
            return game_id
    return None


def trigger_configure(cdp, box_index):
    """Submit the aircraft box form to load the AJAX configure step.

    Returns True if the configure form appeared in #buyAircraft_bucket.
    """
    res = cdp.eval(f"""(() => {{
        const box = document.querySelectorAll('.aircraftPurchaseBox')[{box_index}];
        if (!box) return 'no_box';
        // Try form submit first (standard path)
        const form = box.querySelector('form');
        if (form) {{
            $(form).trigger('submit');
            return 'form_submitted';
        }}
        // Fallback: click Buy button
        const btn = box.querySelector('.purchaseButton, .buyButton, button[type="submit"], a[href*="configure"]');
        if (btn) {{
            btn.click();
            return 'button_clicked';
        }}
        return 'no_trigger';
    }})()""")
    if res not in ('form_submitted', 'button_clicked'):
        print(f"  ERROR: trigger_configure: {res}", file=sys.stderr)
        return False

    # Wait for the configure form to load into #buyAircraft_bucket
    for _ in range(15):
        cdp.wait(0.5)
        state = cdp.eval_json("""(() => {
            const bucket = document.getElementById('buyAircraft_bucket');
            const cfg = document.getElementById('buyAircraft_configure');
            return {
                bucketExists: !!bucket,
                bucketChildren: bucket ? bucket.children.length : 0,
                cfgHidden: cfg ? cfg.classList.contains('hidden') : null,
                bucketHTML: bucket ? bucket.innerHTML.slice(0, 100) : '',
            };
        })()""")
        if not state:
            continue
        if state.get("bucketChildren", 0) > 0:
            print("  Configure form loaded")
            return True
        # Some pages load configure directly (not via bucket)
        if state.get("bucketExists") and state.get("bucketChildren", -1) >= 0:
            # Bucket exists but empty — keep waiting
            pass

    print("  ERROR: Configure form did not appear after 7.5s", file=sys.stderr)
    # Debug: dump what's on the page
    debug = cdp.eval_json("""(() => ({
        url: location.href,
        bucket: !!document.getElementById('buyAircraft_bucket'),
        cfg: !!document.getElementById('buyAircraft_configure'),
        forms: document.querySelectorAll('#buyAircraft_bucket form, #buyAircraft_configure form').length,
    }))()""")
    print(f"  Debug: {debug}", file=sys.stderr)
    return False


def set_input_value(form_js, selector, value):
    """JS expression to set an input value using native setter + jQuery trigger."""
    return f"""(() => {{
        const form = {form_js};
        if (!form) return 'no_form';
        const el = form.querySelector('{selector}');
        if (!el) return 'no_element:{selector}';
        const nativeSetter = Object.getOwnPropertyDescriptor(
            HTMLInputElement.prototype, 'value'
        ).set;
        nativeSetter.call(el, '{value}');
        $(el).trigger('input').trigger('change');
        return 'ok';
    }})()"""


def set_seat_js(form_js, selector, value):
    """Set a seat slider's manual input. Fires the full event chain
    (input/change/keyup/blur) so the jQuery-UI slider handler recomputes the
    hidden aircraft[seats*] fields. Verified against the live configure form."""
    return f"""(() => {{
        const form = {form_js};
        if (!form) return 'no_form';
        const el = form.querySelector('{selector}');
        if (!el) return 'no_element:{selector}';
        const ns = Object.getOwnPropertyDescriptor(
            HTMLInputElement.prototype, 'value'
        ).set;
        ns.call(el, '{value}');
        $(el).trigger('input').trigger('change').trigger('keyup').trigger('blur');
        return 'ok';
    }})()"""


def configure_and_purchase(cdp, hub_iata, eco, bus, first, cargo,
                           num_aircraft=1, dry_run=False, name_prefix=None):
    """Fill configure form and submit purchase.

    Returns (success: bool|None, message: str). None = dry run.
    """
    bucket_form_js = "document.getElementById('buyAircraft_bucket').querySelector('form')"

    # Step 1: Set hub dropdown
    hub_result = cdp.eval(f"""(() => {{
        const form = {bucket_form_js};
        if (!form) return 'no_form';
        const hub = form.querySelector('#aircraft_hub, select[name*="hub"]');
        if (!hub) return 'no_hub_select';
        const want = '{hub_iata.upper()}';
        const wantLower = '{hub_iata.lower()}';
        // Strategy 1: data-iata attribute
        let opt = Array.from(hub.options).find(
            o => (o.getAttribute('data-iata') || '').toLowerCase() === wantLower
        );
        // Strategy 2: option text contains the IATA code
        if (!opt) {{
            opt = Array.from(hub.options).find(
                o => o.textContent.toUpperCase().includes(want)
            );
        }}
        // Strategy 3: option value is the IATA
        if (!opt) {{
            opt = Array.from(hub.options).find(
                o => o.value.toUpperCase() === want
            );
        }}
        if (!opt) {{
            // Dump available options for debugging
            const opts = Array.from(hub.options).map(o => ({{
                val: o.value, text: o.textContent.trim().slice(0, 60),
                iata: o.getAttribute('data-iata')
            }}));
            return JSON.stringify({{error: 'no_matching_option', options: opts.slice(0, 20)}});
        }}
        hub.value = opt.value;
        $(hub).trigger('change');
        return JSON.stringify({{hub: opt.value, iata: want, text: opt.textContent.trim().slice(0, 40)}});
    }})()""")

    # Parse hub result
    hub_debug = hub_result
    if isinstance(hub_result, str):
        try:
            hub_debug = json.loads(hub_result)
        except (json.JSONDecodeError, TypeError):
            pass
    if isinstance(hub_debug, dict) and "error" in hub_debug:
        print(f"  ERROR: Hub selection failed: {hub_debug}", file=sys.stderr)
        return False, f"hub_select_failed: {hub_debug.get('error')}"
    print(f"  Hub set: {hub_debug}")

    cdp.wait(0.5)

    # Step 2: Set seat config.
    # Each class input is a jQuery-UI slider whose max is clamped against the
    # CURRENT values of the other classes. Setting a high eco while bus/first
    # are still at their template defaults gets capped (verified live: eco=60
    # silently reverted to 38). So zero all four first, then set in the order
    # cargo → first → bus → eco (eco LAST) so no class ever clamps.
    for sel in ('.cargoManualInput', '.firstManualInput',
                '.busManualInput', '.ecoManualInput'):
        cdp.eval(set_seat_js(bucket_form_js, sel, '0'))
        cdp.wait(0.15)
    for sel, val in (('.cargoManualInput', cargo), ('.firstManualInput', first),
                     ('.busManualInput', bus), ('.ecoManualInput', eco)):
        res = cdp.eval(set_seat_js(bucket_form_js, sel, str(val)))
        if res and res.startswith('no_element'):
            print(f"  WARNING: seat input {sel} not found", file=sys.stderr)
        cdp.wait(0.25)

    # Step 3: Set quantity
    qty_js = f"""(() => {{
        const form = {bucket_form_js};
        if (!form) return 'no_form';
        const setVal = (el) => {{
            if (!el) return;
            const ns = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
            ns.call(el, '{num_aircraft}');
            $(el).trigger('input').trigger('change');
        }};
        // Try multiple selectors for quantity input
        setVal(form.querySelector('.aircraftQuantity'));
        setVal(form.querySelector('input[name="aircraft[quantity]"]'));
        setVal(form.querySelector('input[type="number"]'));
        return 'qty_set';
    }})()"""
    cdp.eval(qty_js)
    cdp.wait(0.3)

    # Step 3b: Set aircraft name
    if name_prefix:
        cdp.eval(set_input_value(bucket_form_js, 'input[name="aircraft[name]"]', name_prefix))
        cdp.eval(f"""(() => {{
            const form = {bucket_form_js};
            if (!form) return;
            const r = form.querySelector('input[name="aircraft[namePattern]"][value="0"]');
            if (r) {{ r.checked = true; $(r).trigger('change'); }}
        }})()""")
        cdp.wait(0.3)

    # Read back actual values for verification
    actual = cdp.eval_json(f"""(() => {{
        const form = {bucket_form_js};
        if (!form) return null;
        return {{
            eco: form.querySelector('.ecoManualInput')?.value,
            bus: form.querySelector('.busManualInput')?.value,
            first: form.querySelector('.firstManualInput')?.value,
            cargo: form.querySelector('.cargoManualInput')?.value,
            hub: form.querySelector('#aircraft_hub, select[name*="hub"]')?.value,
            qty: form.querySelector('input[name="aircraft[quantity]"], .aircraftQuantity')?.value,
            name: form.querySelector('input[name="aircraft[name]"]')?.value || '',
        }};
    }})()""")

    if actual:
        print(f"  Config read back: eco={actual.get('eco')} bus={actual.get('bus')} "
              f"first={actual.get('first')} cargo={actual.get('cargo')}t "
              f"hub={actual.get('hub')} qty={actual.get('qty')} "
              f"name={actual.get('name')!r}")

    # Safety guard: the form must hold EXACTLY the requested seat config before
    # we spend money. A missing input (None) or a clamped/reverted value means
    # we matched the wrong aircraft variant or the sliders didn't take — in
    # which case the game would otherwise buy with its default layout. Abort.
    def _as_int(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None
    if not actual:
        return False, "config_readback_failed: configure form not found"
    want = {"eco": eco, "bus": bus, "first": first, "cargo": cargo}
    mismatches = {
        k: (actual.get(k), v) for k, v in want.items()
        if _as_int(actual.get(k)) != v
    }
    if mismatches:
        detail = ", ".join(f"{k}: got {g!r} want {w}" for k, (g, w) in mismatches.items())
        print(f"  ABORT: seat config did not apply ({detail}).", file=sys.stderr)
        print("  This usually means the wrong aircraft variant was matched "
              "(check game_id/haul) or a value exceeded capacity. Not buying.",
              file=sys.stderr)
        return False, f"seat_config_mismatch: {detail}"

    if dry_run:
        # Exercise the real serialization without buying: clicking "Personal
        # purchase" makes the game build the `aircrafts` JSON and call
        # form.submit(). Stub form.submit() + preventDefault the submit event so
        # we capture the exact payload that WOULD be posted, then restore.
        payload = cdp.eval(r"""(() => {
            const cfg = document.getElementById('buyAircraft_configure');
            const form = cfg && cfg.querySelector('form[action*="buyMultiple"]');
            const btn = cfg && cfg.querySelector(
                'input.purchaseButton[data-purchaseassistance="false"]');
            if (!form || !btn) return 'no_form_or_button';
            const orig = HTMLFormElement.prototype.submit;
            let captured = null;
            HTMLFormElement.prototype.submit = function () {
                captured = this.querySelector('[name="aircrafts"]').value;
            };
            const block = (e) => e.preventDefault();
            form.addEventListener('submit', block, false);
            try { btn.click(); }
            finally {
                HTMLFormElement.prototype.submit = orig;
                form.removeEventListener('submit', block, false);
            }
            return captured || form.querySelector('[name="aircrafts"]').value || '(empty)';
        })()""")
        print("  DRY RUN: would POST /aircraft/buy/new/buyMultiple")
        print(f"  DRY RUN: aircrafts = {payload}")
        return None, "dry run"

    # Step 4: Submit purchase by clicking "Personal purchase".
    # The game serializes the configured aircraft into the form's hidden
    # `aircrafts` field inside this button's CLICK handler, then calls
    # form.submit(). Firing a bare submit event (jQuery .trigger('submit'))
    # skips that handler, leaving `aircrafts` empty — the POST then buys
    # nothing. So we must click the actual button. data-purchaseassistance
    # ="false" selects a personal (non-alliance) purchase.
    print(f"  Submitting purchase for {num_aircraft}x aircraft...")
    submit_result = cdp.eval(r"""(() => {
        const cfg = document.getElementById('buyAircraft_configure');
        if (!cfg) return 'no_configure_section';
        const btn = cfg.querySelector(
            'input.purchaseButton[data-purchaseassistance="false"]');
        if (!btn) return 'no_personal_purchase_button';
        btn.click();
        return 'submitted';
    })()""")

    if submit_result != 'submitted':
        print(f"  ERROR: submit returned {submit_result}", file=sys.stderr)
        return False, f"submit_failed: {submit_result}"

    # Wait for navigation/response
    for _ in range(20):
        cdp.wait(0.5)
        result = cdp.eval_json("""(() => ({
            url: document.location.href,
            pathname: window.location.pathname,
            ready: document.readyState,
        }))()""")
        if not result:
            continue
        pn = result.get("pathname", "")
        if "/buyMultiple" in pn:
            return True, "Purchase successful (recap page)"
        if pn == "/home" or pn == "/":
            return False, "Redirected to home — purchase FAILED"
        # Still on aircraft pages — keep waiting
        if "/aircraft/buy" in pn and "configure" not in pn.lower() and "/new/" in pn:
            # Navigated back to list — purchase likely failed
            return False, f"Back on list page: {pn}"

    # Timeout
    result = cdp.eval_json("""({pathname: window.location.pathname})""")
    pn = (result or {}).get("pathname", "?")
    return False, f"Timeout waiting for result (on {pn})"


# ── DB helpers ──────────────────────────────────────────────────────────────

def load_circuit(db, circuit_name):
    circuit_name = circuit_name.upper()
    row = db.execute(
        "SELECT name, hub_iata, aircraft_model, total_hours, "
        "total_eco, total_bus, total_fir, total_cargo, status, "
        "waves, waves_bought, "
        "eco_seats, bus_seats, fir_seats, cargo_seats "
        "FROM circuits WHERE name=?", (circuit_name,)
    ).fetchone()
    if not row:
        return None
    routes = db.execute(
        "SELECT dest_iata, dest_name, distance_km, eco_demand, bus_demand, "
        "fir_demand, cargo_demand, flight_time_rt, route_order "
        "FROM circuit_routes WHERE circuit_name=? ORDER BY route_order",
        (circuit_name,)
    ).fetchall()
    return {
        "name": row[0], "hub": row[1], "model": row[2],
        "total_hours": row[3], "total_eco": row[4],
        "total_bus": row[5], "total_fir": row[6], "total_cargo": row[7],
        "status": row[8],
        "waves": row[9] or 0, "waves_bought": row[10] or 0,
        "planned_eco": row[11], "planned_bus": row[12],
        "planned_fir": row[13], "planned_cargo": row[14],
        "routes": routes,
    }


def load_aircraft_specs(db, model_name):
    """Load aircraft specs by full model name."""
    row = db.execute(
        "SELECT model, max_pax, max_tonnage, category, speed_kmh, range_km, gross_price "
        "FROM aircraft WHERE model=?", (model_name,)
    ).fetchone()
    if not row:
        return None
    return {
        "model": row[0], "max_pax": row[1], "max_tonnage": row[2],
        "category": row[3], "speed": row[4], "range_km": row[5], "price": row[6],
    }


def get_hub_id_from_db(db, hub_iata):
    """Look up numeric hub ID from player_hubs table."""
    row = db.execute(
        "SELECT hub_id FROM player_hubs WHERE hub_iata=?",
        (hub_iata.upper(),)
    ).fetchone()
    return row[0] if row else None


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Aircraft Buyer — purchase aircraft via Chrome CDP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Circuit mode:
  python3 aircraft_buyer.py MPM-C001
  python3 aircraft_buyer.py MPM-C001 --dry-run
  python3 aircraft_buyer.py MPM-C001 --eco 400 --bus 100

Standalone mode:
  python3 aircraft_buyer.py --model B742 --hub MPM --eco 293 --bus 16 --first 11 --cargo 35 --quantity 7
  python3 aircraft_buyer.py --model B742 --hub MPM --eco 293 --bus 16 --first 11 --cargo 35 --quantity 7 --dry-run

List:
  python3 aircraft_buyer.py --list
""",
    )
    p.add_argument("circuit", nargs="?", help="Circuit name (e.g. MPM-C001)")
    p.add_argument("--model", help="Aircraft model or alias (e.g. B742, 747-200B)")
    p.add_argument("--hub", help="Hub IATA code (e.g. MPM)")
    p.add_argument("--hub-id", dest="hub_id", type=int, default=None,
                   help="Numeric hub ID (looked up from DB if not given)")
    p.add_argument("--eco", type=int, default=None)
    p.add_argument("--bus", type=int, default=None)
    p.add_argument("--first", type=int, default=None)
    p.add_argument("--cargo", type=int, default=None)
    p.add_argument("--quantity", type=int, default=None)
    p.add_argument("--name", default=None, help="Aircraft name prefix")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--list", action="store_true")
    args = p.parse_args()

    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row

    if args.list:
        circuits = db.execute(
            "SELECT name, hub_iata, aircraft_model, total_hours, status, waves, "
            "eco_seats, bus_seats, fir_seats, cargo_seats "
            "FROM circuits ORDER BY name"
        ).fetchall()
        if not circuits:
            print("No circuits found.")
            return
        print(f"{'Circuit':<12} {'Hub':<6} {'Aircraft':<8} {'Waves':<7} "
              f"{'Eco':<5} {'Bus':<4} {'Fir':<4} {'Crg':<4} {'Hours':<7} {'Status'}")
        print("-" * 72)
        for c in circuits:
            e = c['eco_seats'] or '-'
            b = c['bus_seats'] or '-'
            f = c['fir_seats'] or '-'
            cg = c['cargo_seats'] or '-'
            w = c['waves'] or '-'
            print(f"{c['name']:<12} {c['hub_iata']:<6} {c['aircraft_model']:<8} "
                  f"{w:<7} {e:<5} {b:<4} {f:<4} {cg:<4} "
                  f"{c['total_hours']:<7.1f} {c['status']}")
        return

    # Determine mode: circuit vs standalone
    if args.circuit:
        # Circuit mode
        circuit = load_circuit(db, args.circuit)
        if not circuit:
            print(f"ERROR: Circuit '{args.circuit}' not found.", file=sys.stderr)
            sys.exit(1)

        model_alias = circuit["model"]
        model_name = resolve_model(model_alias)
        hub_iata = circuit["hub"]
        specs = load_aircraft_specs(db, model_name)

        if not specs:
            print(f"  WARNING: '{model_name}' not in aircraft table — capacity check skipped",
                  file=sys.stderr)
            specs = {"max_pax": 0, "max_tonnage": 0, "range_km": 0, "category": 0, "price": 0}

        # Seat config: CLI args > planner-saved > demand-derived
        if args.eco is not None:
            eco = args.eco
            bus = args.bus if args.bus is not None else 0
            first = args.first if args.first is not None else 0
            cargo = args.cargo if args.cargo is not None else 0
        elif circuit["planned_eco"] is not None:
            eco = circuit["planned_eco"] or 0
            bus = circuit["planned_bus"] or 0
            first = circuit["planned_fir"] or 0
            cargo = circuit["planned_cargo"] or 0
            print(f"Using planner seat config: eco={eco} bus={bus} first={first} cargo={cargo}")
        else:
            # Demand-derived fallback
            te = circuit["total_eco"] or 1
            tb = (circuit["total_bus"] or 0) * 1.5
            tf = (circuit["total_fir"] or 0) * 2.0
            tw = te + tb + tf
            max_pax = specs["max_pax"]
            eco = int(max_pax * te / tw) if tw > 0 else max_pax
            bus = int(max_pax * tb / tw) if tw > 0 else 0
            first = int(max_pax * tf / tw) if tw > 0 else 0
            while eco + bus + first > max_pax and bus > 0:
                bus -= 1
            while eco + bus + first > max_pax and first > 0:
                first -= 1
            while eco + bus + first > max_pax:
                eco -= 1
            used = 0.1 * eco + 0.125 * bus + 0.15 * first
            cargo = max(0, int(specs["max_tonnage"] - used))
            print(f"Auto seat config (demand-derived): eco={eco} bus={bus} first={first} cargo={cargo}")

        # Quantity: fill gap to waves*7
        waves = circuit["waves"]
        waves_bought = circuit["waves_bought"]
        target = waves * 7
        gap = max(0, target - waves_bought * 7)
        requested = args.quantity if args.quantity is not None else gap
        name_prefix = args.name or circuit["name"]

        if requested <= 0:
            print(f"Nothing to buy: {circuit['name']} has {waves_bought}/{waves} waves "
                  f"({waves_bought * 7} aircraft).")
            return

        game_id = AIRCRAFT_GAME_IDS.get(model_name)
        haul = CATEGORY_TO_HAUL.get(specs.get("category", 7), "long")

    elif args.model:
        # Standalone mode
        model_alias = args.model
        model_name = resolve_model(model_alias)
        hub_iata = args.hub
        if not hub_iata:
            p.error("--hub is required in standalone mode")
            return

        specs = load_aircraft_specs(db, model_name)
        if not specs:
            print(f"ERROR: '{model_name}' not found in aircraft table.", file=sys.stderr)
            p.error(f"Unknown model: {model_name}")
            return

        eco = args.eco if args.eco is not None else 0
        bus = args.bus if args.bus is not None else 0
        first = args.first if args.first is not None else 0
        cargo = args.cargo if args.cargo is not None else 0
        requested = args.quantity or 1
        name_prefix = args.name or model_alias
        waves = None
        waves_bought = 0
        game_id = AIRCRAFT_GAME_IDS.get(model_name)
        haul = CATEGORY_TO_HAUL.get(specs.get("category", 7), "long")
    else:
        p.error("Provide a circuit name or use --model for standalone mode")
        return

    hub_id = args.hub_id or get_hub_id_from_db(db, hub_iata)

    # Summary
    print(f"\n{'=' * 55}")
    print(f"  Aircraft:  {model_name} (alias: {model_alias}, game_id: {game_id or 'TBD'})")
    print(f"  Hub:       {hub_iata}" + (f" (id: {hub_id})" if hub_id else ""))
    print(f"  Haul:      {haul}")
    if specs.get("max_pax"):
        print(f"  Specs:     {specs['max_pax']} PAX / {specs['max_tonnage']}T / "
              f"{specs['range_km']}km")
    print(f"  Seats:     eco={eco} bus={bus} first={first} cargo={cargo}t")
    print(f"  Name:      {name_prefix}")
    print(f"  To buy:    {requested} aircraft "
          f"({(requested + PER_PURCHASE_LIMIT - 1) // PER_PURCHASE_LIMIT} batch(es) of "
          f"≤{PER_PURCHASE_LIMIT})")
    if waves is not None:
        print(f"  Circuit:   waves={waves} target={waves * 7} already={waves_bought * 7}")
    print(f"{'=' * 55}\n")

    # Offline capacity sanity check (both dry-run and live).
    if specs.get("max_tonnage", 0) > 0:
        payload = 0.1 * eco + 0.125 * bus + 0.15 * first + 1 * cargo
        print(f"Payload check: {payload:.2f}T / {specs['max_tonnage']}T max")
        print(f"PAX check:     {eco + bus + first} / {specs['max_pax']} max")
        if payload > specs["max_tonnage"]:
            print("WARNING: Payload exceeds capacity!")
        if eco + bus + first > specs["max_pax"]:
            print("WARNING: Seats exceed capacity!")
    print()

    # ── Live flow ─────────────────────────────────────────────────────────
    # A dry-run still drives the real page (navigate → configure → read back →
    # capture the payload that WOULD be posted) but never submits. This is the
    # only way to confirm the right aircraft variant was matched and the seat
    # sliders actually took, so dry-run failures catch wrong game_id/haul.

    print("Connecting to Chrome...")
    cdp = connect_cdp()

    balance_before = get_balance(cdp)
    print(f"Balance: ${balance_before:,.0f}" if balance_before else "Could not read balance")

    # Resolve game_id if not known
    if not game_id:
        print(f"Looking up game_id for {model_name}...")
        game_id = scrape_game_id(cdp, model_name)
        if not game_id:
            print(f"ERROR: Could not find {model_name} on any haul page.", file=sys.stderr)
            cdp.close()
            sys.exit(1)

    bought_total = 0
    remaining = requested
    batch_idx = 0

    while remaining > 0:
        batch_idx += 1
        batch_qty = min(remaining, PER_PURCHASE_LIMIT)
        print(f"\n{'─' * 50}")
        print(f"  Batch {batch_idx}: buying {batch_qty} aircraft")
        print(f"{'─' * 50}")

        # Navigate to the right haul page (waits for a fresh document)
        count = navigate_to_list(cdp, haul)
        if not count:
            print("ERROR: Aircraft list didn't load — aborting", file=sys.stderr)
            break

        # Find aircraft box
        box_index = find_aircraft_on_page(cdp, model_name, game_id)
        if box_index is None or box_index < 0:
            # Maybe the aircraft is on a different haul page
            print(f"  Not found on /{haul}, searching other pages...")
            found_haul, box_index = search_all_haul_pages(cdp, model_name, game_id)
            if box_index < 0:
                print(f"ERROR: {model_name} not found on any haul page — aborting", file=sys.stderr)
                break
            haul = found_haul

        # Trigger configure step
        if not trigger_configure(cdp, box_index):
            print("ERROR: Configure form did not load — aborting", file=sys.stderr)
            break

        # Configure and purchase (dry_run captures payload without submitting)
        success, message = configure_and_purchase(
            cdp, hub_iata,
            eco=eco, bus=bus, first=first, cargo=cargo,
            num_aircraft=batch_qty, name_prefix=name_prefix,
            dry_run=args.dry_run,
        )
        print(f"  Result: {message}")

        if args.dry_run:
            # success is None on a dry run — one batch is enough to validate.
            print("\nDRY RUN — no purchases made.")
            break

        if not success and success is not None:
            print("  Stopping after failed batch.")
            break

        if success:
            bought_total += batch_qty
            remaining -= batch_qty

            # Update DB if in circuit mode
            if args.circuit and waves is not None:
                new_bought = waves_bought + batch_idx
                try:
                    db.execute(
                        "UPDATE circuits SET waves_bought=? WHERE name=?",
                        (new_bought, args.circuit.upper())
                    )
                    db.commit()
                except Exception as e:
                    print(f"  (DB update skipped: {e})")

        if remaining > 0:
            time.sleep(2)

    if args.dry_run:
        cdp.close()
        db.close()
        sys.exit(0)

    balance_after = get_balance(cdp)
    if balance_before and balance_after:
        spent = balance_before - balance_after
        print(f"\nBalance: ${balance_before:,.0f} -> ${balance_after:,.0f} (spent: ${spent:,.0f})")
    print(f"\nBought {bought_total}/{requested} aircraft.")
    cdp.close()
    db.close()
    sys.exit(0 if bought_total == requested else 2)


if __name__ == "__main__":
    main()
