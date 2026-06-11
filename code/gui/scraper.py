"""Scraper page — route demand data collection via CDP or OpenClaw."""

import asyncio, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nicegui import ui

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def build(container):
    refs  = {}
    state = {'running': False, 'cancel': False, 'backend': None}

    def set_status(msg: str, running: bool = False):
        if 'status_lbl' in refs:
            refs['status_lbl'].set_text(msg)
            refs['status_lbl'].style(f'color:{"#f5a623" if running else "var(--text-dim)"};')
        if 'spinner' in refs:
            refs['spinner'].set_visibility(running)

    def update_progress(done: int, total: int):
        pct = (done / total * 100) if total else 0
        refs['prog_fill'].style(f'width:{pct:.1f}%; height:100%; border-radius:3px; background:linear-gradient(90deg,#22d3ee,#22c55e); transition:width 0.3s;')
        refs['prog_label'].set_text(f'{done}/{total} countries ({pct:.0f}%)')
        refs['prog_wrap'].set_visibility(True)

    def detect_backend():
        try:
            sys.path.insert(0, os.path.join(BASE_DIR, 'code'))
            from scraping.backends import CDPBackend, OpenClawBackend
            if CDPBackend.is_available(): return 'cdp'
            if OpenClawBackend.is_available(): return 'openclaw'
        except Exception:
            pass
        return 'cdp'

    async def start_scrape():
        if state['running']: return
        from scraping.constants import COUNTRIES
        from scraping.core import (
            load_checkpoint, save_checkpoint, get_route_count,
            do_audit, extract_data, write_routes, make_url,
        )
        from scraping.backends import CDPBackend, OpenClawBackend

        hub_id       = refs['hub_id'].value.strip()
        hub          = refs['hub'].value.strip().upper()
        backend_name = refs['backend'].value

        if not hub_id: set_status('hub ID required'); return
        if not hub:    set_status('hub IATA required'); return

        try:
            state['backend'] = CDPBackend() if backend_name == 'cdp' else OpenClawBackend()
        except Exception as e:
            set_status(f'backend connect failed: {e}'); return

        do_audit_flag = refs['audit'].value
        skip_audit    = set(c.lower() for c in refs['skip_audit'].value.strip().split() if c)

        state['cancel']  = False
        state['running'] = True
        refs['btn_run'].disable()
        refs['btn_stop'].enable()
        refs['table'].rows[:] = []
        refs['table'].update()
        refs['prog_wrap'].set_visibility(True)

        out_path  = os.path.join(BASE_DIR, 'data', f'{hub.lower()}_demand_raw.txt')
        ckpt_path = os.path.join(BASE_DIR, 'data', f'{hub.lower()}_scrape_progress.txt')

        done_ck   = load_checkpoint(ckpt_path)
        remaining = [c for c in COUNTRIES if c not in done_ck]
        backend   = state['backend']
        loop      = asyncio.get_event_loop()
        total_routes = 0
        total_errors = 0
        row_num      = 0
        total        = len(remaining)

        if not os.path.exists(out_path):
            with open(out_path, 'w') as f:
                f.write('# country|iata|name|distance|category|price|eco|bus|fir|cargo|gross_price\n')

        set_status(f'{len(done_ck)} done, {total} remaining via {backend_name}', running=True)

        try:
            for idx, country in enumerate(remaining):
                if state['cancel']:
                    set_status(f'stopped at {country} ({idx}/{total})')
                    break

                row_num += 1
                update_progress(idx, total)
                set_status(f'[{idx+1}/{total}] {country} — navigating…', running=True)
                await loop.run_in_executor(None, lambda c=country: backend.navigate(make_url(hub_id, c)))
                count = await loop.run_in_executor(None, lambda: get_route_count(backend))

                if count == 0:
                    refs['table'].add_rows([{'n': str(row_num), 'country': country,
                                            'routes': '0', 'audit': '—',
                                            'status': 'empty', 'time': time.strftime('%H:%M:%S')}])
                    refs['table'].update()
                    save_checkpoint(ckpt_path, country, 0, 'empty')
                    continue

                audit_tag = '—'
                if do_audit_flag and country not in skip_audit:
                    set_status(f'[{idx+1}/{total}] {country} — auditing {count} routes…', running=True)
                    selected, status = await loop.run_in_executor(None, lambda: do_audit(backend))
                    if status == 'ok':
                        wait = selected * 3 + 5
                        audit_tag = f'{selected}r ~{wait}s'
                        set_status(f'[{idx+1}/{total}] {country} — waiting {wait}s…', running=True)
                        await loop.run_in_executor(None, lambda w=wait: time.sleep(w))
                    else:
                        audit_tag = f'FAIL:{status}'
                        total_errors += 1
                        refs['table'].add_rows([{'n': str(row_num), 'country': country,
                                               'routes': str(count), 'audit': audit_tag,
                                               'status': 'audit_fail', 'time': time.strftime('%H:%M:%S')}])
                        refs['table'].update()
                        save_checkpoint(ckpt_path, country, count, f'audit_fail:{status}')
                        continue

                set_status(f'[{idx+1}/{total}] {country} — scraping…', running=True)
                routes, status = await loop.run_in_executor(None, lambda: extract_data(backend))

                if routes is None:
                    total_errors += 1
                    refs['table'].add_rows([{'n': str(row_num), 'country': country,
                                           'routes': str(count), 'audit': audit_tag,
                                           'status': f'FAIL: {str(status)[:30]}',
                                           'time': time.strftime('%H:%M:%S')}])
                    refs['table'].update()
                    save_checkpoint(ckpt_path, country, count, 'scrape_fail')
                    continue

                if not routes:
                    refs['table'].add_rows([{'n': str(row_num), 'country': country,
                                           'routes': str(count), 'audit': audit_tag,
                                           'status': 'no data', 'time': time.strftime('%H:%M:%S')}])
                    refs['table'].update()
                    save_checkpoint(ckpt_path, country, count, 'no_data')
                    continue

                await loop.run_in_executor(None, lambda: write_routes(out_path, country, routes))
                scraped = sum(1 for l in routes if len(l.split('|')) >= 6 and l.split('|')[5])
                total_routes += scraped
                refs['table'].add_rows([{'n': str(row_num), 'country': country,
                                       'routes': str(count), 'audit': audit_tag,
                                       'status': f'{scraped} scraped',
                                       'time': time.strftime('%H:%M:%S')}])
                refs['table'].update()
                save_checkpoint(ckpt_path, country, count, 'ok')
                set_status(
                    f'[{idx+1}/{total}] {country} — {scraped} routes ({total_routes} total)',
                    running=True,
                )
                await loop.run_in_executor(None, lambda: time.sleep(0.5))
        finally:
            if backend: backend.close()
            state['running'] = False
            refs['btn_run'].enable()
            refs['btn_stop'].disable()
            update_progress(total, total)

        set_status(f'done — {total_routes} routes scraped, {total_errors} errors | {out_path}')

    def stop_scrape():
        state['cancel'] = True
        set_status('stopping after current country…')

    def reset():
        hub = refs['hub'].value.strip().lower()
        if not hub: return
        ckpt = os.path.join(BASE_DIR, 'data', f'{hub}_scrape_progress.txt')
        out  = os.path.join(BASE_DIR, 'data', f'{hub}_demand_raw.txt')
        removed = []
        for path, label in [(ckpt, 'checkpoint'), (out, 'output')]:
            if os.path.exists(path):
                os.remove(path)
                removed.append(label)
        refs['table'].rows[:] = []
        refs['table'].update()
        refs['prog_wrap'].set_visibility(False)
        set_status(f'reset: removed {", ".join(removed)}' if removed else 'nothing to reset')

    detected = detect_backend()

    with container:
        # Section header with action buttons
        with ui.element('div').classes('am-section-header'):
            with ui.element('div'):
                ui.label('Data Scraper').classes('am-section-title')
                ui.label('Route demand + audit price collection via CDP or OpenClaw').classes('am-section-sub')
            with ui.element('div').classes('am-section-actions'):
                refs['btn_run']  = ui.button('▶  Run',   on_click=start_scrape) \
                    .props('flat no-caps no-ripple dense').classes('am-wf am-wf-success')
                refs['btn_stop'] = ui.button('■  Stop',  on_click=stop_scrape) \
                    .props('flat no-caps no-ripple dense').classes('am-wf am-wf-danger')
                refs['btn_stop'].disable()
                ui.button('↺  Reset', on_click=reset) \
                    .props('flat no-caps no-ripple dense').classes('am-wf')

        # Parameter panel
        with ui.element('div').classes('am-panel').style('margin-bottom:14px; flex-shrink:0;'):
            with ui.element('div').style(
                'display:grid; grid-template-columns:repeat(4,1fr); gap:10px 16px;'
            ):
                def param_field(ref_key, label, default='', mono=True, placeholder=''):
                    with ui.element('div').style('display:flex; flex-direction:column; gap:4px;'):
                        ui.label(label).style(
                            'font-size:10px; color:var(--text-dim); '
                            'font-family:DM Mono,monospace; letter-spacing:0.5px;'
                        )
                        inp = ui.input(value=default, placeholder=placeholder) \
                                .props('dense dark outlined').style(
                            'font-family:DM Mono,monospace; font-size:12px;'
                        )
                        refs[ref_key] = inp

                param_field('hub_id',     'HUB ID (game URL)', placeholder='10087991')
                param_field('hub',        'HUB IATA',          'HKG', placeholder='HKG')

                with ui.element('div').style('display:flex; flex-direction:column; gap:4px;'):
                    ui.label('BACKEND').style(
                        'font-size:10px; color:var(--text-dim); '
                        'font-family:DM Mono,monospace; letter-spacing:0.5px;'
                    )
                    refs['backend'] = ui.select(
                        {'cdp': 'cdp (CDP websocket)', 'openclaw': 'openclaw (CLI)'},
                        value=detected,
                    ).props('dense dark outlined').style('font-size:12px;')

                param_field('skip_audit', 'SKIP AUDIT (space-sep)', placeholder='ng gh ke')

            with ui.element('div').style('margin-top:10px;'):
                refs['audit'] = ui.checkbox('Enable audit (costs $ in-game)', value=False) \
                    .props('dark color=amber')

        # Progress bar (hidden until run)
        prog_wrap = ui.element('div').style('margin-bottom:12px; flex-shrink:0;')
        prog_wrap.set_visibility(False)
        refs['prog_wrap'] = prog_wrap
        with prog_wrap:
            with ui.element('div').style('display:flex; justify-content:space-between; margin-bottom:4px;'):
                ui.label('PROGRESS').style(
                    'font-size:10px; color:var(--text-dim); font-family:DM Mono,monospace;'
                )
                refs['prog_label'] = ui.label('0/0 countries (0%)').style(
                    'font-size:10px; color:#22d3ee; font-family:DM Mono,monospace;'
                )
            with ui.element('div').classes('am-progress-track'):
                refs['prog_fill'] = ui.element('div').style(
                    'width:0%; height:100%; border-radius:3px; '
                    'background:linear-gradient(90deg,#22d3ee,#22c55e); transition:width 0.3s;'
                )

        # Results table
        cols = [
            {'name': 'n',       'label': '#',       'field': 'n',       'align': 'right'},
            {'name': 'country', 'label': 'COUNTRY',  'field': 'country', 'align': 'left'},
            {'name': 'routes',  'label': 'ROUTES',   'field': 'routes',  'align': 'right'},
            {'name': 'audit',   'label': 'AUDIT',    'field': 'audit',   'align': 'center'},
            {'name': 'status',  'label': 'STATUS',   'field': 'status',  'align': 'left'},
            {'name': 'time',    'label': 'TIME',     'field': 'time',    'align': 'right'},
        ]
        t = ui.table(columns=cols, rows=[], row_key='n') \
              .classes('w-full').style('flex:1; min-height:250px;')
        t.props('dense virtual-scroll')
        refs['table'] = t

        # Status bar
        with ui.element('div').classes('am-status-bar'):
            refs['spinner'] = ui.element('div').classes('am-spinner')
            refs['spinner'].set_visibility(False)
            refs['status_lbl'] = ui.label('Configure params and press Run').style(
                'color:var(--text-dim);'
            )
