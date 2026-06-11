import os
import time

from .constants import (
    COUNTRIES, EXTRACT_JS, ROUTE_COUNT_JS,
    AUDIT_SELECT_ALL_JS, AUDIT_READ_COUNT_JS, AUDIT_CLICK_JS,
    AUDIT_CHECK_POPUP_JS, AUDIT_CONFIRM_JS, AUDIT_CLOSE_POPUP_JS,
)


def load_checkpoint(path):
    done = set()
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split('|')
                if parts:
                    done.add(parts[0])
    return done


def save_checkpoint(path, country, route_count, status):
    with open(path, 'a') as f:
        f.write(f"{country}|{route_count}|{time.strftime('%Y-%m-%dT%H:%M:%S')}|{status}\n")


def make_url(hub_id, country):
    return f"https://www.airlines-manager.com/network/newline/{hub_id}/{country}"


def get_route_count(backend):
    raw = backend.eval_js(ROUTE_COUNT_JS)
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 0


def do_audit(backend):
    backend.eval_js(AUDIT_SELECT_ALL_JS)
    time.sleep(1)

    sel_str = backend.eval_js(AUDIT_READ_COUNT_JS)
    try:
        selected = int(sel_str)
    except (ValueError, TypeError):
        return 0, f"count_error:{sel_str}"

    if selected == 0:
        return 0, "nothing_selected"

    r = backend.eval_js(AUDIT_CLICK_JS)
    if 'ok' not in str(r):
        return selected, "no_audit_btn"

    popup_ok = False
    for _ in range(10):
        time.sleep(1)
        r = backend.eval_js(AUDIT_CHECK_POPUP_JS)
        if 'Continue' in str(r) or 'OK' in str(r):
            popup_ok = True
            break

    if not popup_ok:
        backend.eval_js(AUDIT_CLOSE_POPUP_JS)
        return selected, f"no_continue_btn: {r}"

    backend.eval_js(AUDIT_CONFIRM_JS)
    return selected, "ok"


def scrape_country(backend, hub_id, country):
    backend.navigate(make_url(hub_id, country))
    count = get_route_count(backend)
    if count == 0:
        return {"country": country, "count": 0, "status": "empty", "routes": []}
    return {"country": country, "count": count, "status": "navigated", "routes": []}


def extract_data(backend):
    raw = backend.eval_js(EXTRACT_JS)
    if not raw or raw.startswith("ERROR") or raw == "TIMEOUT":
        return None, raw or "no data"
    raw = raw.strip().strip('"')
    if not raw:
        return [], "no_data"
    lines = raw.split('\n')
    routes = []
    for line in lines:
        parts = line.split('|')
        if len(parts) >= 5:
            routes.append(line)
    return routes, "ok"


def write_routes(out_path, country, routes):
    with open(out_path, 'a') as f:
        for line in routes:
            f.write(f"{country}|{line}\n")
