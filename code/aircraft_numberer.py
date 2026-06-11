#!/usr/bin/env python3
"""
Aircraft Numberer — assign canonical names <HUB>-C<NNN>-<MMM> to aircraft.

Two operations, both performed in one run:

1. Normalize: aircraft already named "<HUB>-C<NNN>-<N>" or "<HUB>-C<NNN>-<NN>"
   (1-2 digit suffix) get renamed to the canonical 3-digit form, preserving
   their existing number. e.g. "MPM-C003-9" -> "MPM-C003-009".

2. Number bare: aircraft named exactly "<HUB>-C<NNN>" (no suffix yet, e.g.
   freshly bought) get assigned the next available 3-digit slot.

After both passes, updates `waves_bought = floor(total_3digit / 7)` in the
DB and re-derives the circuit's status.

Walks the global /aircraft?page=N listing rather than the planning page so it
finds aircraft regardless of hub or current view.

Usage:
    python3 aircraft_numberer.py --hub HKG --circuit HKG-C001
    python3 aircraft_numberer.py --hub HKG --circuit HKG-C001 --dry-run
"""

import argparse, json, os, re, sys, time
from urllib.parse import quote

from cdp import CDP, get_am_tab  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import update_circuit_progress, get_db  # noqa: E402


def planned_target(circuit_name: str) -> int | None:
    """Return waves*7 from DB, or None if not set."""
    row = get_db().execute(
        "SELECT waves FROM circuits WHERE name=?", (circuit_name,)
    ).fetchone()
    if not row or not row[0]:
        return None
    return int(row[0]) * 7


PAGE_BATCH = 10  # /aircraft pages per JS round-trip
PAGE_FETCH_TIMEOUT = 120  # seconds for the chunked fetch


def _url_qs(name_filter: str | None) -> str:
    """Return the &name=X fragment used by the server-side filter, or ''."""
    if not name_filter:
        return ""
    from urllib.parse import quote
    return "&name=" + quote(name_filter)


def discover_total_pages(cdp, name_filter: str | None = None) -> int:
    """Highest /aircraft?page=N from the pagination row on page 1.

    When ``name_filter`` is set, the server filters the listing by
    aircraft-name prefix (the same field as the /aircraft search box) and
    pagination shrinks to just the matching subset — much faster than
    scanning the full fleet.
    """
    qs = _url_qs(name_filter)
    js = f"""
    (async () => {{
      const html = await fetch('/aircraft?page=1{qs}', {{credentials:'include'}}).then(r => r.text());
      const m = html.match(/page=(\\d+)"[^>]*>&gt;&gt;|page=(\\d+)"[^>]*>>>/);
      const all = [...html.matchAll(/[?&]page=(\\d+)/g)].map(x => parseInt(x[1]));
      return all.length ? Math.max(...all) : 1;
    }})()
    """
    n = cdp.eval(js, await_promise=True)
    return int(n or 1)


def scrape_all_aircraft(cdp, total_pages: int, name_filter: str | None = None):
    """Return [{id, name}, …] from /aircraft, paginated.

    Set ``name_filter`` to scope to a name-prefix server-side.
    """
    qs = _url_qs(name_filter)
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


def get_form_token(cdp, aircraft_id: int):
    """GET /aircraft/edit/<id>, parse out the CSRF token."""
    js = (
        f"fetch('/aircraft/edit/{aircraft_id}', "
        f"{{method:'GET', credentials:'include'}}).then(r => r.text())"
    )
    html = cdp.eval(js, await_promise=True)
    if not html:
        return None
    m = re.search(r'name="aircraft\[_token\]"\s+value="([^"]+)"', html)
    return m.group(1) if m else None


def rename(cdp, aircraft_id: int, new_name: str, token: str):
    """POST the rename form. Returns HTTP status."""
    body = (
        "aircraft%5Bname%5D=" + quote(new_name)
        + "&aircraft%5B_token%5D=" + quote(token)
    )
    js = f"""
        fetch('/aircraft/edit/{aircraft_id}', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
          body: {json.dumps(body)},
          credentials: 'include'
        }}).then(r => r.status)
    """
    return cdp.eval(js, await_promise=True)


def number_circuit(circuit_name: str, dry_run: bool = False) -> int:
    cdp = CDP(get_am_tab()["webSocketDebuggerUrl"], timeout=PAGE_FETCH_TIMEOUT)
    cdp.connect()
    try:
        print(f"Discovering /aircraft pagination (filtered to {circuit_name!r}) …")
        total_pages = discover_total_pages(cdp, name_filter=circuit_name)
        print(f"  {total_pages} pages — scraping aircraft list …")
        all_ac = scrape_all_aircraft(cdp, total_pages, name_filter=circuit_name)
        print(f"  {len(all_ac)} aircraft total")

        prefix = circuit_name
        full_pat = re.compile(rf"^{re.escape(prefix)}-(\d{{1,3}})$")

        # 1. Identify all aircraft associated with this circuit
        circuit_ac = []
        for ac in all_ac:
            if ac["name"].startswith(prefix):
                # Ensure it's exactly the prefix or prefix-NNN
                if ac["name"] == prefix or full_pat.match(ac["name"]):
                    circuit_ac.append(ac)
                    
        # Sort by ID to ensure deterministic behavior (older aircraft get slots first)
        circuit_ac.sort(key=lambda x: x["id"])
        
        target = planned_target(circuit_name)
        
        # 2. Categorize
        # keep: slots -> ac dict (already correctly numbered and <= target)
        keep = {}
        # flex: list of (ac, old_name) that need a new name (duplicates, bare, short, or > target)
        flex = []
        
        for ac in circuit_ac:
            name = ac["name"]
            if name == prefix:
                flex.append((ac, name))
                continue
                
            m = full_pat.match(name)
            if m:
                n = int(m.group(1))
                is_canonical = (len(m.group(1)) == 3)
                
                # It's a valid slot if it's canonical, we haven't filled this slot yet, 
                # and it's within our target (if we have a target).
                if is_canonical and n not in keep and (target is None or n <= target):
                    keep[n] = ac
                else:
                    flex.append((ac, name))
            else:
                flex.append((ac, name))

        print(f"\n{prefix}:")
        print(f"  total aircraft matching: {len(circuit_ac)}")
        print(f"  already correctly numbered: {len(keep)}")
        print(f"  needs numbering (flex): {len(flex)}")
        if target is not None:
            print(f"  target slots: {target}")

        plan = []
        
        # 3. Fill missing slots up to target
        next_slot = 1
        while flex:
            # If we have a target and we've reached it, remaining flex go to storage
            if target is not None and next_slot > target:
                break
                
            if next_slot not in keep:
                ac, old_name = flex.pop(0)
                new_name = f"{prefix}-{next_slot:03d}"
                plan.append((ac["id"], old_name, new_name))
                keep[next_slot] = ac
            
            next_slot += 1
            
        # 4. Any remaining flex aircraft go to STORAGE
        if flex:
            res = get_db().execute("SELECT aircraft_model FROM circuits WHERE name=?", (circuit_name,)).fetchone()
            ac_model = res[0] if res else "AC"
            storage_prefix = f"{ac_model}-STORAGE"
            
            storage_pat = re.compile(rf"^{re.escape(storage_prefix)}-(\d{{3}})$")
            used_storage = set()
            for ac in all_ac:
                sm = storage_pat.match(ac["name"])
                if sm: used_storage.add(int(sm.group(1)))
            
            next_s = 1
            for ac, old_name in flex:
                while next_s in used_storage:
                    next_s += 1
                new_name = f"{storage_prefix}-{next_s:03d}"
                plan.append((ac["id"], old_name, new_name))
                used_storage.add(next_s)
                next_s += 1
            
            print(f"  excess aircraft: {len(flex)} -> will be renamed to {storage_prefix}-NNN")

        if not plan:
            print("  Nothing to do.")
            total_after = len(keep)
            waves_bought = total_after // 7
            if not dry_run:
                update_circuit_progress(circuit_name, waves_bought=waves_bought)
            print(f"  Total numbered: {total_after} → waves_bought={waves_bought}")
            return 0

        if dry_run:
            print("\n[dry-run] Planned renames:")
            for aid, raw, new in plan:
                print(f"  {aid}  {raw!r:<24} -> {new!r}")
            return 0

        ok = fail = 0
        for idx, (aid, raw, new_name) in enumerate(plan, 1):
            tok = get_form_token(cdp, aid)
            if not tok:
                print(f"  [{idx:3d}/{len(plan)}] {aid}: NO TOKEN", flush=True)
                fail += 1
                continue
            status = rename(cdp, aid, new_name, tok)
            if status in (200, 302):
                ok += 1
                if idx % 10 == 0 or idx == len(plan):
                    print(f"  [{idx:3d}/{len(plan)}] {raw!r} -> {new_name!r}",
                          flush=True)
            else:
                fail += 1
                print(f"  [{idx:3d}/{len(plan)}] {raw!r} -> {new_name!r} "
                      f"FAIL HTTP {status}", flush=True)
            time.sleep(0.2)

        total_after = len(keep)
        waves_bought = total_after // 7
        update_circuit_progress(circuit_name, waves_bought=waves_bought)
        print(f"\nRenamed {ok}/{len(plan)}  Failed: {fail}")
        print(f"Total numbered: {total_after} → waves_bought={waves_bought}")
        return 0 if fail == 0 else 2
    finally:
        cdp.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--hub", required=False, help="Hub IATA (kept for CLI parity; not used)")
    p.add_argument("--circuit", required=True, help="Circuit name, e.g. HKG-C001")
    p.add_argument("--dry-run", action="store_true", help="Preview without renaming")
    args = p.parse_args()
    sys.exit(number_circuit(args.circuit.upper(), args.dry_run))


if __name__ == "__main__":
    main()
