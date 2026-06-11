"""Warehouse page — manage unused aircraft across the entire fleet."""

import asyncio, os, sys, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nicegui import ui, run
from gui.workers import run_subprocess
from db import get_stored_aircraft, upsert_fleet, get_db, list_hubs_with_routes, list_warehouse_models
from gui.logbuf import add as log_add
from cdp import CDP, get_am_tab
from circuit_scheduler import get_aircraft_at_hub

def build(container):
    state = {'busy': False, 'rows': []}
    refs = {}

    def set_status(msg: str, running: bool = False):
        log_add('warehouse', msg)
        if 'status_lbl' in refs:
            refs['status_lbl'].set_text(msg)
            refs['status_lbl'].style(f'color:{"#f5a623" if running else "var(--text-dim)"};')
        if 'spinner' in refs:
            refs['spinner'].set_visibility(running)

    async def reload():
        hubs   = refs['sel_hubs'].value   if 'sel_hubs'   in refs else []
        mquery = refs['sel_models'].value if 'sel_models' in refs else ''
        name_q = refs['inp_name'].value   if 'inp_name'   in refs else ''

        rows = await run.io_bound(get_stored_aircraft, min_util=0, max_util=0,
                                  hubs=hubs, model_query=mquery, name_query=name_q)
        for r in rows:
            raw = r.get('name') or ''
            # Stored as "<model> / <assigned name>"; strip the model prefix.
            if ' / ' in raw:
                r['display_name'] = raw.split(' / ', 1)[1].strip()
            else:
                r['display_name'] = raw
        state['rows'] = rows
        refs['table'].rows[:] = rows
        refs['table'].update()
        if not state['busy']:
            set_status(f'{len(rows)} aircraft in warehouse')

    async def sync_fleet():
        if state['busy']: return
        state['busy'] = True
        set_status('syncing fleet (scraping all hubs)…', running=True)
        
        tab = get_am_tab()
        if not tab:
            set_status('ERROR: AM tab not found. Open Chrome with --remote-debugging-port=9222'); state['busy'] = False; return
            
        cdp = CDP(tab["webSocketDebuggerUrl"])
        try:
            await run.io_bound(cdp.connect)
            
            # 1. Get list of all hubs from player_hubs table
            db = get_db()
            hubs = [r[0] for r in db.execute("SELECT hub_iata FROM player_hubs").fetchall()]
            if not hubs:
                set_status('No hubs in DB. Run Hub scraper or add hubs first.'); state['busy'] = False; return

            all_fleet = []
            await run.io_bound(cdp.navigate, "https://www.airlines-manager.com/network/planning")
            await asyncio.sleep(4)

            for i, hub in enumerate(hubs, 1):
                set_status(f'[{i}/{len(hubs)}] scraping hub {hub}…', running=True)
                
                # Select hub using JS in CDP
                success = await run.io_bound(cdp.eval, f"""(() => {{
                    const btns = document.querySelectorAll('.planninghubBtn');
                    for (const btn of btns) {{
                        const txt = btn.textContent.trim();
                        if (txt.startsWith('{hub} /') || txt.startsWith('{hub}/')) {{
                            btn.click();
                            return true;
                        }}
                    }}
                    return false;
                }})()""")
                
                if not success:
                    log_add('warehouse', f'hub {hub} not found on planning page')
                    continue
                
                await asyncio.sleep(3) # Wait for AJAX
                
                # Scrape aircraft
                ac_list = await run.io_bound(get_aircraft_at_hub, cdp)
                for ac in ac_list:
                    ac['hub'] = hub
                    all_fleet.append(ac)
            
            await run.io_bound(upsert_fleet, all_fleet)
            set_status(f'sync complete: {len(all_fleet)} aircraft processed')
            reload()
        except Exception as e:
            set_status(f'sync failed: {e}')
        finally:
            cdp.close()
            state['busy'] = False

    async def bulk_rename(new_prefix: str = None):
        sel = refs['table'].selected
        if not sel: set_status('select aircraft first'); return
        
        # If no prefix provided, show dialog
        if new_prefix is None:
            with ui.dialog() as dialog, ui.card():
                ui.label('Bulk Rename').classes('text-lg font-bold')
                inp = ui.input('New Prefix (e.g. SELL or STORAGE)').classes('w-full')
                with ui.row():
                    ui.button('Cancel', on_click=dialog.close).props('flat')
                    ui.button('Rename', on_click=lambda: dialog.submit(inp.value))
            
            prefix = await dialog
            if not prefix: return
        else:
            prefix = new_prefix

        state['busy'] = True
        set_status(f'renaming {len(sel)} aircraft to {prefix}…', running=True)
        
        # Use aircraft_numberer logic or a simplified version
        from aircraft_numberer import get_form_token, rename
        tab = get_am_tab()
        cdp = CDP(tab["webSocketDebuggerUrl"])
        try:
            await run.io_bound(cdp.connect)
            ok = fail = 0
            for i, ac in enumerate(sel, 1):
                aid = ac['aircraft_id']
                name = ac['name']
                new_name = f"{prefix}-{i:03d}" if len(sel) > 1 else prefix
                
                set_status(f'[{i}/{len(sel)}] {name} -> {new_name}', running=True)
                tok = await run.io_bound(get_form_token, cdp, aid)
                if tok:
                    status = await run.io_bound(rename, cdp, aid, new_name, tok)
                    if status in (200, 302): ok += 1
                    else: fail += 1
                else:
                    fail += 1
                await asyncio.sleep(0.2)
            
            set_status(f'rename complete: {ok} ok, {fail} failed')
            # Update local DB if successful? Or just wait for next sync.
            # For simplicity, we just reload from DB (which won't have new names yet).
            # Ideal: update DB locally too.
        finally:
            cdp.close()
            state['busy'] = False
            reload()

    async def assign_to_circuit():
        sel = refs['table'].selected
        if not sel: set_status('select aircraft first'); return
        
        with ui.dialog() as dialog, ui.card():
            ui.label('Assign to Circuit').classes('text-lg font-bold')
            ui.label(f'Will rename {len(sel)} aircraft to circuit format.')
            inp = ui.input('Circuit Name (e.g. HKG-C001)').classes('w-full')
            with ui.row():
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button('Assign', on_click=lambda: dialog.submit(inp.value.upper()))
        
        circuit = await dialog
        if not circuit: return
        
        # We need to find the next available number for this circuit.
        # This requires scraping the /aircraft list like numberer does.
        # For simplicity, we'll just run aircraft_numberer.py via subprocess?
        # No, that script expects bare aircraft.
        # Let's just do it here.
        await bulk_rename(circuit)

    with container:
        with ui.element('div').classes('am-section-header'):
            with ui.element('div'):
                ui.label('Aircraft Warehouse').classes('am-section-title')
                ui.label('Aircraft with 0% utilization across all hubs').classes('am-section-sub')
            with ui.element('div').classes('am-section-actions'):
                ui.button('Clear Filters', on_click=lambda: _clear_filters()) \
                    .props('flat dense').classes('am-wf-danger')
                ui.button('Sync Fleet', on_click=sync_fleet) \
                    .props('flat no-caps no-ripple dense').classes('am-wf am-wf-cyan')
                ui.button('Rename Selected', on_click=lambda: bulk_rename()) \
                    .props('flat no-caps no-ripple dense').classes('am-wf')
                ui.button('Assign to Circuit', on_click=assign_to_circuit) \
                    .props('flat no-caps no-ripple dense').classes('am-wf am-wf-success')

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

            with ui.element('div').style('display:flex; flex-direction:column; gap:4px; flex:1;'):
                ui.label('NAME').classes('am-metric-label')
                refs['inp_name'] = ui.input(placeholder='substring match (case-insensitive)',
                                            on_change=lambda: asyncio.ensure_future(reload())) \
                    .props('dense dark outlined clearable debounce=250').style('min-width:240px;')

        def _clear_filters():
            refs['sel_hubs'].value = []
            refs['sel_models'].value = ''
            refs['inp_name'].value = ''
            asyncio.ensure_future(reload())

        cols = [
            {'name': 'model', 'label': 'MODEL', 'field': 'model', 'align': 'left', 'sortable': True},
            {'name': 'iata',  'label': 'IATA',  'field': 'icao_code', 'align': 'left', 'sortable': True},
            {'name': 'name', 'label': 'NAME', 'field': 'display_name', 'align': 'left', 'sortable': True},
            {'name': 'hub', 'label': 'HUB', 'field': 'hub_iata', 'align': 'left', 'sortable': True},
            {'name': 'updated', 'label': 'LAST SYNC', 'field': 'updated_at', 'align': 'left', 'sortable': True},
        ]
        t = ui.table(columns=cols, rows=[], row_key='aircraft_id', selection='multiple') \
              .classes('w-full').style('max-height:400px; flex:1;')
        t.props('dense virtual-scroll')
        refs['table'] = t

        with ui.element('div').classes('am-status-bar'):
            refs['spinner'] = ui.element('div').classes('am-spinner')
            refs['spinner'].set_visibility(False)
            refs['status_lbl'] = ui.label('Ready').style('color:var(--text-dim);')

        asyncio.ensure_future(reload())
        return reload
