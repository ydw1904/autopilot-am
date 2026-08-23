"""Airlines Manager GUI / Web App — sidebar layout v3 "Crane"."""

import sys, os, datetime, asyncio, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from starlette.responses import Response
from nicegui import ui, app

import gui.fleet    as fleet_page
import gui.liveries as liveries_page
import gui.hub      as hub_page
import gui.planner  as planner_page
import gui.circuits as circuits_page
import gui.scraper  as scraper_page
import gui.log_view as log_page
import gui.library  as library_page
import gui.mass     as mass_page
import gui.warehouse as warehouse_page
from gui.theme   import CSS, NAVY, GOLD, DIM, DIM2, BORDER2, GREEN, RED, CYAN
from gui.state   import APP
from gui.workers import cdp_up, launch_chrome
from db          import get_skin_image_bytes


# ── Skin image serving endpoint ──────────────────────────────────────────────
_TRANSPARENT_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00"
    b"\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00"
    b"\x00IEND\xaeB`\x82"
)

@app.get("/api/skin_image/{skin_id}")
@app.get("/api/skin_image/{skin_id}/{size}")
def serve_skin_image(skin_id: int, size: str = "big"):
    """Serve cached livery PNG bytes directly from mobile_skin_images in DB."""
    try:
        data = get_skin_image_bytes(skin_id, size)
        if data:
            return Response(
                content=data,
                media_type="image/png",
                headers={"Cache-Control": "public, max-age=86400"},
            )
    except Exception:
        pass
    return Response(
        content=_TRANSPARENT_PNG,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


def on_circuits_ready(circuits):
    APP["circuits"] = circuits
    if _refresh_circuits:
        _refresh_circuits()

_refresh_circuits = None


NAV_ITEMS = [
    {"id": "fleet",     "icon": "✈", "label": "Fleet",     "sub": "Fleet mgmt"},
    {"id": "liveries",  "icon": "🎨", "label": "Liveries",  "sub": "Collection"},
    {"id": "hub",       "icon": "◈", "label": "Hub Mgmt",  "sub": "Routes & stats"},
    {"id": "plan",      "icon": "⟁", "label": "Planner",   "sub": "Find circuits"},
    {"id": "circuits",  "icon": "◎", "label": "Circuits",  "sub": "Results"},
    {"id": "library",   "icon": "▤", "label": "Library",   "sub": "Saved circuits"},
    {"id": "mass",      "icon": "⛁", "label": "Mass",      "sub": "Bulk actions"},
    {"id": "warehouse", "icon": "⛃", "label": "Warehouse", "sub": "Unused fleet"},
    {"id": "scraper",   "icon": "⌕", "label": "Scraper",   "sub": "Data collection"},
    {"id": "log",       "icon": "≡", "label": "Log",       "sub": "Activity"},
]


@ui.page("/")
def index():
    global _refresh_circuits

    ui.add_css(CSS)

    pages             = {}
    nav_els           = {}   # page_id → nav item div element
    _reload_fleet     = None
    _filter_fleet_skin = None
    _reload_liveries  = None
    _reload_library   = None
    _reload_warehouse = None

    def navigate(page_id: str):
        APP["active_page"] = page_id
        for pid, nav_el in nav_els.items():
            if pid == page_id:
                nav_el.classes(add="active", remove="")
            else:
                nav_el.classes(remove="active")
        for pid, container in pages.items():
            container.set_visibility(pid == page_id)

        if page_id == "fleet" and _reload_fleet:
            asyncio.ensure_future(_reload_fleet())
        elif page_id == "liveries" and _reload_liveries:
            asyncio.ensure_future(_reload_liveries())
        elif page_id == "library" and _reload_library:
            asyncio.ensure_future(_reload_library())
        elif page_id == "warehouse" and _reload_warehouse:
            asyncio.ensure_future(_reload_warehouse())

        ui.run_javascript(
            f"history.replaceState(null, '', '?page={page_id}')"
        )

    def jump_to_fleet_with_skin(skin_id: int):
        navigate("fleet")
        if _filter_fleet_skin:
            _filter_fleet_skin(skin_id)

    # ── Sidebar ──────────────────────────────────────────────────────────
    with ui.left_drawer(value=True).props("permanent width=196 bordered") \
         .style("padding:0; overflow:hidden;"):

        # Logo
        with ui.element("div").style(
            "display:flex; align-items:center; gap:10px; "
            "padding:20px 16px 18px; border-bottom:1px solid var(--border); flex-shrink:0;"
        ):
            with ui.element("div").style(
                "width:34px; height:34px; border-radius:10px; flex-shrink:0; "
                "background:linear-gradient(135deg,#0A2470,#05164D); "
                "display:flex; align-items:center; justify-content:center; "
                "font-size:16px; color:#FFAD00; font-weight:700; "
                "box-shadow:0 2px 8px rgba(5,22,77,0.25);"
            ):
                ui.label("✈")
            with ui.element("div"):
                ui.label("AM").style(
                    "font-size:15px; font-weight:700; color:var(--text-hi); "
                    "font-family:Inter,sans-serif; letter-spacing:0.4px; display:block;"
                )
                ui.label("Revenue Optimizer").style(
                    "font-size:9px; color:var(--text-dim); "
                    "font-family:Inter,sans-serif; letter-spacing:0.3px; display:block;"
                )

        # Status dots
        with ui.element("div").style(
            "display:flex; flex-direction:column; gap:6px; padding:12px 16px; "
            "border-bottom:1px solid var(--border); flex-shrink:0;"
        ):
            def _dot_row(label):
                with ui.element("div").style("display:flex; align-items:center; gap:8px;") as row:
                    dot = ui.element("div").style(
                        f"width:7px; height:7px; border-radius:50%; background:{BORDER2}; flex-shrink:0;"
                    )
                    ui.label(label).style(
                        f"font-size:11px; color:{DIM}; font-family:Inter,sans-serif; font-weight:500;"
                    )
                return dot

            chrome_dot  = _dot_row("Chrome")
            planner_dot = _dot_row("Planner")

            with ui.element("div").style("display:flex; align-items:center; gap:8px;"):
                circuit_dot   = ui.element("div").style(
                    f"width:7px; height:7px; border-radius:50%; background:{BORDER2}; flex-shrink:0;"
                )
                circuit_label = ui.label("0C").style(
                    f"font-size:11px; color:{DIM}; font-family:JetBrains Mono,monospace;"
                )

        _cdp_cache = {"v": False, "t": 0.0}

        def _update_dots():
            import time
            now = time.monotonic()
            if now - _cdp_cache["t"] > 4.0:
                _cdp_cache["v"] = cdp_up()
                _cdp_cache["t"] = now
            chrome_up = _cdp_cache["v"]
            plan_run  = APP.get("planner_running", False)
            n_circ    = len(APP.get("circuits", []))

            chrome_dot.style(
                f"width:7px; height:7px; border-radius:50%; flex-shrink:0; "
                f"background:{GREEN if chrome_up else RED};"
            )
            planner_dot.style(
                f"width:7px; height:7px; border-radius:50%; flex-shrink:0; "
                f"background:{GOLD if plan_run else BORDER2};"
                + ("; animation:pulseDot 1.5s ease-in-out infinite;" if plan_run else "")
            )
            circuit_dot.style(
                f"width:7px; height:7px; border-radius:50%; flex-shrink:0; "
                f"background:{CYAN if n_circ > 0 else BORDER2};"
            )
            circuit_label.set_text(f"{n_circ}C")

        ui.timer(3.0, _update_dots)

        # Nav items
        with ui.element("div").style(
            "flex:1; overflow-y:auto; padding:10px 10px; display:flex; flex-direction:column; gap:2px;"
        ):
            for item in NAV_ITEMS:
                pid = item["id"]
                is_active = (pid == "fleet")
                with ui.element("div") \
                     .classes("am-nav-item" + (" active" if is_active else "")) \
                     .on("click", lambda _p=pid: navigate(_p)) as nav_el:
                    ui.label(item["icon"]).classes("am-nav-icon")
                    with ui.element("div").style("flex:1; min-width:0;"):
                        ui.label(item["label"]).classes("am-nav-label")
                        ui.label(item["sub"]).classes("am-nav-sub")
                    ui.element("div").classes("am-nav-indicator")
                nav_els[pid] = nav_el

        # Bottom: version + clock
        with ui.element("div").style(
            "padding:12px 16px; border-top:1px solid var(--border); flex-shrink:0; "
            "display:flex; flex-direction:column; gap:4px;"
        ):
            ui.label("v3.0 Crane").style(
                "font-size:9px; color:var(--text-dim2); "
                "font-family:Inter,sans-serif; letter-spacing:0.4px; font-weight:500;"
            )
            clock_el = ui.label("").style(
                "font-size:13px; color:var(--text-dim); "
                "font-family:JetBrains Mono,monospace; letter-spacing:0.8px;"
            )
            ui.timer(1.0, lambda: clock_el.set_text(
                datetime.datetime.now().strftime("%H:%M:%S")
            ))

    # ── Page containers ───────────────────────────────────────────────────

    fleet_div = ui.element("div").classes("am-page")
    fleet_div.set_visibility(True)
    _reload_fleet, _filter_fleet_skin = fleet_page.build(fleet_div)
    pages["fleet"] = fleet_div

    liveries_div = ui.element("div").classes("am-page")
    liveries_div.set_visibility(False)
    _reload_liveries = liveries_page.build(liveries_div, on_view_in_fleet=jump_to_fleet_with_skin)
    pages["liveries"] = liveries_div

    hub_div = ui.element("div").classes("am-page")
    hub_div.set_visibility(False)
    hub_page.build(hub_div)
    pages["hub"] = hub_div

    plan_div = ui.element("div").classes("am-page")
    plan_div.set_visibility(False)
    planner_page.build(plan_div, on_circuits_ready)
    pages["plan"] = plan_div

    circ_div = ui.element("div").classes("am-page")
    circ_div.set_visibility(False)
    _refresh_circuits = circuits_page.build(circ_div, lambda: APP["circuits"])
    pages["circuits"] = circ_div

    lib_div = ui.element("div").classes("am-page")
    lib_div.set_visibility(False)
    _reload_library = library_page.build(lib_div)
    pages["library"] = lib_div

    mass_div = ui.element("div").classes("am-page")
    mass_div.set_visibility(False)
    mass_page.build(mass_div)
    pages["mass"] = mass_div

    warehouse_div = ui.element("div").classes("am-page")
    warehouse_div.set_visibility(False)
    _reload_warehouse = warehouse_page.build(warehouse_div)
    pages["warehouse"] = warehouse_div

    scr_div = ui.element("div").classes("am-page")
    scr_div.set_visibility(False)
    scraper_page.build(scr_div)
    pages["scraper"] = scr_div

    log_div = ui.element("div").classes("am-page")
    log_div.set_visibility(False)
    log_page.build(log_div)
    pages["log"] = log_div

    # Deep link: /?page=<id>
    async def _initial_nav():
        try:
            target = await ui.run_javascript(
                "new URLSearchParams(location.search).get('page')",
                timeout=5.0,
            )
        except (TimeoutError, Exception):
            return
        if target and target in pages and target != "fleet":
            navigate(target)
    ui.timer(1.5, _initial_nav, once=True)

    # Alt+1-0 shortcuts
    def _kb(e):
        if e.args.get("altKey") and not e.args.get("ctrlKey"):
            key = e.args.get("key", "")
            mapping = {
                "1": "fleet",
                "2": "liveries",
                "3": "hub",
                "4": "plan",
                "5": "circuits",
                "6": "library",
                "7": "mass",
                "8": "warehouse",
                "9": "scraper",
                "0": "log",
            }
            if key in mapping:
                navigate(mapping[key])

    ui.on("keydown", _kb)


def main():
    p = argparse.ArgumentParser(description="Airlines Manager Control Panel Web App")
    p.add_argument("--host", default=os.getenv("AM_HOST", "127.0.0.1"), help="Host to bind (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=int(os.getenv("AM_PORT", "8080")), help="Port to bind (default: 8080)")
    p.add_argument("--native", action="store_true", help="Launch in native pywebview window")
    p.add_argument("--no-browser", action="store_true", help="Do not automatically launch browser tab")
    args, _ = p.parse_known_args()

    print(f"✈ Airlines Manager Web App starting at http://{args.host}:{args.port}")

    ui.run(
        host=args.host,
        port=args.port,
        native=args.native,
        window_size=(1380, 880) if args.native else None,
        title="Airlines Manager Control Panel",
        dark=False,
        reload=False,
        show=not args.no_browser and not args.native,
    )


if __name__ == "__main__":
    main()
