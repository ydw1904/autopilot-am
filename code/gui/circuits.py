"""Circuits detail page — planner output, 2-column layout."""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nicegui import ui

_state = {'circuits': [], 'selected': 0}


def _route_rows(c: dict) -> tuple[list, list]:
    has_bd = bool(c.get('breakdown'))
    cfg    = c.get('cfg') or {}
    if has_bd:
        cols = [
            {'name': 'iata',  'label': 'IATA',    'field': 'iata',  'align': 'left'},
            {'name': 'name',  'label': 'DEST',     'field': 'name',  'align': 'left'},
            {'name': 'dist',  'label': 'DIST',     'field': 'dist',  'align': 'right'},
            {'name': 'ft',    'label': 'FT',       'field': 'ft',    'align': 'right'},
            {'name': 'eco',   'label': 'ECO $',    'field': 'eco',   'align': 'right'},
            {'name': 'bus',   'label': 'BUS $',    'field': 'bus',   'align': 'right'},
            {'name': 'fir',   'label': 'FIR $',    'field': 'fir',   'align': 'right'},
            {'name': 'cargo', 'label': 'CARGO $',  'field': 'cargo', 'align': 'right'},
            {'name': 'daily', 'label': 'DAILY $',  'field': 'daily', 'align': 'right'},
        ]
        bd_map = {b['iata']: b for b in c['breakdown']}
        rows = []
        for r in sorted(c['routes'], key=lambda x: -x['dist']):
            bd   = bd_map.get(r['iata'])
            cls  = {x['name']: x for x in (bd['classes'] if bd else [])}
            def _p(name):
                if cfg.get(name, 0) <= 0 or name not in cls: return '—'
                return f'${cls[name]["price"]:,.0f}'
            rows.append({
                'iata':  r['iata'], 'name': r.get('name', ''),
                'dist':  f'{r["dist"]:,}km', 'ft': f'{r["ft"]:.2f}h',
                'eco':   _p('eco'),  'bus': _p('bus'),
                'fir':   _p('fir'),  'cargo': _p('cargo'),
                'daily': f'${bd["rev"]:,.0f}' if bd else '—',
            })
    else:
        cols = [
            {'name': 'iata',  'label': 'IATA',      'field': 'iata',  'align': 'left'},
            {'name': 'name',  'label': 'DEST',       'field': 'name',  'align': 'left'},
            {'name': 'dist',  'label': 'DIST',       'field': 'dist',  'align': 'right'},
            {'name': 'ft',    'label': 'FT',         'field': 'ft',    'align': 'right'},
            {'name': 'eco',   'label': 'ECO DEM',    'field': 'eco',   'align': 'right'},
            {'name': 'bus',   'label': 'BUS DEM',    'field': 'bus',   'align': 'right'},
            {'name': 'fir',   'label': 'FIR DEM',    'field': 'fir',   'align': 'right'},
            {'name': 'cargo', 'label': 'CARGO DEM',  'field': 'cargo', 'align': 'right'},
        ]
        rows = [
            {
                'iata':  r['iata'], 'name': r.get('name', ''),
                'dist':  f'{r["dist"]:,}km', 'ft': f'{r["ft"]:.2f}h',
                'eco':   f'{r["eco_d"]:,}', 'bus': f'{r["bus_d"]:,}',
                'fir':   f'{r["fir_d"]:,}', 'cargo': f'{r["cargo_d"]:,}',
            }
            for r in sorted(c['routes'], key=lambda x: -x['dist'])
        ]
    return cols, rows


def build(container, get_circuits):
    refs = {}

    def refresh():
        circuits = get_circuits()
        _state['circuits'] = circuits
        # Rebuild tab buttons
        if 'tabs_row' in refs:
            refs['tabs_row'].clear()
            with refs['tabs_row']:
                if not circuits:
                    ui.label('Run the planner to generate circuits').style(
                        'color:var(--text-dim2); font-size:12px; font-family:DM Mono,monospace;'
                    )
                else:
                    for i, c in enumerate(circuits):
                        label = c.get('name') or f"C{c.get('num', i+1):03d}"
                        ui.button(label, on_click=lambda _, idx=i: select(idx)) \
                          .props('flat no-caps no-ripple dense') \
                          .classes('am-pill' + (' active' if i == _state['selected'] else ''))
        if circuits:
            select(_state['selected'] if _state['selected'] < len(circuits) else 0)

    def select(idx: int):
        _state['selected'] = idx
        circuits = _state['circuits']
        if not circuits or idx >= len(circuits):
            return
        c = circuits[idx]

        # Update tab active state
        if 'tabs_row' in refs:
            for i, btn in enumerate(refs['tabs_row']):
                if hasattr(btn, 'classes'):
                    if i == idx: btn.classes(add='active')
                    else:        btn.classes(remove='active')

        cols, rows = _route_rows(c)
        if 'table' in refs:
            refs['table'].columns[:] = cols
            refs['table'].rows[:] = rows
            refs['table'].update()

        # Update stats panel
        cfg = c.get('cfg')
        hub = c.get('hub', '—')
        ac  = c.get('ac', {})
        cid = c.get('name') or f"{hub}-C{c['num']:03d}"

        refs['stats_name'].set_text(cid)
        refs['stats_ac'].set_text(f"{ac.get('alias','?')} ({ac.get('model','?')})")
        refs['stats_hub'].set_text(f"Hub: {hub}  ·  {len(c['routes'])} routes")

        if cfg:
            planes  = c['waves'] * 7
            inv     = planes * ac.get('price', 0)
            pb      = inv / c['daily_rev'] if c.get('daily_rev') else 0
            refs['stats_daily'].set_text(f'${c["daily_rev"]:,.0f}')
            refs['stats_weekly'].set_text(f'${c["weekly_rev"]:,.0f}')
            refs['stats_planes'].set_text(f'{planes} ({c["waves"]} waves)')
            refs['stats_invest'].set_text(f'${inv:,.0f}')
            refs['stats_pb'].set_text(f'{pb:.0f}d')
            refs['stats_cfg'].set_text(
                f'eco={cfg["eco"]} bus={cfg["bus"]} fir={cfg["fir"]} cargo={cfg["cargo"]}'
            )
        else:
            refs['stats_daily'].set_text(f'${c.get("p1_score",0):,.0f} (est)')
            refs['stats_weekly'].set_text('—')
            refs['stats_planes'].set_text('—')
            refs['stats_invest'].set_text('—')
            refs['stats_pb'].set_text('—')
            refs['stats_cfg'].set_text('phase 1 only')

    with container:
        # Section header
        with ui.element('div').classes('am-section-header'):
            with ui.element('div'):
                ui.label('Circuits').classes('am-section-title')
                ui.label('Planner results — per-circuit detail').classes('am-section-sub')

        # Circuit tabs (pills)
        tabs_row = ui.element('div').style(
            'display:flex; gap:6px; flex-wrap:wrap; margin-bottom:12px; flex-shrink:0;'
        )
        refs['tabs_row'] = tabs_row
        with tabs_row:
            ui.label('Run the planner to generate circuits').style(
                'color:var(--text-dim2); font-size:12px; font-family:DM Mono,monospace;'
            )

        # 2-column layout: table left, stats right
        with ui.element('div').style(
            'display:flex; gap:12px; flex:1; min-height:250px;'
        ):
            # Left: route table
            t = ui.table(columns=[], rows=[], row_key='iata') \
                  .classes('w-full').style('flex:1; min-height:250px;')
            t.props('dense virtual-scroll')
            refs['table'] = t

            # Right: stats panel
            with ui.element('div').classes('am-panel').style(
                'width:220px; flex-shrink:0; display:flex; flex-direction:column; gap:12px; overflow-y:auto;'
            ):
                refs['stats_name'] = ui.label('—').style(
                    'color:#22d3ee; font-weight:700; font-family:DM Mono,monospace; font-size:13px;'
                )
                refs['stats_ac'] = ui.label('—').style(
                    'font-size:11px; color:var(--text-dim); font-family:DM Mono,monospace;'
                )
                refs['stats_hub'] = ui.label('—').style(
                    'font-size:10px; color:var(--text-dim2); font-family:DM Mono,monospace;'
                )

                ui.element('div').style('border-top:1px solid var(--border); margin:4px 0;')

                def stats_metric(ref_key, label, color='var(--text-hi)'):
                    with ui.element('div'):
                        ui.label(label).classes('am-metric-label')
                        refs[ref_key] = ui.label('—').classes('am-metric-value').style(f'color:{color};')

                stats_metric('stats_daily',  'DAILY REV',  '#22c55e')
                stats_metric('stats_weekly', 'WEEKLY REV', '#22c55e')
                stats_metric('stats_planes', 'AIRCRAFT')
                stats_metric('stats_invest', 'INVESTMENT', '#f5a623')
                stats_metric('stats_pb',     'PAYBACK',    '#f5a623')
                stats_metric('stats_cfg',    'CONFIG')

        # Status bar
        with ui.element('div').classes('am-status-bar'):
            ui.label('Select a circuit tab to view details').style('color:var(--text-dim);')

    return refresh
