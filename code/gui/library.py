"""Library page — circuit management with workflow buttons."""

import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nicegui import ui, run
from gui.workers import run_subprocess, launch_chrome
from db import (
    saved_circuits, update_circuit_status, delete_saved_circuit,
    load_saved_circuit, circuit_exists, list_hubs_with_routes, list_all_models,
)
from gui.logbuf import add as log_add

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from circuit_planner import ideal_eco, ideal_bus, ideal_fir, ideal_cargo, supersim_price
from masstool import fetch_masstool_hub, compute_route_summary
from db import get_player_hub_id
from cdp import CDP, get_am_tab


STATUS_FILTERS = [('all', 'All'), ('planned', 'Planned'), ('bought', 'Bought'), ('completed', 'Completed')]

COLS = [
    {'name': 'name',    'label': 'CIRCUIT',   'field': 'name',    'align': 'left',  'sortable': True},
    {'name': 'hub',     'label': 'HUB',       'field': 'hub',     'align': 'left',  'sortable': True},
    {'name': 'ac',      'label': 'AC',        'field': 'ac',      'align': 'left',  'sortable': True},
    {'name': 'iata',    'label': 'IATA',      'field': 'iata',    'align': 'left',  'sortable': True},
    {'name': 'routes',  'label': 'ROUTES',    'field': 'routes',  'align': 'right', 'sortable': True},
    {'name': 'waves',   'label': 'WAVES',     'field': 'waves',   'align': 'right', 'sortable': True},
    {'name': 'weekly',  'label': 'WEEKLY',    'field': 'weekly',  'align': 'right', 'sortable': True},
    {'name': 'total',   'label': 'TOTAL INV', 'field': 'total',   'align': 'right'},
    {'name': 'payback', 'label': 'PAYBACK',   'field': '_payback_raw', 'align': 'right', 'sortable': True},
    {'name': 'status',  'label': 'STATUS',    'field': 'status',  'align': 'left',  'sortable': True},
    {'name': 'created', 'label': 'CREATED',   'field': 'created', 'align': 'left',  'sortable': True},
]


def _count_routes(name: str) -> int:
    from db import get_db
    return get_db().execute(
        'SELECT COUNT(*) FROM circuit_routes WHERE circuit_name = ?', (name,)
    ).fetchone()[0]


def _waves_disp(ws, wb, wp) -> str:
    if ws == wp and wb == wp: return str(wp)
    if ws == wb and (ws or wb): return f'{ws}/{wp}'
    if ws or wb: return f'{ws}/{wb}/{wp}'
    return str(wp)


def _fmt_money(n) -> str:
    if not n: return '—'
    if n >= 1e9: return f'${n/1e9:.2f}B'
    if n >= 1e6: return f'${n/1e6:.1f}M'
    return f'${n:,.0f}'


def _circuit_row(r: dict) -> dict:
    ws = r.get('waves_scheduled') or 0
    wb = r.get('waves_bought') or 0
    wp = r.get('waves') or 0
    ac_inv    = r.get('investment') or 0
    route_inv = r.get('route_investment') or 0
    total_inv = ac_inv + route_inv
    weekly    = r.get('weekly_rev') or 0
    if weekly > 0 and total_inv > 0:
        weeks = total_inv / weekly
        payback = f'{weeks:.1f}w' if weeks < 52 else f'{weeks/52:.1f}y'
    else:
        payback = '—'
    return {
        'name':    r['name'],
        'hub':     r['hub_iata'],
        'ac':      r.get('aircraft_name') or r['aircraft_model'],
        'iata':    r.get('aircraft_icao') or r['aircraft_model'],
        'routes':  _count_routes(r['name']),
        'waves':   _waves_disp(ws, wb, wp),
        'weekly':  _fmt_money(weekly),
        'total':   _fmt_money(total_inv),
        'payback': payback,
        'status':  r.get('status') or '—',
        'created': (r.get('created_at') or '')[:10],
        '_status': r.get('status', ''),
        '_weekly_raw':  weekly,
        '_total_raw':   total_inv,
        '_payback_raw': (total_inv / weekly) if (weekly > 0 and total_inv > 0) else 1e9,
    }


def _fmt_compact_money(n: float) -> str:
    if not n: return '—'
    if n >= 1e6: return f'${n/1e6:.1f}M'
    if n >= 1e3: return f'${n/1e3:.0f}K'
    return f'${n:,.0f}'


def _detail_rows(c: dict, live: dict | None = None,
                 flights_per_week: int = 0) -> tuple[list, list]:
    """Build (cols, rows) for the detail table.

    If `live` (dict keyed by destination IATA) is provided, extra columns are
    appended showing remaining demand, current price (eco), current weekly
    revenue, and flights/week — sourced from the masstool page.
    """
    cfg    = c.get('cfg')
    waves  = c.get('waves') or 0
    routes = c.get('routes', [])
    live = live or {}

    def _enrich(row: dict, dest: str) -> dict:
        if not live: return row
        d = live.get(dest)
        if not d:
            row.update({'rem_e': '—', 'rem_c': '—',
                        'cur_p': '—', 'cur_rev': '—', 'flt_w': '—'})
            return row
        s = compute_route_summary(d)
        row.update({
            'rem_e':   f'{s["remaining_eco"]:,}',
            'rem_c':   f'{s["remaining_cargo"]:,}',
            'cur_p':   f'${s["current_price_eco"]:,}',
            'cur_rev': _fmt_compact_money(s['weekly_revenue']),
            'flt_w':   str(flights_per_week) if flights_per_week else '—',
        })
        return row

    live_cols = [
        {'name': 'rem_e',   'label': 'REM E',    'field': 'rem_e',   'align': 'right'},
        {'name': 'rem_c',   'label': 'REM C',    'field': 'rem_c',   'align': 'right'},
        {'name': 'cur_p',   'label': 'CUR P$',   'field': 'cur_p',   'align': 'right'},
        {'name': 'cur_rev', 'label': 'CUR $/W',  'field': 'cur_rev', 'align': 'right'},
        {'name': 'flt_w',   'label': 'FLT/W',    'field': 'flt_w',   'align': 'right'},
    ] if live else []

    if cfg and waves:
        comfort, speed = 500, 700
        rows = []
        for r in sorted(routes, key=lambda x: -x['dist']):
            p_eco   = ideal_eco(r['dist'], comfort)
            p_bus   = ideal_bus(r['dist'], comfort)
            p_fir   = ideal_fir(r['dist'], comfort)
            p_cargo = ideal_cargo(r['dist'], speed)
            cap_eco   = 2 * cfg['eco']   * waves
            cap_bus   = 2 * cfg['bus']   * waves
            cap_fir   = 2 * cfg['fir']   * waves
            cap_cargo = 2 * cfg['cargo'] * waves
            ss_eco   = supersim_price(p_eco,   cap_eco,   r['eco_d'])   if cfg['eco']   else 0
            ss_bus   = supersim_price(p_bus,   cap_bus,   r['bus_d'])   if cfg['bus']   else 0
            ss_fir   = supersim_price(p_fir,   cap_fir,   r['fir_d'])   if cfg['fir']   else 0
            ss_cargo = supersim_price(p_cargo, cap_cargo, r['cargo_d']) if cfg['cargo'] else 0
            daily = (
                min(cap_eco,   r['eco_d'])   * ss_eco
                + min(cap_bus,   r['bus_d'])   * ss_bus
                + min(cap_fir,   r['fir_d'])   * ss_fir
                + min(cap_cargo, r['cargo_d']) * ss_cargo
            )
            def _fmt(seats, ss):
                return f'${ss:,.0f}' if seats > 0 else '—'
            rows.append(_enrich({
                'iata': r['iata'], 'name': r.get('name', ''),
                'own':  '✓' if r.get('is_owned') else '—',
                'dist': f'{r["dist"]:,}km', 'ft': f'{r["ft"]:.2f}h',
                'eco':  _fmt(cfg['eco'],   ss_eco),
                'bus':  _fmt(cfg['bus'],   ss_bus),
                'fir':  _fmt(cfg['fir'],   ss_fir),
                'cargo':_fmt(cfg['cargo'], ss_cargo),
                'daily':f'${daily:,.0f}',
            }, r['iata']))
        cols = [
            {'name': 'iata',  'label': 'IATA',    'field': 'iata',  'align': 'left'},
            {'name': 'own',   'label': 'OWN',     'field': 'own',   'align': 'center'},
            {'name': 'name',  'label': 'DEST',     'field': 'name',  'align': 'left'},
            {'name': 'dist',  'label': 'DIST',     'field': 'dist',  'align': 'right'},
            {'name': 'ft',    'label': 'FT',       'field': 'ft',    'align': 'right'},
            {'name': 'eco',   'label': 'ECO $',    'field': 'eco',   'align': 'right'},
            {'name': 'bus',   'label': 'BUS $',    'field': 'bus',   'align': 'right'},
            {'name': 'fir',   'label': 'FIR $',    'field': 'fir',   'align': 'right'},
            {'name': 'cargo', 'label': 'CARGO $',  'field': 'cargo', 'align': 'right'},
            {'name': 'daily', 'label': 'DAILY $',  'field': 'daily', 'align': 'right'},
        ] + live_cols
        return cols, rows
    else:
        cols = [
            {'name': 'iata',  'label': 'IATA',      'field': 'iata',  'align': 'left'},
            {'name': 'own',   'label': 'OWN',       'field': 'own',   'align': 'center'},
            {'name': 'name',  'label': 'DEST',       'field': 'name',  'align': 'left'},
            {'name': 'dist',  'label': 'DIST',       'field': 'dist',  'align': 'right'},
            {'name': 'ft',    'label': 'FT',         'field': 'ft',    'align': 'right'},
            {'name': 'eco',   'label': 'ECO DEM',    'field': 'eco',   'align': 'right'},
            {'name': 'bus',   'label': 'BUS DEM',    'field': 'bus',   'align': 'right'},
            {'name': 'fir',   'label': 'FIR DEM',    'field': 'fir',   'align': 'right'},
            {'name': 'cargo', 'label': 'CARGO DEM',  'field': 'cargo', 'align': 'right'},
        ] + live_cols
        rows = [
            _enrich({
                'iata': r['iata'], 'name': r.get('name', ''),
                'own':  '✓' if r.get('is_owned') else '—',
                'dist': f'{r["dist"]:,}km', 'ft': f'{r["ft"]:.2f}h',
                'eco':  f'{r["eco_d"]:,}',  'bus': f'{r["bus_d"]:,}',
                'fir':  f'{r["fir_d"]:,}',  'cargo': f'{r["cargo_d"]:,}',
            }, r['iata'])
            for r in sorted(routes, key=lambda x: -x['dist'])
        ]
        return cols, rows


def build(container):
    state = {
        'filter': 'all', 'rows': [], 'busy': False,
        'open_names': set(),
        'open_circuits': {},   # name -> circuit dict (loaded once)
        'live_by_hub': {},     # hub_iata -> live data dict (cached masstool fetch)
    }
    refs  = {}

    def _hub_live(hub_iata: str) -> dict:
        """Return cached live data for hub, or empty dict if not fetched."""
        return state['live_by_hub'].get(hub_iata.upper(), {})

    # ── helpers ──────────────────────────────────────────────────────────

    def set_status(msg: str, running: bool = False):
        log_add('library', msg)
        if 'status_lbl' in refs:
            refs['status_lbl'].set_text(msg)
            refs['status_lbl'].style(f'color:{"#f5a623" if running else "var(--text-dim)"};')
        if 'spinner' in refs:
            refs['spinner'].set_visibility(running)

    def selected_names() -> list[str]:
        sel = refs['table'].selected if 'table' in refs else []
        return [r['name'] for r in sel]

    def selected_name() -> str | None:
        names = selected_names()
        return names[0] if names else None

    def clear_selection():
        if 'table' in refs:
            refs['table'].selected = []
            refs['table'].update()
        set_status('selection cleared')

    async def reload():
        hubs    = refs['sel_hubs'].value   if 'sel_hubs'   in refs else []
        mquery  = refs['sel_models'].value if 'sel_models' in refs else ''
        stats   = refs['sel_status'].value if 'sel_status' in refs else []

        rows = await run.io_bound(saved_circuits, statuses=stats, hubs=hubs, model_query=mquery)
        state['rows'] = rows
        table_rows = [_circuit_row(r) for r in rows]
        refs['table'].rows[:] = table_rows

        if 'table' in refs:
            valid_names = {r['name'] for r in table_rows}
            refs['table'].selected = [r for r in refs['table'].selected if r.get('name') in valid_names]

        refs['table'].update()

        # Update summary stats
        earned = planned_rev = spent = outstanding = 0
        for r in rows:
            status = r.get('status') or ''
            weekly = r.get('weekly_rev') or 0
            wp     = r.get('waves') or 0
            wb     = r.get('waves_bought') or 0
            inv    = r.get('investment') or 0
            ri     = r.get('route_investment') or 0
            frac   = (wb / wp) if wp else 0
            if status == 'completed':
                earned      += weekly
                spent       += inv + ri
            elif status == 'bought':
                earned      += weekly * frac
                spent       += ri + inv * frac
                outstanding += inv * (1 - frac)
            else:
                planned_rev += weekly
                outstanding += inv + ri
        refs['stat_earned'].set_text(_fmt_money(earned))
        refs['stat_planned_rev'].set_text(_fmt_money(planned_rev))
        refs['stat_spent'].set_text(_fmt_money(spent))
        refs['stat_outstanding'].set_text(_fmt_money(outstanding))

        if not state['busy']:
            set_status(f'{len(rows)} circuit(s)')

    # ── workflow actions ──────────────────────────────────────────────────

    async def do_launch_chrome():
        state['busy'] = True
        set_status('connecting to Chrome…', running=True)
        await launch_chrome(set_status)
        state['busy'] = False
        set_status('Chrome ready')

    async def do_purchase_aircraft():
        name = selected_name()
        if not name: set_status('select a circuit first'); return
        set_status(f'purchasing aircraft for {name}…', running=True)
        state['busy'] = True
        rc, lines = await run_subprocess('aircraft_buyer.py', [name], on_progress=set_status)
        state['busy'] = False
        if rc == 0:
            set_status(f'{name}: aircraft purchased — run Number next')
            await reload()
        else:
            set_status(f'aircraft_buyer failed: {(lines[-1] if lines else "")[:100]}')

    async def do_buy_routes():
        name = selected_name()
        if not name: set_status('select a circuit first'); return
        row = next((r for r in state['rows'] if r['name'] == name), None)
        if not row: set_status(f'cannot find {name}'); return
        from db import get_db
        db = get_db()
        hub_iata = row['hub_iata']
        ph = db.execute('SELECT hub_id FROM player_hubs WHERE hub_iata=?', (hub_iata,)).fetchone()
        if not ph:
            set_status(f'no player hub_id for {hub_iata}. Seed: '
                       f"INSERT INTO player_hubs VALUES ('{hub_iata}', <id>)")
            return
        hub_id = str(ph[0])
        set_status(f'buying routes for {name} via {hub_iata}/{hub_id}…', running=True)
        state['busy'] = True
        rc, lines = await run_subprocess(
            'circuit_route_buyer.py',
            ['--circuit', name, '--hub-id', hub_id],
            on_progress=set_status,
        )
        state['busy'] = False
        set_status(f'{name}: routes bought' if rc == 0 else f'route_buyer failed: {(lines[-1] if lines else "")[:100]}')
        if rc == 0: await reload()

    async def do_reconfig():
        name = selected_name()
        if not name: set_status('select a circuit first'); return
        set_status(f'reconfiguring {name}…', running=True)
        state['busy'] = True
        rc, lines = await run_subprocess('aircraft_reconfigurator.py', ['--circuit', name], on_progress=set_status)
        state['busy'] = False
        set_status(f'{name}: reconfigured' if rc == 0 else f'reconfig failed: {(lines[-1] if lines else "")[:100]}')
        if rc == 0: await reload()

    async def do_number():
        name = selected_name()
        if not name: set_status('select a circuit first'); return
        row = next((r for r in state['rows'] if r['name'] == name), None)
        if not row: set_status(f'cannot find {name}'); return
        set_status(f'numbering aircraft for {name}…', running=True)
        state['busy'] = True
        rc, lines = await run_subprocess(
            'aircraft_numberer.py', ['--hub', row['hub_iata'], '--circuit', name],
            on_progress=set_status,
        )
        state['busy'] = False
        set_status(f'{name}: numbered' if rc == 0 else f'numberer failed: {(lines[-1] if lines else "")[:100]}')
        if rc == 0: await reload()

    async def do_schedule(only_new: bool = False):
        name = selected_name()
        if not name: set_status('select a circuit first'); return
        row = next((r for r in state['rows'] if r['name'] == name), None)
        if not row: set_status(f'cannot find {name}'); return
        mode = 'remaining' if only_new else 'all'
        set_status(f'scheduling {name} ({mode})…', running=True)
        args = ['--hub', row['hub_iata'], '--circuit', name] + (['--only-new'] if only_new else [])
        state['busy'] = True
        rc, lines = await run_subprocess('circuit_scheduler.py', args, on_progress=set_status)
        state['busy'] = False
        set_status(f'{name} scheduled' if rc == 0 else f'scheduler failed: {(lines[-1] if lines else "")[:100]}')
        if rc == 0: await reload()

    async def mark_status(status: str):
        names = selected_names()
        if not names: set_status('select a circuit first'); return
        for n in names:
            update_circuit_status(n, status)
        set_status(f'{len(names)} → {status}: {" ".join(names)}')
        await reload()

    def delete_selected():
        names = selected_names()
        if not names: set_status('select a circuit first'); return
        for n in names:
            delete_saved_circuit(n)
        if state['open_names'] & set(names):
            refs['details_container'].clear()
            state['open_names'] -= set(names)
        set_status(f'deleted {len(names)}: {" ".join(names)}')
        asyncio.ensure_future(reload())

    async def _refresh_live(name: str):
        """Fetch masstool data for the circuit's hub and re-render its panel."""
        c = state['open_circuits'].get(name)
        if not c:
            set_status(f'{name} not open'); return
        hub = (c.get('hub') or '').upper()
        if not hub:
            set_status(f'{name}: no hub'); return

        from gui.workers import cdp_up
        if not cdp_up():
            set_status('CDP not available — use Launch Chrome first'); return

        set_status(f'fetching live data for {hub}…', running=True)
        state['busy'] = True

        def _do_fetch() -> dict:
            tab = get_am_tab()
            if not tab:
                raise RuntimeError('no AM tab open')
            hub_id = get_player_hub_id(hub)
            if not hub_id:
                raise RuntimeError(f'no player_hubs entry for {hub}')
            cdp = CDP(tab['webSocketDebuggerUrl'], timeout=60)
            cdp.connect()
            try:
                return fetch_masstool_hub(cdp, hub_id)
            finally:
                cdp.close()

        try:
            data = await run.io_bound(_do_fetch)
        except Exception as e:
            state['busy'] = False
            set_status(f'live fetch failed: {e}'); return

        state['live_by_hub'][hub] = data
        state['busy'] = False

        # Re-render every open panel whose hub matches this hub so they all
        # benefit from the freshly cached data.
        _rerender_open_panels()
        n_routes = len(c.get('routes') or [])
        n_matched = sum(1 for r in c.get('routes') or [] if r['iata'] in data)
        set_status(f'live data for {hub}: {len(data)} routes, '
                   f'{n_matched}/{n_routes} matched on {name}')

    def _build_detail_panel(name: str, c: dict):
        status_cls_map = {'planned': 'am-tag-planned', 'bought': 'am-tag-bought',
                          'completed': 'am-tag-completed'}
        hub = (c.get('hub') or '').upper()
        live = _hub_live(hub)
        waves_sched = c.get('waves_scheduled') or 0
        flights_per_week = waves_sched * 14  # 2 directions × 7 days

        cur_weekly = 0
        if live:
            for r in c.get('routes') or []:
                d = live.get(r['iata'])
                if d:
                    cur_weekly += compute_route_summary(d)['weekly_revenue']

        panel = ui.element('div').classes('am-panel')
        with panel:
            with ui.element('div').style(
                'display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;'
            ):
                with ui.element('div').style('display:flex; align-items:center; gap:10px; flex-wrap:wrap;'):
                    ui.label(name).style(
                        'color:#22d3ee; font-weight:700; font-family:DM Mono,monospace;'
                    )
                    ui.label(
                        f'{c.get("hub","?")} · {c.get("ac", {}).get("alias","?")} · '
                        f'{len(c.get("routes",[]))} routes · {c.get("waves",0)} waves'
                    ).style('color:var(--text-dim); font-size:11px; font-family:DM Mono,monospace;')
                    cfg = c.get('cfg')
                    if cfg:
                        ui.label(
                            f'e{cfg["eco"]} b{cfg["bus"]} f{cfg["fir"]} c{cfg["cargo"]}'
                        ).style('color:#a855f7; font-size:11px; font-family:DM Mono,monospace;')
                with ui.element('div').style('display:flex; align-items:center; gap:8px;'):
                    ui.button('🔄 Live',
                              on_click=lambda n=name: asyncio.ensure_future(_refresh_live(n))) \
                      .props('flat no-caps no-ripple dense').classes('am-wf am-wf-cyan')
                    ui.label((c.get('status') or '—').upper()).classes(
                        f'am-tag {status_cls_map.get(c.get("status",""), "am-tag-slate")}'
                    )

            with ui.element('div').style(
                'display:grid; grid-template-columns:repeat(5,1fr); gap:10px; margin-bottom:10px;'
            ):
                def metric(label, value, color):
                    with ui.element('div'):
                        ui.label(label).classes('am-metric-label')
                        ui.label(value).classes('am-metric-value').style(f'color:{color};')

                metric('Weekly (plan)', _fmt_money(c.get('weekly_rev') or 0),   '#22c55e')
                metric('Cur Wk Rev',    _fmt_money(cur_weekly) if live else '—', '#22d3ee')
                metric('Flights/W',     str(flights_per_week) if flights_per_week else '—', '#a855f7')
                metric('Routes Inv',    _fmt_money(c.get('route_investment') or 0), '#f5a623')
                metric('AC Inv',        _fmt_money(c.get('investment') or 0),       '#f5a623')

            cols, rows = _detail_rows(c, live=live, flights_per_week=flights_per_week)
            t = ui.table(columns=cols, rows=rows, row_key='iata') \
                  .classes('w-full').style('max-height:200px;')
            t.props('dense')

    def _rerender_open_panels():
        names = list(state['open_names'])
        container = refs['details_container']
        container.clear()
        with container:
            for n in names:
                c = state['open_circuits'].get(n) or load_saved_circuit(n)
                if not c: continue
                state['open_circuits'][n] = c
                _build_detail_panel(n, c)

    def open_selected():
        names = selected_names()
        if not names: set_status('select a circuit first'); return
        container = refs['details_container']
        # Toggle: if same set already open, close all
        if set(names) == state['open_names']:
            container.clear()
            state['open_names'] = set()
            state['open_circuits'] = {}
            set_status('closed details')
            return
        container.clear()
        loaded = []
        new_circuits = {}
        with container:
            for n in names:
                c = load_saved_circuit(n)
                if not c:
                    set_status(f'failed to load {n}'); continue
                new_circuits[n] = c
                _build_detail_panel(n, c)
                loaded.append(n)
        state['open_names'] = set(loaded)
        state['open_circuits'] = new_circuits
        set_status(f'opened {len(loaded)}: {" ".join(loaded)}')

    async def do_rename():
        name = selected_name()
        if not name: set_status('select a circuit first'); return
        result = {'value': None}
        dialog = ui.dialog()
        with dialog, ui.card().style('background:var(--panel); border:1px solid var(--border); min-width:300px; padding:20px;'):
            ui.label(f'Rename {name}').style('color:var(--cyan); font-weight:600; font-size:13px; margin-bottom:12px;')
            inp = ui.input(value=name).props('dense dark outlined').classes('w-full')
            with ui.row().classes('gap-2 mt-3 justify-end'):
                ui.button('Cancel', on_click=dialog.close) \
                  .props('flat no-caps dense').classes('am-wf')
                async def _ok():
                    result['value'] = inp.value.strip().upper()
                    dialog.close()
                ui.button('OK', on_click=_ok) \
                  .props('flat no-caps dense').classes('am-wf am-wf-cyan')
        inp.on('keydown.enter', lambda: asyncio.ensure_future(_ok()))
        await dialog
        new_name = result['value']
        if not new_name or new_name == name:
            set_status('rename cancelled'); return
        if circuit_exists(new_name):
            set_status(f'rename failed: {new_name} already exists'); return
        set_status(f'renaming {name} → {new_name}…', running=True)
        state['busy'] = True
        rc, lines = await run_subprocess(
            'circuit_renamer.py', ['--old', name, '--new', new_name], on_progress=set_status
        )
        state['busy'] = False
        set_status(f'renamed {name} → {new_name}' if rc == 0 else f'rename failed: {(lines[-1] if lines else "")[:100]}')
        if rc == 0: await reload()

    # ── layout ───────────────────────────────────────────────────────────

    with container:
        # Section header
        with ui.element('div').classes('am-section-header'):
            with ui.element('div'):
                ui.label('Circuit Library').classes('am-section-title')
                ui.label('Manage saved circuits — workflow tracking').classes('am-section-sub')
            with ui.element('div').classes('am-section-actions'):
                refs['header_count'] = ui.label('').style(
                    'font-size:11px; color:var(--text-dim); font-family:DM Mono,monospace;'
                )

        # Filters
        with ui.element('div').classes('am-panel').style('margin-bottom:12px; display:flex; gap:16px; align-items:flex-end;'):
            with ui.element('div').style('display:flex; flex-direction:column; gap:4px;'):
                ui.label('HUBS').classes('am-metric-label')
                hub_opts = {h['hub_iata']: h['hub_iata'] for h in list_hubs_with_routes()}
                refs['sel_hubs'] = ui.select(hub_opts, multiple=True, label='All Hubs', on_change=reload) \
                    .props('dense dark outlined use-chips').style('width:200px;')
            
            with ui.element('div').style('display:flex; flex-direction:column; gap:4px;'):
                ui.label('MODELS').classes('am-metric-label')
                refs['sel_models'] = ui.input(label='Search (e.g. 737)',
                                              on_change=lambda: asyncio.ensure_future(reload())) \
                    .props('dense dark outlined clearable debounce=250').style('width:200px;')

            with ui.element('div').style('display:flex; flex-direction:column; gap:4px;'):
                ui.label('STATUS').classes('am-metric-label')
                status_opts = {k: v for k, v in STATUS_FILTERS if k != 'all'}
                refs['sel_status'] = ui.select(status_opts, multiple=True, label='All Statuses', on_change=reload) \
                    .props('dense dark outlined use-chips').style('width:200px;')

            ui.button('Clear', on_click=lambda: _clear_filters()) \
                .props('flat dense').classes('am-wf-danger').style('margin-bottom:4px;')

        def _clear_filters():
            refs['sel_hubs'].value = []
            refs['sel_models'].value = ''
            refs['sel_status'].value = []
            asyncio.ensure_future(reload())

        # WORKFLOW section
        with ui.element('div').style('margin-bottom:12px; flex-shrink:0;'):
            ui.label('WORKFLOW').classes('am-section-label')
            with ui.element('div').style('display:flex; gap:6px; flex-wrap:wrap;'):
                ui.button('Launch Chrome',
                          on_click=lambda: asyncio.ensure_future(do_launch_chrome())) \
                  .props('flat no-caps no-ripple dense').classes('am-wf am-wf-cyan')
                ui.button('Buy Routes',
                          on_click=lambda: asyncio.ensure_future(do_buy_routes())) \
                  .props('flat no-caps no-ripple dense').classes('am-wf')
                ui.button('Buy AC',
                          on_click=lambda: asyncio.ensure_future(do_purchase_aircraft())) \
                  .props('flat no-caps no-ripple dense').classes('am-wf')
                ui.button('Reconfig',
                          on_click=lambda: asyncio.ensure_future(do_reconfig())) \
                  .props('flat no-caps no-ripple dense').classes('am-wf')
                ui.button('Number',
                          on_click=lambda: asyncio.ensure_future(do_number())) \
                  .props('flat no-caps no-ripple dense').classes('am-wf')
                ui.button('Schedule',
                          on_click=lambda: asyncio.ensure_future(do_schedule(False))) \
                  .props('flat no-caps no-ripple dense').classes('am-wf')
                ui.button('Sched. New',
                          on_click=lambda: asyncio.ensure_future(do_schedule(True))) \
                  .props('flat no-caps no-ripple dense').classes('am-wf')

        # MANAGE section
        with ui.element('div').style('margin-bottom:12px; flex-shrink:0;'):
            ui.label('MANAGE').classes('am-section-label')
            with ui.element('div').style('display:flex; gap:6px; flex-wrap:wrap;'):
                ui.button('→ Planned',
                          on_click=lambda: asyncio.ensure_future(mark_status('planned'))) \
                  .props('flat no-caps no-ripple dense').classes('am-wf')
                ui.button('→ Bought',
                          on_click=lambda: asyncio.ensure_future(mark_status('bought'))) \
                  .props('flat no-caps no-ripple dense').classes('am-wf am-wf-cyan')
                ui.button('→ Done',
                          on_click=lambda: asyncio.ensure_future(mark_status('completed'))) \
                  .props('flat no-caps no-ripple dense').classes('am-wf am-wf-success')
                ui.button('Free Routes',
                          on_click=lambda: asyncio.ensure_future(mark_status('archived'))) \
                  .props('flat no-caps no-ripple dense').classes('am-wf am-wf-danger')
                ui.button('Open/Close',
                          on_click=open_selected) \
                  .props('flat no-caps no-ripple dense').classes('am-wf')
                ui.button('Unselect All',
                          on_click=clear_selection) \
                  .props('flat no-caps no-ripple dense').classes('am-wf')
                ui.button('Rename',
                          on_click=lambda: asyncio.ensure_future(do_rename())) \
                  .props('flat no-caps no-ripple dense').classes('am-wf')
                ui.button('Delete',
                          on_click=delete_selected) \
                  .props('flat no-caps no-ripple dense').classes('am-wf am-wf-danger')

        # Summary stat cards
        with ui.element('div').style('display:flex; gap:10px; margin-bottom:12px; flex-shrink:0; flex-wrap:wrap;'):
            with ui.element('div').classes('am-stat'):
                ui.label('EARNED WEEKLY').classes('am-stat-label')
                refs['stat_earned'] = ui.label('—').classes('am-stat-value').style('color:#22c55e;')
            with ui.element('div').classes('am-stat'):
                ui.label('PLANNED WEEKLY').classes('am-stat-label')
                refs['stat_planned_rev'] = ui.label('—').classes('am-stat-value').style('color:var(--text-dim);')
            with ui.element('div').classes('am-stat'):
                ui.label('SPENT').classes('am-stat-label')
                refs['stat_spent'] = ui.label('—').classes('am-stat-value').style('color:#f5a623;')
            with ui.element('div').classes('am-stat'):
                ui.label('OUTSTANDING').classes('am-stat-label')
                refs['stat_outstanding'] = ui.label('—').classes('am-stat-value').style('color:#22d3ee;')

        # Main table with status badge slot
        t = ui.table(columns=COLS, rows=[], row_key='name', selection='multiple') \
              .classes('w-full').style('max-height:240px; flex-shrink:0;')
        t.props('dense virtual-scroll :virtual-scroll-sticky-size-start="36"')
        t.add_slot('body-cell-status', r'''
            <q-td :props="props">
                <span :class="'am-tag am-tag-' + props.row._status">
                    {{ (props.row.status || '—').toUpperCase() }}
                </span>
            </q-td>
        ''')
        t.add_slot('body-cell-weekly', r'''
            <q-td :props="props" style="text-align:right;">
                <span style="color:#22c55e; font-weight:600;">{{ props.row.weekly }}</span>
            </q-td>
        ''')
        t.add_slot('body-cell-total', r'''
            <q-td :props="props" style="text-align:right;">
                <span style="color:#f5a623;">{{ props.row.total }}</span>
            </q-td>
        ''')
        t.add_slot('body-cell-name', r'''
            <q-td :props="props">
                <span style="color:#e2e8f0; font-weight:600;">{{ props.row.name }}</span>
            </q-td>
        ''')
        t.add_slot('body-cell-payback', r'''
            <q-td :props="props" style="text-align:right;">
                <span style="color:#94a3b8;">{{ props.row.payback }}</span>
            </q-td>
        ''')
        t.add_slot('body-cell-ac', r'''
            <q-td :props="props">
                <span style="color:#22d3ee;">{{ props.row.ac }}</span>
            </q-td>
        ''')
        refs['table'] = t

        # Detail container — one panel per opened circuit
        details_container = ui.element('div').style(
            'display:flex; flex-direction:column; gap:10px; margin-top:12px; flex-shrink:0;'
        )
        refs['details_container'] = details_container

        # Status bar
        with ui.element('div').classes('am-status-bar'):
            refs['spinner'] = ui.element('div').classes('am-spinner')
            refs['spinner'].set_visibility(False)
            refs['status_lbl'] = ui.label('Ready').style('color:var(--text-dim);')

        asyncio.ensure_future(reload())
        return reload
