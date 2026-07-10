"""Shared helpers for driving the game's /network/planning page.

Single home for the hub-selection and list-scraping logic used by the MCP
server, the CLI scripts, and the GUI. Imports only from cdp.py and does not
print — callers report progress/errors from the returned values.
"""

import time

from cdp import BASE_URL


def wait_for_js(cdp, expression, timeout=15.0, interval=0.5):
    """Poll a JS expression until it returns a truthy non-error value."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = cdp.eval(expression)
        if last and not isinstance(last, dict):
            return last
        time.sleep(interval)
    return last


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
    result = cdp.eval_json(f"""((() => {{
        const btns = document.querySelectorAll('.planninghubBtn');
        for (const btn of btns) {{
            const txt = (btn.textContent || '').trim();
            if (txt.startsWith('{hub_iata} /') || txt.startsWith('{hub_iata}/')) {{
                btn.click();
                return {{found: true, id: btn.id, text: txt}};
            }}
        }}
        return {{found: false, count: btns.length}};
    }})())""")
    if not result or not result.get("found"):
        return False

    loaded = wait_for_js(
        cdp,
        """((() => {
            const hasAircraft = document.querySelectorAll('#aircraftList .aircraftListMiniBox').length;
            const hasLines = document.querySelectorAll('#lineList .lineList').length;
            return hasAircraft || hasLines || false;
        })())""",
        timeout=15.0,
        interval=0.5,
    )
    return bool(loaded)


def get_aircraft_at_hub(cdp):
    """
    Extract all aircraft at the currently selected hub from the planning page.

    Returns list of dicts: [{id: int, name: str, model: str, util: float}, ...]
    The `id` is the game's aircraftId (from aircraftId_XXXXXXX).
    """
    data = cdp.eval_json("""(() => {
        const result = [];
        const boxes = document.querySelectorAll('#aircraftList .aircraftListMiniBox');
        for (const box of boxes) {
            const idMatch = box.id && box.id.match(/aircraftId_(\\d+)/);
            if (!idMatch) continue;
            const id = parseInt(idMatch[1]);
            const boldEl = box.querySelector('.title .bold');
            const raw = boldEl ? boldEl.textContent.trim() : '';
            const model = raw.split('/')[0].trim();
            // Utilization: "<N>%" in .content .listBox1 > b. 0% = empty schedule.
            const utilEl = box.querySelector('.content .listBox1 > b');
            const utilStr = utilEl ? utilEl.textContent.trim().replace('%','') : '0';
            const util = parseFloat(utilStr) || 0;
            result.push({id, name: raw, model, util});
        }
        return result;
    })()""")
    return data or []


def get_lines_at_hub(cdp):
    """
    Extract all lines (routes) at the currently selected hub.

    Returns list of dicts: [{lineId: int, name: str, dest: str}, ...]
    """
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
