"""Airlines Manager GUI — sidebar layout v2."""

import sys, os, datetime, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nicegui import ui, app
import gui.hub      as hub_page
import gui.planner  as planner_page
import gui.circuits as circuits_page
import gui.scraper  as scraper_page
import gui.log_view as log_page
import gui.library  as library_page
import gui.mass     as mass_page
import gui.warehouse as warehouse_page
from gui.theme   import CSS
from gui.state   import APP
from gui.workers import cdp_up, launch_chrome


def on_circuits_ready(circuits):
    APP['circuits'] = circuits
    if _refresh_circuits:
        _refresh_circuits()

_refresh_circuits = None


NAV_ITEMS = [
    {'id': 'hub',      'icon': '◈', 'label': 'Hub Mgmt', 'sub': 'Routes & stats'},
    {'id': 'plan',     'icon': '⟁', 'label': 'Planner', 'sub': 'Find circuits'},
    {'id': 'circuits', 'icon': '◎', 'label': 'Circuits','sub': 'Results'},
    {'id': 'library',  'icon': '▤', 'label': 'Library', 'sub': 'Saved circuits'},
    {'id': 'mass',     'icon': '⛁', 'label': 'Mass',    'sub': 'Bulk actions'},
    {'id': 'warehouse','icon': '⛃', 'label': 'Warehouse','sub': 'Unused fleet'},
    {'id': 'scraper',  'icon': '⌕', 'label': 'Scraper', 'sub': 'Data collection'},
    {'id': 'log',      'icon': '≡', 'label': 'Log',     'sub': 'Activity'},
]


@ui.page('/')
def index():
    global _refresh_circuits

    ui.add_css(CSS)
    ui.dark_mode().enable()

    pages           = {}
    nav_els         = {}   # page_id → nav item div element
    _reload_library = None

    def navigate(page_id: str):
        APP['active_page'] = page_id
        for pid, nav_el in nav_els.items():
            if pid == page_id:
                nav_el.classes(add='active', remove='')
            else:
                nav_el.classes(remove='active')
        for pid, container in pages.items():
            container.set_visibility(pid == page_id)
        if page_id == 'library' and _reload_library:
            asyncio.ensure_future(_reload_library())
        if page_id == 'warehouse' and _reload_warehouse:
            asyncio.ensure_future(_reload_warehouse())

    # ── Sidebar ──────────────────────────────────────────────────────────
    with ui.left_drawer(value=True).props('permanent width=160 bordered') \
         .style('padding:0; overflow:hidden;'):

        # Logo
        with ui.element('div').style(
            'display:flex; align-items:center; gap:8px; '
            'padding:14px 12px 12px; border-bottom:1px solid var(--border); flex-shrink:0;'
        ):
            with ui.element('div').style(
                'width:28px; height:28px; border-radius:7px; flex-shrink:0; '
                'background:linear-gradient(135deg,#f5a623,#d4820f); '
                'display:flex; align-items:center; justify-content:center; '
                'font-size:13px; color:#fff; font-weight:700;'
            ):
                ui.label('✈')
            with ui.element('div'):
                ui.label('AM').style(
                    'font-size:13px; font-weight:700; color:var(--text-hi); '
                    'font-family:DM Sans,sans-serif; letter-spacing:0.8px; display:block;'
                )
                ui.label('Revenue Optimizer').style(
                    'font-size:8px; color:var(--text-dim); '
                    'font-family:DM Mono,monospace; letter-spacing:0.4px; display:block;'
                )

        # Status dots
        with ui.element('div').style(
            'display:flex; flex-direction:column; gap:5px; padding:8px 12px; '
            'border-bottom:1px solid var(--border); flex-shrink:0;'
        ):
            def _dot_row(label):
                with ui.element('div').style('display:flex; align-items:center; gap:6px;') as row:
                    dot = ui.element('div').style(
                        'width:7px; height:7px; border-radius:50%; background:#334155; flex-shrink:0;'
                    )
                    ui.label(label).style(
                        'font-size:10px; color:#64748b; font-family:DM Mono,monospace;'
                    )
                return dot

            chrome_dot  = _dot_row('Chrome')
            planner_dot = _dot_row('Planner')

            with ui.element('div').style('display:flex; align-items:center; gap:6px;'):
                circuit_dot   = ui.element('div').style(
                    'width:7px; height:7px; border-radius:50%; background:#334155; flex-shrink:0;'
                )
                circuit_label = ui.label('0C').style(
                    'font-size:10px; color:#64748b; font-family:DM Mono,monospace;'
                )

        _cdp_cache = {'v': False, 't': 0.0}

        def _update_dots():
            import time
            now = time.monotonic()
            if now - _cdp_cache['t'] > 4.0:
                _cdp_cache['v'] = cdp_up()
                _cdp_cache['t'] = now
            chrome_up = _cdp_cache['v']
            plan_run  = APP.get('planner_running', False)
            n_circ    = len(APP.get('circuits', []))

            chrome_dot.style(
                f'width:7px; height:7px; border-radius:50%; flex-shrink:0; '
                f'background:{"#22c55e" if chrome_up else "#ef4444"};'
            )
            planner_dot.style(
                f'width:7px; height:7px; border-radius:50%; flex-shrink:0; '
                f'background:{"#f5a623" if plan_run else "#334155"};'
                + ('; animation:pulseDot 1.5s ease-in-out infinite;' if plan_run else '')
            )
            circuit_dot.style(
                f'width:7px; height:7px; border-radius:50%; flex-shrink:0; '
                f'background:{"#22d3ee" if n_circ > 0 else "#334155"};'
            )
            circuit_label.set_text(f'{n_circ}C')

        ui.timer(3.0, _update_dots)

        # Nav items
        with ui.element('div').style(
            'flex:1; overflow-y:auto; padding:6px; display:flex; flex-direction:column; gap:1px;'
        ):
            for item in NAV_ITEMS:
                pid = item['id']
                is_active = (pid == 'library')
                with ui.element('div') \
                     .classes('am-nav-item' + (' active' if is_active else '')) \
                     .on('click', lambda _p=pid: navigate(_p)) as nav_el:
                    ui.label(item['icon']).classes('am-nav-icon')
                    with ui.element('div').style('flex:1; min-width:0;'):
                        ui.label(item['label']).classes('am-nav-label')
                        ui.label(item['sub']).classes('am-nav-sub')
                    ui.element('div').classes('am-nav-indicator')
                nav_els[pid] = nav_el

        # Bottom: version + clock
        with ui.element('div').style(
            'padding:10px 12px; border-top:1px solid var(--border); flex-shrink:0; '
            'display:flex; flex-direction:column; gap:3px;'
        ):
            ui.label('v1.0').style(
                'font-size:8px; color:var(--text-dim2); '
                'font-family:DM Mono,monospace; letter-spacing:0.4px;'
            )
            clock_el = ui.label('').style(
                'font-size:12px; color:var(--text-dim); '
                'font-family:DM Mono,monospace; letter-spacing:0.8px;'
            )
            ui.timer(1.0, lambda: clock_el.set_text(
                datetime.datetime.now().strftime('%H:%M:%S')
            ))

    # ── Page containers ───────────────────────────────────────────────────

    hub_div = ui.element('div').classes('am-page')
    hub_div.set_visibility(False)
    hub_page.build(hub_div)
    pages['hub'] = hub_div

    plan_div = ui.element('div').classes('am-page')
    plan_div.set_visibility(False)
    planner_page.build(plan_div, on_circuits_ready)
    pages['plan'] = plan_div

    circ_div = ui.element('div').classes('am-page')
    circ_div.set_visibility(False)
    _refresh_circuits = circuits_page.build(circ_div, lambda: APP['circuits'])
    pages['circuits'] = circ_div

    lib_div = ui.element('div').classes('am-page')
    _reload_library = library_page.build(lib_div)
    pages['library'] = lib_div

    mass_div = ui.element('div').classes('am-page')
    mass_div.set_visibility(False)
    mass_page.build(mass_div)
    pages['mass'] = mass_div

    warehouse_div = ui.element('div').classes('am-page')
    warehouse_div.set_visibility(False)
    _reload_warehouse = warehouse_page.build(warehouse_div)
    pages['warehouse'] = warehouse_div

    scr_div = ui.element('div').classes('am-page')
    scr_div.set_visibility(False)
    scraper_page.build(scr_div)
    pages['scraper'] = scr_div

    log_div = ui.element('div').classes('am-page')
    log_div.set_visibility(False)
    log_page.build(log_div)
    pages['log'] = log_div

    # Alt+1-6 shortcuts
    def _kb(e):
        if e.args.get('altKey') and not e.args.get('ctrlKey'):
            key = e.args.get('key', '')
            mapping = {'1':'hub','2':'plan','3':'circuits','4':'library','5':'mass','6':'warehouse','7':'scraper','8':'log'}
            if key in mapping:
                navigate(mapping[key])

    ui.on('keydown', _kb)


def main():
    ui.run(
        native=True,
        window_size=(1280, 820),
        title='AM Revenue Optimizer',
        dark=True,
        reload=False,
        show=False,
    )


if __name__ == '__main__':
    main()
