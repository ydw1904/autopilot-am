#!/usr/bin/env python3
"""
Aircraft Reconfigurator — bring all aircraft assigned to a circuit into line
with the circuit's planned hub and seat configuration.

Workflow:
  1. User manually renames aircraft (in-game) to "<HUB>-C<NNN>" (or already
     "<HUB>-C<NNN>-<MMM>" — both are picked up).
  2. This tool walks /aircraft, finds matching aircraft, and for each one
     posts to:
       - /aircraft/show/<id>/attribute   (relocate to circuit's hub)
       - /aircraft/show/<id>/reconfigure (apply planner seats)
     Skips per-aircraft if the current state already matches.

Usage:
    python3 aircraft_reconfigurator.py --circuit MPM-C003
    python3 aircraft_reconfigurator.py --circuit MPM-C003 --dry-run

Requirements: Chrome with --remote-debugging-port=9222 and a logged-in AM tab.
"""

import argparse, json, os, re, sys, time
from urllib.parse import quote

from cdp import CDP, get_am_tab  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import get_db  # noqa: E402


PAGE_BATCH = 10
PAGE_FETCH_TIMEOUT = 120


# ── Page scraping helpers ────────────────────────────────────────────────

def _qs(name_filter: str | None) -> str:
    if not name_filter:
        return ""
    from urllib.parse import quote
    return "&name=" + quote(name_filter)


def discover_total_pages(cdp, name_filter: str | None = None) -> int:
    qs = _qs(name_filter)
    js = f"""
    (async () => {{
      const html = await fetch('/aircraft?page=1{qs}', {{credentials:'include'}}).then(r => r.text());
      const all = [...html.matchAll(/[?&]page=(\\d+)/g)].map(x => parseInt(x[1]));
      return all.length ? Math.max(...all) : 1;
    }})()
    """
    # Retry — a single fetch can fail or return a partial page that yields 1.
    best = 1
    for _ in range(4):
        n = int(cdp.eval(js, await_promise=True) or 1)
        if n > best:
            best = n
        if best > 5:
            return best
        time.sleep(0.5)
    return best


def scrape_all_aircraft(cdp, total_pages: int, name_filter: str | None = None):
    qs = _qs(name_filter)
    out = []
    for start in range(1, total_pages + 1, PAGE_BATCH):
        end = min(start + PAGE_BATCH - 1, total_pages)
        js = f"""
        (async () => {{
          const out = [];
          for (let p = {start}; p <= {end}; p++) {{
            const html = await fetch('/aircraft?page=' + p + '{qs}', {{credentials:'include'}}).then(r => r.text());
            const re = /id="editAircraftName(\\d+)"[^>]*>([\\s\\S]*?)<\\/span>/g;
            let m;
            while ((m = re.exec(html)) !== null) {{
              const inner = m[2].replace(/<[^>]+>/g, '').trim();
              out.push({{id: parseInt(m[1]), name: inner}});
            }}
          }}
          return out;
        }})()
        """
        batch = cdp.eval(js, await_promise=True)
        out.extend(batch or [])
    return out


# ── Per-aircraft GET/POST ────────────────────────────────────────────────

def _fetch_with_retry(cdp, url, attempts=5):
    """In-browser fetch with retry on null/empty/transient JS errors."""
    for i in range(attempts):
        html = cdp.eval(
            f"fetch({json.dumps(url)}, {{credentials:'include'}}).then(r => r.text()).catch(e => null)",
            await_promise=True,
        )
        if html:
            return html
        time.sleep(0.4 * (i + 1))
    return None


def get_current_hub_iata(cdp, aircraft_id: int):
    """GET /aircraft/show/<id> and extract the displayed 'Hub XXX /' text."""
    html = _fetch_with_retry(cdp, f"/aircraft/show/{aircraft_id}")
    if not html:
        return None
    # The detail page renders as:
    #   <span class="hubBtn"> Hub </span>
    #   <span>MPM /</span>
    m = re.search(
        r'class="hubBtn">\s*Hub\s*</span>\s*<span>\s*([A-Z]{3})\s*/', html
    )
    return m.group(1).upper() if m else None


def fetch_attribute_page(cdp, aircraft_id: int):
    """GET /aircraft/show/<id>/attribute. Returns dict:
       iata_to_hub_id (dict[str,int]), token (str)."""
    html = _fetch_with_retry(cdp, f"/aircraft/show/{aircraft_id}/attribute")
    if not html:
        return None
    tok = re.search(r'name="form\[_token\]"\s+value="([^"]+)"', html)

    # Each hub option lives inside <div class="hubListBox">…</div>. Within it,
    # the title block has "Hub XXX -" and the content block has hubIdValue.
    iata_to_hub = {}
    # Walk top-level hubListBox wrappers (not the nested -Title or -Lists ones).
    for blk_m in re.finditer(
        r'<div class="hubListBox">([\s\S]*?)(?=<div class="hubListBox">|$)', html
    ):
        blk = blk_m.group(1)
        title_m = re.search(
            r'class="hubListBoxTitle"[\s\S]*?Hub\s*</span>\s*([A-Z]{3})\s*-', blk
        )
        id_m = re.search(r'id="hubIdValue"\s*>\s*(\d+)\s*<', blk)
        if title_m and id_m:
            iata_to_hub.setdefault(title_m.group(1).upper(), int(id_m.group(1)))
    return {
        "iata_to_hub_id": iata_to_hub,
        "token": tok.group(1) if tok else None,
    }


def post_relocate(cdp, aircraft_id: int, hub_id: int, token: str):
    body = f"hubId={hub_id}&form%5B_token%5D={quote(token)}"
    js = f"""
    fetch('/aircraft/show/{aircraft_id}/attribute', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
      body: {json.dumps(body)},
      credentials: 'include'
    }}).then(r => r.status)
    """
    return cdp.eval(js, await_promise=True)


def fetch_reconfigure_page(cdp, aircraft_id: int):
    """GET /aircraft/show/<id>/reconfigure. Returns dict with current seats,
       payload total, name, skin id, and CSRF token."""
    html = _fetch_with_retry(cdp, f"/aircraft/show/{aircraft_id}/reconfigure")
    if not html:
        return None
    def fld(name):
        m = re.search(rf'name="aircraft\[{re.escape(name)}\]"[^>]*\bvalue="([^"]*)"', html)
        return m.group(1) if m else None
    # Skin: SAFETY-CRITICAL. Rare liveries cannot be re-applied once removed,
    # so we must echo back exactly the currently-applied skin. Only accept a
    # radio that is explicitly `checked`. No fallbacks — if we can't find it,
    # caller must abort rather than risk overwriting the livery.
    skin_id = None
    skin_img = None  # audit trail so we can verify livery isn't being changed
    # Primary signal: the page's JS sets the current skin via
    #   document.getElementById('aircraft_aircraftSkin_'+<ID>)...
    #   .setAttribute("checked", "checked")
    # That ID is authoritative — it's what the game treats as currently applied.
    js_m = re.search(
        r"getElementById\(\s*['\"]aircraft_aircraftSkin_['\"]\s*\+\s*(\d+)\s*\)"
        r"[\s\S]{0,400}?setAttribute\(\s*['\"]checked['\"]",
        html,
    )
    if js_m:
        skin_id = int(js_m.group(1))
    else:
        # Fallback: some renders bake `checked` directly into the radio tag.
        for radio_m in re.finditer(
            r'<input[^>]*name="aircraft\[aircraftSkin\]"[^>]*>', html
        ):
            tag = radio_m.group(0)
            if "checked" not in tag:
                continue
            val_m = re.search(r'\bvalue="(\d+)"', tag)
            if val_m:
                skin_id = int(val_m.group(1))
            break
    # Find the image filename for the resolved skin id, for audit logging.
    if skin_id is not None:
        label_m = re.search(
            rf'<label[^>]*for="aircraft_aircraftSkin_{skin_id}"[\s\S]*?<img[^>]*src="([^"]+)"',
            html,
        )
        if label_m:
            skin_img = label_m.group(1).rsplit("/", 1)[-1]
    return {
        "eco": int(fld("seatsEco") or 0),
        "bus": int(fld("seatsBus") or 0),
        "first": int(fld("seatsFirst") or 0),
        "cargo": int(fld("payloadUsed") or 0),
        "payload_total": fld("payloadTotal") or "0",
        "name": fld("name") or "",
        "skin_id": skin_id,
        "skin_img": skin_img,
        "token": fld("_token"),
    }


def post_reconfigure(cdp, aircraft_id: int, *, eco, bus, first, cargo):
    """Reconfigure an aircraft's seats. Navigates to the reconfigure page,
    reads current state, sets sliders, submits, and verifies.

    Returns (status_code, message)."""
    target_url = f"https://www.airlines-manager.com/aircraft/show/{aircraft_id}/reconfigure"
    cdp.navigate(target_url)
    time.sleep(4)

    if not cdp.eval("!!document.getElementById('sliderEco')"):
        return 500, "page_did_not_load"

    state = cdp.eval(
        "(() => {"
        " const skin = document.querySelector('input[name=\"aircraft[aircraftSkin]\"]:checked');"
        " return JSON.stringify({"
        "   eco: $(document.getElementById('sliderEco')).slider('value'),"
        "   bus: $(document.getElementById('sliderBus')).slider('value'),"
        "   first: $(document.getElementById('sliderFirst')).slider('value'),"
        "   cargo: $(document.getElementById('sliderCargo')).slider('value'),"
        "   skin: skin ? parseInt(skin.value) : null"
        " });"
        "})()"
    )
    state = json.loads(state) if isinstance(state, str) else state
    if not isinstance(state, dict):
        return 500, f"failed_to_read_state: {state!r}"

    if (state.get("eco") == eco and state.get("bus") == bus
            and state.get("first") == first and state.get("cargo") == cargo):
        return 200, "already_correct"

    if state.get("skin") is None:
        return 500, "no_checked_skin (refusing — would risk livery)"

    cdp.eval(
        f"$('#sliderEco').slider('value', {eco});"
        f" $('#sliderBus').slider('value', {bus});"
        f" $('#sliderFirst').slider('value', {first});"
        f" $('#sliderCargo').slider('value', {cargo});"
    )

    cdp.eval(
        f"$('#seatsEcoInput').val({eco});"
        f" $('#seatsBusInput').val({bus});"
        f" $('#seatsFirstInput').val({first});"
        f" $('#payloadUsedInput').val({cargo});"
    )

    cdp.eval("window.onbeforeunload = null; $(window).off('beforeunload');")
    cdp.eval("document.getElementById('showEquipment').submit()")

    time.sleep(4)
    cdp.navigate(target_url)
    time.sleep(3)
    vals_raw = cdp.eval(
        "JSON.stringify({"
        " eco: $(document.getElementById('sliderEco')).slider('value'),"
        " bus: $(document.getElementById('sliderBus')).slider('value'),"
        " first: $(document.getElementById('sliderFirst')).slider('value'),"
        " cargo: $(document.getElementById('sliderCargo')).slider('value')"
        "})"
    )
    vals = json.loads(vals_raw) if isinstance(vals_raw, str) else vals_raw
    if (isinstance(vals, dict)
            and vals.get("eco") == eco and vals.get("bus") == bus
            and vals.get("first") == first and vals.get("cargo") == cargo):
        return 200, target_url
    return 500, f"values_mismatch: {vals}"


# ── Main ─────────────────────────────────────────────────────────────────

def reconfigure_circuit(circuit_name: str, dry_run: bool = False) -> int:
    db = get_db()
    row = db.execute(
        "SELECT name, hub_iata, eco_seats, bus_seats, fir_seats, cargo_seats "
        "FROM circuits WHERE name = ?", (circuit_name,)
    ).fetchone()
    if not row:
        print(f"ERROR: circuit {circuit_name} not in DB", file=sys.stderr)
        return 1
    target_iata = row["hub_iata"].upper()
    target_seats = {
        "eco": row["eco_seats"] or 0,
        "bus": row["bus_seats"] or 0,
        "first": row["fir_seats"] or 0,
        "cargo": row["cargo_seats"] or 0,
    }
    print(f"Target for {circuit_name}: hub={target_iata} "
          f"seats=eco={target_seats['eco']} bus={target_seats['bus']} "
          f"first={target_seats['first']} cargo={target_seats['cargo']}t")

    cdp = CDP(get_am_tab()["webSocketDebuggerUrl"], timeout=PAGE_FETCH_TIMEOUT)
    cdp.connect()
    try:
        total_pages = discover_total_pages(cdp, name_filter=circuit_name)
        print(f"Scraping {total_pages} filtered /aircraft pages (name~{circuit_name!r}) …")
        all_ac = scrape_all_aircraft(cdp, total_pages, name_filter=circuit_name)
        print(f"  {len(all_ac)} aircraft total")

        prefix_re = re.compile(rf"^{re.escape(circuit_name)}(?:-\d{{1,3}})?$")
        matched = [ac for ac in all_ac if prefix_re.match(ac["name"])]
        print(f"  {len(matched)} match {circuit_name} prefix")

        if not matched:
            print("Nothing to do.")
            return 0

        # /attribute lists only OTHER hubs (you can't move to current). So we
        # resolve target_hub_id lazily, per-aircraft when relocation is needed.

        relocated = reconfigured = skipped = failed = 0
        for idx, ac in enumerate(matched, 1):
            aid, name = ac["id"], ac["name"]
            label = f"[{idx:3d}/{len(matched)}] {aid} {name!r}"

            # 1) Current hub from /show page (cheaper than parsing /attribute).
            cur_iata = get_current_hub_iata(cdp, aid)
            need_relocate = cur_iata != target_iata
            if need_relocate:
                if dry_run:
                    print(f"  {label} relocate {cur_iata} -> {target_iata}")
                else:
                    attr = fetch_attribute_page(cdp, aid)
                    if not attr or not attr["token"]:
                        print(f"  {label} FAIL fetch attribute"); failed += 1; continue
                    target_hub_id = attr["iata_to_hub_id"].get(target_iata)
                    if not target_hub_id:
                        print(f"  {label} FAIL: target hub {target_iata} not in "
                              f"option list ({sorted(attr['iata_to_hub_id'])})")
                        failed += 1; continue
                    s = post_relocate(cdp, aid, target_hub_id, attr["token"])
                    if s in (200, 204, 302):
                        relocated += 1
                    else:
                        print(f"  {label} relocate FAIL HTTP {s}"); failed += 1
                        continue
                    time.sleep(0.2)

            # 2) Seat check + reconfigure via page navigation.
            s, msg = post_reconfigure(
                cdp, aid,
                eco=target_seats["eco"], bus=target_seats["bus"],
                first=target_seats["first"], cargo=target_seats["cargo"],
            )
            if s == 200 and msg == "already_correct":
                skipped += 1
            elif s == 200:
                reconfigured += 1
            else:
                print(f"  {label} reconfigure FAIL ({msg})"); failed += 1
                continue

            if idx % 10 == 0 or idx == len(matched):
                print(f"  {label} done", flush=True)

        print(f"\nRelocated: {relocated}  Reconfigured: {reconfigured}  "
              f"Skipped (already correct): {skipped}  Failed: {failed}")
        return 0 if failed == 0 else 2
    finally:
        cdp.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--circuit", required=True, help="Circuit name, e.g. MPM-C003")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    sys.exit(reconfigure_circuit(args.circuit.upper(), args.dry_run))


if __name__ == "__main__":
    main()
