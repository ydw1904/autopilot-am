"""Mass Tools — bulk operations across multiple circuits or hubs."""

import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nicegui import ui, run
from gui.workers import run_subprocess
from db import saved_circuits, get_db, list_hubs_with_routes, list_all_models
from gui.logbuf import add as log_add


def build(container):
    state = {'busy': False, 'selected': set()}
    refs = {}

    def set_status(msg: str, running: bool = False):
        log_add('mass', msg)
        if 'status_lbl' in refs:
            refs['status_lbl'].set_text(msg)
            refs['status_lbl'].style(
                f'color:{"#f5a623" if running else "var(--text-dim)"};'
            )
        if 'spinner' in refs:
            refs['spinner'].set_visibility(running)

    # ── Selection helpers ─────────────────────────────────────────────────

    def selected_rows() -> list[dict]:
        return refs['table'].selected if 'table' in refs else []

    def selected_names() -> list[str]:
        return [r['name'] for r in selected_rows()]

    # ── Reload circuit table ──────────────────────────────────────────────

    async def reload():
        hubs   = refs['sel_hubs'].value   if 'sel_hubs'   in refs else []
        mquery = refs['sel_models'].value if 'sel_models' in refs else ''
        stats  = refs['sel_status'].value if 'sel_status' in refs else []

        rows = await run.io_bound(saved_circuits, statuses=stats, hubs=hubs, model_query=mquery)
        table_rows = []
        for r in rows:
            wp = r.get('waves') or 0
            wb = r.get('waves_bought') or 0
            ws = r.get('waves_scheduled') or 0
            table_rows.append({
                'name':   r['name'],
                'hub':    r['hub_iata'],
                'ac':     r.get('aircraft_name') or r['aircraft_model'],
                'iata':   r.get('aircraft_icao') or r['aircraft_model'],
                'waves':  f'{ws}/{wb}/{wp}' if (ws or wb) else str(wp),
                'status': r.get('status') or '—',
                '_status': r.get('status', ''),
            })
        refs['table'].rows[:] = table_rows

        if 'table' in refs:
            valid_names = {r['name'] for r in table_rows}
            refs['table'].selected = [r for r in refs['table'].selected if r.get('name') in valid_names]

        refs['table'].update()
        if not state['busy']:
            set_status(f'{len(rows)} circuits loaded')

    # ── Bulk runners ──────────────────────────────────────────────────────

    async def _run_each(script: str, mk_args, label: str):
        """Run `script` once per selected circuit. mk_args(row) -> list[str]."""
        rows = selected_rows()
        if not rows:
            set_status('select circuits first'); return
        state['busy'] = True
        n_ok = n_fail = 0
        for i, row in enumerate(rows, 1):
            name = row['name']
            set_status(f'[{i}/{len(rows)}] {label}: {name}…', running=True)
            args = mk_args(row)
            rc, _ = await run_subprocess(script, args, on_progress=set_status)
            if rc == 0: n_ok += 1
            else:       n_fail += 1
        state['busy'] = False
        set_status(f'{label}: ok={n_ok} fail={n_fail}')
        asyncio.ensure_future(reload())

    def do_reconfig():
        return _run_each(
            'aircraft_reconfigurator.py',
            lambda r: ['--circuit', r['name']],
            'reconfig',
        )

    def do_number():
        return _run_each(
            'aircraft_numberer.py',
            lambda r: ['--hub', r['hub'], '--circuit', r['name']],
            'number',
        )

    def do_schedule(only_new=False):
        extra = ['--only-new'] if only_new else []
        return _run_each(
            'circuit_scheduler.py',
            lambda r: ['--hub', r['hub'], '--circuit', r['name']] + extra,
            'schedule' + (' (new)' if only_new else ''),
        )

    def do_buy_ac():
        return _run_each(
            'aircraft_buyer.py',
            lambda r: [r['name']],
            'buy AC',
        )

    async def do_rename(dry_run: bool):
        old = (refs['rename_old'].value or '').strip()
        new = (refs['rename_new'].value or '').strip()
        if not old or not new:
            set_status('OLD and NEW prefixes required'); return
        args = ['--old', old, '--new', new] + (['--dry-run'] if dry_run else [])
        label = f'rename{"-dry" if dry_run else ""}'
        set_status(f'{label} {old} → {new}…', running=True)
        state['busy'] = True
        rc, lines = await run_subprocess('mass_renamer.py', args, on_progress=set_status)
        state['busy'] = False
        tail = (lines[-1] if lines else '')[:120]
        set_status(f'{label}: {"ok" if rc==0 else "FAIL"} — {tail}')

    async def do_unschedule(dry_run: bool):
        raw = (refs['unsched_prefixes'].value or '').strip()
        if not raw:
            set_status('enter at least one prefix'); return
        prefixes = raw.split()
        args = list(prefixes) + (['--dry-run'] if dry_run else [])
        label = 'unsched(dry)' if dry_run else 'unschedule'
        set_status(f'{label}: starting…', running=True)
        state['busy'] = True
        rc, lines = await run_subprocess('mass_unscheduler.py', args, on_progress=set_status)
        state['busy'] = False
        tail = (lines[-1] if lines else '')[:120]
        set_status(f'{label}: {"ok" if rc==0 else "FAIL"} — {tail}')

    async def do_auto_price(dry_run: bool):
        hub  = (refs['price_hub'].value or '').strip().upper()
        mode = refs['price_mode'].value or 'ideal'
        pct  = refs['price_pct'].value or 100
        mx   = int(refs['price_max'].value or 0)
        args = ['--mode', mode, '--pct', str(pct), '--skip-unchanged']
        if hub: args += ['--hub', hub]
        if mx:  args += ['--max', str(mx)]
        if dry_run: args.append('--dry-run')
        label = f"price-{'sim' if dry_run else mode}{'@'+str(pct) if mode=='percent' else ''}"
        set_status(f'{label}: starting…', running=True)
        state['busy'] = True
        rc, lines = await run_subprocess('auto_pricer.py', args, on_progress=set_status)
        state['busy'] = False
        tail = (lines[-1] if lines else '')[:120]
        set_status(f'{label}: {"ok" if rc==0 else "FAIL"} — {tail}')

    def do_buy_routes():
        # Look up player_hub_id per circuit
        db = get_db()
        rows = selected_rows()
        if not rows:
            set_status('select circuits first'); return
        async def runner():
            state['busy'] = True
            n_ok = n_fail = 0
            for i, row in enumerate(rows, 1):
                ph = db.execute(
                    'SELECT hub_id FROM player_hubs WHERE hub_iata=?',
                    (row['hub'],),
                ).fetchone()
                if not ph:
                    set_status(f'{row["name"]}: no player_hubs entry for {row["hub"]}')
                    n_fail += 1
                    continue
                set_status(f'[{i}/{len(rows)}] buy routes: {row["name"]}…', running=True)
                rc, _ = await run_subprocess(
                    'circuit_route_buyer.py',
                    ['--circuit', row['name'], '--hub-id', str(ph[0])],
                    on_progress=set_status,
                )
                if rc == 0: n_ok += 1
                else:       n_fail += 1
            state['busy'] = False
            set_status(f'buy routes: ok={n_ok} fail={n_fail}')
            reload()
        return asyncio.ensure_future(runner())

    # ── Filter helpers ────────────────────────────────────────────────────

    def select_by(predicate, label: str):
        t = refs['table']
        t.selected = [r for r in t.rows if predicate(r)]
        t.update()
        set_status(f'selected {len(t.selected)} {label}')

    # ── Layout ────────────────────────────────────────────────────────────

    with container:
        with ui.element('div').classes('am-section-header'):
            with ui.element('div'):
                ui.label('Mass Tools').classes('am-section-title')
                ui.label('Bulk operations across multiple circuits').classes('am-section-sub')
            with ui.element('div').classes('am-section-actions'):
                ui.button('Clear Filters', on_click=lambda: _clear_filters()) \
                    .props('flat dense').classes('am-wf-danger')

        # Filters
        with ui.element('div').classes('am-panel').style('margin-bottom:12px; display:flex; gap:16px; align-items:flex-end;'):
            with ui.element('div').style('display:flex; flex-direction:column; gap:4px;'):
                ui.label('HUBS').classes('am-metric-label')
                hub_opts = {h['hub_iata']: h['hub_iata'] for h in list_hubs_with_routes()}
                refs['sel_hubs'] = ui.select(hub_opts, multiple=True, label='All Hubs', on_change=lambda: asyncio.ensure_future(reload())) \
                    .props('dense dark outlined use-chips').style('width:200px;')
            
            with ui.element('div').style('display:flex; flex-direction:column; gap:4px;'):
                ui.label('MODELS').classes('am-metric-label')
                refs['sel_models'] = ui.input(label='Search (e.g. 737)',
                                              on_change=lambda: asyncio.ensure_future(reload())) \
                    .props('dense dark outlined clearable debounce=250').style('width:200px;')

            with ui.element('div').style('display:flex; flex-direction:column; gap:4px;'):
                ui.label('STATUS').classes('am-metric-label')
                status_opts = {'planned': 'Planned', 'bought': 'Bought', 'completed': 'Completed'}
                refs['sel_status'] = ui.select(status_opts, multiple=True, label='All Statuses', on_change=lambda: asyncio.ensure_future(reload())) \
                    .props('dense dark outlined use-chips').style('width:200px;')

        def _clear_filters():
            refs['sel_hubs'].value = []
            refs['sel_models'].value = ''
            refs['sel_status'].value = []
            asyncio.ensure_future(reload())

        # Quick selection presets
        with ui.element('div').style('margin-bottom:12px; flex-shrink:0;'):
            ui.label('SELECT').classes('am-section-label')
            with ui.element('div').style('display:flex; gap:6px; flex-wrap:wrap;'):
                ui.button('All planned',
                    on_click=lambda: select_by(lambda r: r['_status'] == 'planned', 'planned')) \
                  .props('flat no-caps dense').classes('am-wf')
                ui.button('All bought',
                    on_click=lambda: select_by(lambda r: r['_status'] == 'bought', 'bought')) \
                  .props('flat no-caps dense').classes('am-wf')
                ui.button('All completed',
                    on_click=lambda: select_by(lambda r: r['_status'] == 'completed', 'completed')) \
                  .props('flat no-caps dense').classes('am-wf')
                ui.button('Hub: MPM',
                    on_click=lambda: select_by(lambda r: r['hub'] == 'MPM', 'MPM circuits')) \
                  .props('flat no-caps dense').classes('am-wf')
                ui.button('Clear',
                    on_click=lambda: select_by(lambda _r: False, '')) \
                  .props('flat no-caps dense').classes('am-wf')

        # Mass rename aircraft (old prefix -> new prefix)
        with ui.element('div').style('margin-bottom:12px; flex-shrink:0;'):
            ui.label('MASS RENAME AIRCRAFT').classes('am-section-label')
            with ui.element('div').style('display:flex; gap:8px; flex-wrap:wrap; align-items:center;'):
                refs['rename_old'] = ui.input(label='OLD prefix', placeholder='MPM-C003') \
                    .props('dense dark outlined').style('width:180px;')
                refs['rename_new'] = ui.input(label='NEW prefix', placeholder='MPM-C012') \
                    .props('dense dark outlined').style('width:180px;')
                ui.button('Rename (dry-run)',
                    on_click=lambda: asyncio.ensure_future(do_rename(True))) \
                  .props('flat no-caps dense').classes('am-wf')
                ui.button('Rename',
                    on_click=lambda: asyncio.ensure_future(do_rename(False))) \
                  .props('flat no-caps dense').classes('am-wf am-wf-cyan')

        # Mass unschedule by aircraft name prefix
        with ui.element('div').style('margin-bottom:12px; flex-shrink:0;'):
            ui.label('MASS UNSCHEDULE').classes('am-section-label')
            with ui.element('div').style('display:flex; gap:8px; flex-wrap:wrap; align-items:center;'):
                refs['unsched_prefixes'] = ui.input(
                    label='Prefixes (space-separated)',
                    placeholder='MPM-C007 SHOP-A350-900ULR',
                ).props('dense dark outlined').style('width:420px;')
                ui.button('Unschedule (dry-run)',
                    on_click=lambda: asyncio.ensure_future(do_unschedule(True))) \
                  .props('flat no-caps dense').classes('am-wf')
                ui.button('Unschedule',
                    on_click=lambda: asyncio.ensure_future(do_unschedule(False))) \
                  .props('flat no-caps dense').classes('am-wf am-wf-danger')

        # Pricing tools (global, not per-circuit)
        with ui.element('div').style('margin-bottom:12px; flex-shrink:0;'):
            ui.label('PRICING (ALL ROUTES)').classes('am-section-label')
            with ui.element('div').style('display:flex; gap:8px; flex-wrap:wrap; align-items:center;'):
                refs['price_hub']  = ui.input(label='Hub IATA (blank = all)', value='') \
                    .props('dense dark outlined').style('width:180px;')
                refs['price_mode'] = ui.select(['ideal','percent'], value='ideal', label='Mode') \
                    .props('dense dark outlined').style('width:140px;')
                refs['price_pct']  = ui.number(label='Percent', value=100, min=10, max=200, step=1) \
                    .props('dense dark outlined').style('width:120px;')
                refs['price_max']  = ui.number(label='Max routes (0=all)', value=0, min=0, step=1) \
                    .props('dense dark outlined').style('width:160px;')
                ui.button('Auto Price',
                    on_click=lambda: asyncio.ensure_future(do_auto_price(False))) \
                  .props('flat no-caps dense').classes('am-wf am-wf-cyan')

        # Bulk workflow actions
        with ui.element('div').style('margin-bottom:12px; flex-shrink:0;'):
            ui.label('BULK WORKFLOW').classes('am-section-label')
            with ui.element('div').style('display:flex; gap:6px; flex-wrap:wrap;'):
                ui.button('Buy Routes',
                    on_click=do_buy_routes) \
                  .props('flat no-caps dense').classes('am-wf')
                ui.button('Buy AC',
                    on_click=lambda: asyncio.ensure_future(do_buy_ac())) \
                  .props('flat no-caps dense').classes('am-wf')
                ui.button('Reconfig',
                    on_click=lambda: asyncio.ensure_future(do_reconfig())) \
                  .props('flat no-caps dense').classes('am-wf')
                ui.button('Number',
                    on_click=lambda: asyncio.ensure_future(do_number())) \
                  .props('flat no-caps dense').classes('am-wf')
                ui.button('Schedule',
                    on_click=lambda: asyncio.ensure_future(do_schedule(False))) \
                  .props('flat no-caps dense').classes('am-wf')
                ui.button('Schedule (new only)',
                    on_click=lambda: asyncio.ensure_future(do_schedule(True))) \
                  .props('flat no-caps dense').classes('am-wf')

        # Circuit selection table
        cols = [
            {'name': 'name',   'label': 'CIRCUIT', 'field': 'name',   'align': 'left',  'sortable': True},
            {'name': 'hub',    'label': 'HUB',     'field': 'hub',    'align': 'left',  'sortable': True},
            {'name': 'ac',     'label': 'AC',      'field': 'ac',     'align': 'left',  'sortable': True},
            {'name': 'iata',   'label': 'IATA',    'field': 'iata',   'align': 'left',  'sortable': True},
            {'name': 'waves',  'label': 'WAVES',   'field': 'waves',  'align': 'right'},
            {'name': 'status', 'label': 'STATUS',  'field': 'status', 'align': 'left',  'sortable': True},
        ]
        t = ui.table(columns=cols, rows=[], row_key='name', selection='multiple') \
              .classes('w-full').style('max-height:360px; flex-shrink:0;')
        t.props('dense virtual-scroll')
        t.add_slot('body-cell-status', r'''
            <q-td :props="props">
                <span :class="'am-tag am-tag-' + props.row._status">
                    {{ (props.row.status || '—').toUpperCase() }}
                </span>
            </q-td>
        ''')
        refs['table'] = t

        # Status bar
        with ui.element('div').classes('am-status-bar'):
            refs['spinner'] = ui.element('div').classes('am-spinner')
            refs['spinner'].set_visibility(False)
            refs['status_lbl'] = ui.label('Ready').style('color:var(--text-dim);')

        asyncio.ensure_future(reload())
        return reload
