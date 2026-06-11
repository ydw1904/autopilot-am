"""Planner page — circuit search form + streaming results."""

import asyncio, os, sys, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nicegui import ui, run
from gui.state import APP
from db import DB

_PARAM_FIELDS = [
    ('hub',        'HUB',              ''),
    ('aircraft',   'AIRCRAFT',         'B742'),
    ('circuits',   'CIRCUITS',         '5'),
    ('owned',      'EXCLUDE IATAS',    ''),
    ('beam',       'BEAM WIDTH',       '1200'),
    ('match',      'MATCH RATIO',      '0.90'),
    ('comfort',    'COMFORT',          '500'),
    ('speed',      'SPEED',            '700'),
    ('waves',      'MAX WAVES',        '30'),
    ('max_routes', 'MAX ROUTES',       '150'),
    ('overshoot',  'OVERSHOOT %',      '0'),
]


def build(container, on_circuits_ready):
    refs = {}

    def _get(fid: str) -> str:
        return refs[fid].value.strip() if fid in refs else ''

    def _int(fid: str, default: int) -> int:
        try: return int(_get(fid))
        except ValueError: return default

    def _float(fid: str, default: float) -> float:
        try: return float(_get(fid))
        except ValueError: return default

    def set_status(msg: str, running: bool = False):
        refs['status_lbl'].set_text(msg)
        refs['status_lbl'].style(f'color:{"#f5a623" if running else "var(--text-dim)"};')
        refs['spinner'].set_visibility(running)

    async def run_planner():
        from circuit_planner import load_aircraft, load_routes, search_circuits, optimize_circuit
        from db import locked_route_iatas

        hub = _get('hub').upper()
        if not hub: set_status('hub required'); return
        aircraft_names = _get('aircraft').split()
        if not aircraft_names: set_status('aircraft required'); return

        n_circuits = _int('circuits', 5)
        beam       = _int('beam', 1200)
        match      = _float('match', 0.90)
        comfort    = _float('comfort', 500)
        speed      = _float('speed', 700)
        max_waves  = _int('waves', 20)
        max_routes = _int('max_routes', 150)
        overshoot  = _float('overshoot', 0.0) / 100.0  # field is percent
        owned      = _get('owned').split()
        score_mode = refs['mode'].value if 'mode' in refs else 'revenue'

        refs['btn_run'].disable()
        set_status('starting…', running=True)
        refs['table'].rows[:] = []
        refs['table'].selected = []
        refs['table'].update()
        APP['planner_running'] = True

        all_circuits = []
        loop = asyncio.get_event_loop()
        # check_same_thread=False: this connection is used across executor calls
        db = await loop.run_in_executor(None, lambda: sqlite3.connect(DB, check_same_thread=False))

        try:
            ac_list = []
            for name in aircraft_names:
                ac = await loop.run_in_executor(None, lambda n=name: load_aircraft(db, n))
                if ac: ac_list.append(ac)
            if not ac_list:
                set_status('no valid aircraft found')
                return

            exclude = set(x.upper() for x in owned)
            exclude.add(hub)
            for oh in owned:
                has = await loop.run_in_executor(
                    None, lambda h=oh: db.execute(
                        'SELECT 1 FROM routes WHERE hub_iata=? AND dest_iata=? LIMIT 1',
                        (h.upper(), hub)
                    ).fetchone()
                )
                if has: exclude.add(oh.upper())

            # locked_route_iatas uses the global DB connection (main thread) — call directly
            auto_locked = locked_route_iatas(hub=hub, statuses=('completed', 'bought'))
            if auto_locked:
                set_status(f'auto-excluded {len(auto_locked)} locked routes', running=True)
                exclude |= auto_locked

            used: set = set()
            for n in range(1, n_circuits + 1):
                cur = exclude | used
                candidates = []
                for ac in ac_list:
                    routes = await loop.run_in_executor(
                        None, lambda a=ac: load_routes(db, hub, a, exclude_iatas=cur)
                    )
                    if len(routes) < 2: continue
                    set_status(
                        f'circuit {n}/{n_circuits} — searching {ac["alias"]} ({len(routes)} routes)',
                        running=True,
                    )
                    results = await loop.run_in_executor(
                        None, lambda r=routes, a=ac: search_circuits(
                            r, a, comfort, speed,
                            top_n=3, beam_width=beam,
                            max_routes=max_routes, max_waves=max_waves, match=match,
                            score_mode=score_mode, overshoot_pct=overshoot,
                        )
                    )
                    for score, total_time, indices in results:
                        candidates.append((score, total_time, [routes[i] for i in indices], ac))

                if not candidates:
                    set_status(f'no viable circuits after #{n-1}')
                    break

                candidates.sort(key=lambda x: -x[0])
                p1, tt, rl, best_ac = candidates[0]
                used |= {r['iata'] for r in rl}

                set_status(f'circuit {n}/{n_circuits} — optimizing seats…', running=True)
                cfg, waves, daily_rev, breakdown = await loop.run_in_executor(
                    None, lambda: optimize_circuit(
                        rl, best_ac, comfort=comfort, speed=speed,
                        max_waves=max_waves, overshoot_pct=overshoot,
                    )
                )
                cdata = {
                    'num': n, 'hub': hub, 'ac': best_ac, 'routes': rl,
                    'total_time': tt, 'cfg': cfg, 'waves': waves,
                    'daily_rev': daily_rev, 'weekly_rev': daily_rev * 7 if daily_rev else 0,
                    'breakdown': breakdown, 'p1_score': p1,
                }
                all_circuits.append(cdata)

                if cfg:
                    planes  = waves * 7
                    cfg_str = f'e{cfg["eco"]} b{cfg["bus"]} f{cfg["fir"]} c{cfg["cargo"]}'
                    inv     = planes * best_ac['price']
                    pb      = inv / daily_rev if daily_rev else 0
                    refs['table'].add_rows([{
                        'num': str(n), 'ac': best_ac['alias'],
                        'routes': str(len(rl)), 'time': f'{tt:.1f}h',
                        'cfg': cfg_str, 'waves': str(waves), 'aircraft': str(planes),
                        'daily':  f'${daily_rev:,.0f}',
                        'weekly': f'${daily_rev*7:,.0f}',
                        'invest': f'${inv:,.0f}',
                        'pb':     f'{pb:.0f}d',
                    }])
                else:
                    refs['table'].add_rows([{
                        'num': str(n), 'ac': best_ac['alias'],
                        'routes': str(len(rl)), 'time': f'{tt:.1f}h',
                        'cfg': '—', 'waves': '—', 'aircraft': '—',
                        'daily': f'${p1:,.0f}',
                        'weekly': '—', 'invest': '—', 'pb': '—',
                    }])
                refs['table'].update()
                set_status(
                    f'circuit {n}/{n_circuits} done — ' + ' '.join(r['iata'] for r in rl),
                    running=True,
                )

        finally:
            # Close in an executor thread to match the thread that created the connection
            await loop.run_in_executor(None, db.close)
            refs['btn_run'].enable()
            APP['planner_running'] = False

        set_status(f'done — {len(all_circuits)} circuits found')
        refs['_circuits'] = all_circuits
        on_circuits_ready(all_circuits)

    async def save_selected():
        from db import save_circuit_full
        sel = refs['table'].selected
        if not sel: set_status('no row selected'); return
        circuits = refs.get('_circuits', [])
        if not circuits: set_status('re-run planner first'); return
        custom = refs['save_name'].value.strip().upper() or None
        names = []
        for row in sel:
            idx = int(row['num']) - 1
            if idx >= len(circuits): continue
            c_name = custom if (custom and len(sel) == 1) else None
            try:
                name = await run.io_bound(save_circuit_full, circuits[idx], custom_name=c_name)
                names.append(name)
            except Exception as e:
                set_status(f'save failed: {e}'); return
        set_status(f'saved {len(names)}: {", ".join(names)}')
        if custom and len(sel) == 1: refs['save_name'].value = ''

    async def save_all():
        from db import save_circuit_full
        circuits = refs.get('_circuits', [])
        if not circuits: set_status('nothing to save'); return
        names = []
        for c in circuits:
            try:
                name = await run.io_bound(save_circuit_full, c)
                names.append(name)
            except Exception as e:
                set_status(f'save failed: {e}'); return
        set_status(f'saved {len(names)}: {", ".join(names)}')

    with container:
        # Section header
        with ui.element('div').classes('am-section-header'):
            with ui.element('div'):
                ui.label('Circuit Planner').classes('am-section-title')
                ui.label('Find optimal circuits — beam search over route database').classes('am-section-sub')
            with ui.element('div').classes('am-section-actions'):
                refs['btn_run'] = ui.button('▶  Run', on_click=run_planner) \
                    .props('flat no-caps no-ripple dense').classes('am-wf am-wf-success')
                refs['save_name'] = ui.input(placeholder='custom name (opt.)') \
                    .props('dense dark outlined').style('width:160px; font-size:11px;')
                ui.button('Save row', on_click=save_selected) \
                    .props('flat no-caps no-ripple dense').classes('am-wf')
                ui.button('Save all', on_click=save_all) \
                    .props('flat no-caps no-ripple dense').classes('am-wf')

        # Parameter panel
        with ui.element('div').classes('am-panel').style('margin-bottom:14px; flex-shrink:0;'):
            with ui.element('div').style(
                'display:grid; grid-template-columns:repeat(5,1fr); gap:10px 16px;'
            ):
                for fid, label, default in _PARAM_FIELDS:
                    with ui.element('div').style('display:flex; flex-direction:column; gap:4px;'):
                        ui.label(label).style(
                            'font-size:10px; color:var(--text-dim); '
                            'font-family:DM Mono,monospace; letter-spacing:0.5px;'
                        )
                        inp = ui.input(value=default).props('dense dark outlined').style(
                            'font-family:DM Mono,monospace; font-size:12px;'
                        )
                        refs[fid] = inp

            with ui.element('div').style('margin-top:10px; display:flex; align-items:center; gap:12px;'):
                ui.label('OPTIMIZE FOR').style(
                    'font-size:10px; color:var(--text-dim); '
                    'font-family:DM Mono,monospace; letter-spacing:0.5px;'
                )
                refs['mode'] = ui.select(
                    {'revenue': 'Max Revenue', 'roi': 'Best ROI / Payback'},
                    value='revenue',
                ).props('dense dark outlined').style('font-size:12px; min-width:200px;')

        # Results table
        cols = [
            {'name': 'num',      'label': '#',        'field': 'num',      'align': 'right', 'sortable': True},
            {'name': 'ac',       'label': 'AC',        'field': 'ac',       'align': 'left',  'sortable': True},
            {'name': 'routes',   'label': 'ROUTES',    'field': 'routes',   'align': 'right', 'sortable': True},
            {'name': 'time',     'label': 'TIME',      'field': 'time',     'align': 'right', 'sortable': True},
            {'name': 'cfg',      'label': 'CONFIG',    'field': 'cfg',      'align': 'left'},
            {'name': 'waves',    'label': 'WAVES',     'field': 'waves',    'align': 'right', 'sortable': True},
            {'name': 'aircraft', 'label': 'AIRCRAFT',  'field': 'aircraft', 'align': 'right', 'sortable': True},
            {'name': 'daily',    'label': 'DAILY',     'field': 'daily',    'align': 'right', 'sortable': True},
            {'name': 'weekly',   'label': 'WEEKLY',    'field': 'weekly',   'align': 'right', 'sortable': True},
            {'name': 'invest',   'label': 'INVEST',    'field': 'invest',   'align': 'right', 'sortable': True},
            {'name': 'pb',       'label': 'PAYBACK',   'field': 'pb',       'align': 'right', 'sortable': True},
        ]
        t = ui.table(columns=cols, rows=[], row_key='num', selection='multiple') \
              .classes('w-full').style('flex:1; min-height:250px;')
        t.props('dense virtual-scroll')
        t.add_slot('body-cell-weekly', r'''
            <q-td :props="props" style="text-align:right;">
                <span style="color:#22c55e; font-weight:600;">{{ props.row.weekly }}</span>
            </q-td>
        ''')
        refs['table'] = t

        # Status bar
        with ui.element('div').classes('am-status-bar'):
            refs['spinner'] = ui.element('div').classes('am-spinner')
            refs['spinner'].set_visibility(False)
            refs['status_lbl'] = ui.label('Ready').style('color:var(--text-dim);')
