"""Shared helpers for driving the game's /network/planning page.

Single home for the hub-selection and list-scraping logic used by the MCP
server, the CLI scripts, and the GUI. Imports only from cdp.py and does not
print — callers report progress/errors from the returned values.
"""

import json
import re

# wait_for_js is re-exported here — it predates cdp.wait_for_js and callers
# import it from this module.
from cdp import BASE_URL, js_args, wait_for_js  # noqa: F401


def navigate_to_planning(cdp):
    """Navigate to /network/planning and wait for the aircraft list.

    Returns True once aircraft boxes are visible, False on timeout.
    """
    cdp.navigate(f"{BASE_URL}/network/planning")
    count = wait_for_js(
        cdp,
        "document.querySelectorAll('#aircraftList .aircraftListMiniBox').length",
    )
    return bool(count)


def wait_for_hub_buttons(cdp, timeout=15.0):
    """Wait for the planning page's hub selector buttons to appear."""
    return bool(wait_for_js(
        cdp,
        "document.querySelectorAll('.planninghubBtn').length",
        timeout=timeout,
    ))


def select_hub(cdp, hub_iata):
    """Click the .planninghubBtn whose text starts with '<IATA> /', then wait
    for the hub's aircraft/line lists to reload. Returns True/False."""
    hub_iata = hub_iata.upper().strip()
    result = cdp.eval_json(
        "(((HUB) => {"
        "  const btns = document.querySelectorAll('.planninghubBtn');"
        "  for (const btn of btns) {"
        "    const txt = (btn.textContent || '').trim();"
        "    if (txt.startsWith(HUB + ' /') || txt.startsWith(HUB + '/')) {"
        "      btn.click();"
        "      return {found: true, id: btn.id, text: txt};"
        "    }"
        "  }"
        "  return {found: false, count: btns.length};"
        f"}})({js_args(hub_iata)}))"
    )
    if not result or not result.get("found"):
        return False

    # The click handler shows a .loadingWheel synchronously and hides it only
    # after the AJAX swaps in the new hub's lists — waiting on list contents
    # alone returns early on the previous hub's still-visible items.
    loaded = wait_for_js(
        cdp,
        """((() => {
            const wheels = document.querySelectorAll('#aircraftList .loadingWheel, #lineList .loadingWheel');
            for (const w of wheels) {
                if (!w.classList.contains('hidden')) return false;
            }
            const hasAircraft = document.querySelectorAll('#aircraftList .aircraftListMiniBox').length;
            const hasLines = document.querySelectorAll('#lineList .lineList').length;
            return hasAircraft || hasLines || false;
        })())""",
        timeout=15.0,
        interval=0.5,
    )
    return bool(loaded)


def _resolve_hub_id(cdp, hub_iata):
    """data-hubId of the .planninghubBtn whose text starts with '<IATA> /'."""
    hub_iata = (hub_iata or "").upper().strip()
    if not re.fullmatch(r"[A-Z]{3}", hub_iata):
        return None
    hub_id = cdp.eval(
        "(((HUB) => {"
        "  for (const b of document.querySelectorAll('#hubList .planninghubBtn')) {"
        "    const t = (b.textContent || '').trim();"
        "    if (t.startsWith(HUB + ' /') || t.startsWith(HUB + '/'))"
        "      return b.getAttribute('data-hubId') || '';"
        "  }"
        "  return '';"
        f"}})({js_args(hub_iata)}))"
    )
    return str(hub_id) if hub_id and str(hub_id).isdigit() else None


def _load_hub_json(cdp, hub_iata=None):
    """Fetch a hub's full planning payload as JSON.

    The rendered #aircraftList caps at ~80 mini-boxes on big hubs, and the
    hub-selector click is coordinate-based (synthetic clicks land on the
    wrong hub), so DOM scraping is unreliable. The page's own AJAX endpoint
    /network/planning/load/<hubId> (with the XMLHttpRequest header — without
    it the server returns the HTML shell) carries the complete
    aircraftDataArray / lineDataArray for the hub, independent of which hub
    the UI has selected. Resolve the hubId from ``hub_iata`` when given;
    otherwise fall back to the UI-selected (hover) button.
    """
    if hub_iata:
        hub_id = _resolve_hub_id(cdp, hub_iata)
    else:
        hub_id = cdp.eval(
            "(document.querySelector('#hubList .planninghubBtnHover') || null)"
            "?.getAttribute('data-hubId') || ''")
    if not hub_id or not str(hub_id).isdigit():
        return None
    raw = cdp.eval(
        f"fetch('/network/planning/load/{int(hub_id)}', {{credentials:'include',"
        f" headers: {{'X-Requested-With': 'XMLHttpRequest'}}}})"
        f".then(r => r.text())",
        await_promise=True)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _skin_img(picture):
    """Livery filename from a picture URL, without the CDN cache-buster."""
    if not picture:
        return ""
    return picture.split("?")[0].rsplit("/", 1)[-1]


def get_aircraft_at_hub(cdp, hub_iata=None):
    """
    Extract all aircraft at a hub from the planning page.

    Pass ``hub_iata`` to read the hub's data directly (reliable); without it
    the currently UI-selected hub is used, which can be wrong on this page.

    Returns list of dicts:
    [{id: int, name: str, model: str, util: float, skin_img: str}, ...]
    The `id` is the game's aircraftId (from aircraftId_XXXXXXX). `skin_img` is
    the livery's picture filename — the only livery signal this page carries;
    db.resolve_skin_ids turns it into a numeric skin id.
    """
    payload = _load_hub_json(cdp, hub_iata)
    if payload and isinstance(payload.get("aircraftDataArray"), list):
        return [
            {"id": a["id"], "name": a.get("name") or "",
             "model": a.get("aircraftListName") or "",
             "util": a.get("utilizationPercentage") or 0,
             "skin_img": _skin_img(a.get("picture"))}
            for a in payload["aircraftDataArray"]
            if isinstance(a, dict) and a.get("id")
        ]

    # Fallback: scrape the rendered list (incomplete beyond ~80 aircraft).
    data = cdp.eval_json("""(() => {
        const result = [];
        const boxes = document.querySelectorAll('#aircraftList .aircraftListMiniBox');
        for (const box of boxes) {
            const idMatch = box.id && box.id.match(/aircraftId_(\\d+)/);
            if (!idMatch) continue;
            const id = parseInt(idMatch[1]);
            const boldEl = box.querySelector('.title .bold');
            const raw = boldEl ? boldEl.textContent.trim() : '';
            const parts = raw.split('/').map(s => s.trim()).filter(Boolean);
            const model = parts.length >= 2 ? parts[1] : (parts[0] || '');
            // Utilization: "<N>%" in .content .listBox1 > b. 0% = empty schedule.
            const utilEl = box.querySelector('.content .listBox1 > b');
            const utilStr = utilEl ? utilEl.textContent.trim().replace('%','') : '0';
            const util = parseFloat(utilStr) || 0;
            const img = box.querySelector('img[src*="/skins/"]');
            const src = img ? img.getAttribute('src') || '' : '';
            const skin_img = src ? src.split('?')[0].split('/').pop() : '';
            result.push({id, name: raw, model, util, skin_img});
        }
        return result;
    })()""")
    return data or []


def get_lines_at_hub(cdp, hub_iata=None):
    """
    Extract all lines (routes) at a hub (see get_aircraft_at_hub on hub_iata).

    Returns list of dicts: [{lineId: int, name: str, dest: str}, ...]
    """
    payload = _load_hub_json(cdp, hub_iata)
    if payload and isinstance(payload.get("lineDataArray"), list):
        lines = []
        for ln in payload["lineDataArray"]:
            if not isinstance(ln, dict) or not ln.get("id"):
                continue
            name = ln.get("name") or ""
            codes = re.findall(r"[A-Z]{3}", name)
            dest = codes[1] if len(codes) >= 2 else (codes[0] if codes else "")
            lines.append({"lineId": ln["id"], "name": name, "dest": dest})
        return lines

    # Fallback: scrape the rendered list.
    data = cdp.eval_json("""(() => {
        const result = [];
        const items = document.querySelectorAll('#lineList .lineList');
        for (const item of items) {
            const idEl = item.querySelector('.lineId');
            if (!idEl) continue;
            const lineId = parseInt(idEl.textContent.trim());
            if (!lineId || isNaN(lineId)) continue;
            // Strip the hidden lineId text from the display name.
            // Remaining text looks like "HND / KEF - 21h30".
            const fullText = item.textContent.trim();
            const display = fullText.replace(idEl.textContent.trim(), '').trim();
            // Dest IATA = the second 3-letter code (first is hub).
            const codes = display.match(/[A-Z]{3}/g) || [];
            const dest = codes.length >= 2 ? codes[1] : (codes[0] || '');
            result.push({lineId, name: display, dest});
        }
        return result;
    })()""")
    return data or []
