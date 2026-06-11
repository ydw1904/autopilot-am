"""Hub Management page — route database, stats, and financial overview."""

import os, sys, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nicegui import ui, run
from db import (
    hub_stats, top_routes, list_hubs_with_routes,
    hub_financial_stats, get_owned_routes,
)


def _fmt_money(v):
    if not v:
        return '$0'
    if abs(v) >= 1e9:
        return f'${v/1e9:.1f}B'
    if abs(v) >= 1e6:
        return f'${v/1e6:.1f}M'
    if abs(v) >= 1e3:
        return f'${v/1e3:.0f}K'
    return f'${v:,.0f}'


def build(container):
    refs = {}

    async def reload():
        hubs = refs['sel_hubs'].value
        if not hubs:
            refs['table'].rows[:] = []
            refs['table'].update()
            refs['status'].set_text('No hubs selected')
            return

        cats = refs['sel_cats'].value
        classes = refs['sel_classes'].value
        ownership = refs['sel_owned'].value

        routes = await run.io_bound(top_routes, hubs, 500)

        filtered = []
        for r in routes:
            if cats and str(r['dest_category']) not in cats:
                continue
            if classes:
                has_demand = False
                for c in classes:
                    if r.get(f'{c.lower()}_demand', 0) > 0:
                        has_demand = True; break
                if not has_demand: continue

            if ownership:
                is_owned = bool(r.get('is_owned', 0))
                if 'owned' in ownership and not is_owned: continue
                if 'unowned' in ownership and is_owned: continue

            filtered.append(r)

        rows = []
        for r in filtered:
            rows.append({
                'hub':     r['hub_iata'],
                'iata':    r['dest_iata'],
                'name':    r['dest_name'] or '',
                'dist':    f'{r["distance_km"]:,} km',
                'cat':     str(r['dest_category'] or ''),
                'eco':     f'{r["eco_demand"]:,}'   if r['eco_demand']   else '—',
                'bus':     f'{r["bus_demand"]:,}'   if r['bus_demand']   else '—',
                'fir':     f'{r["fir_demand"]:,}'   if r['fir_demand']   else '—',
                'cargo':   f'{r["cargo_demand"]:,}' if r['cargo_demand'] else '—',
                'price':   f'${r["gross_price"]:,.0f}' if r['gross_price'] else '—',
                'owned':   '✓' if r.get('is_owned') else '—',
                'line_id': str(r['line_id']) if r.get('line_id') else '—',
                '_dist':   r['distance_km'],
                '_eco':    r['eco_demand'] or 0,
            })

        refs['table'].rows[:] = rows
        refs['table'].update()

        refs['stat_routes'].set_text(f'{len(rows):,}')
        refs['status'].set_text(f'{", ".join(hubs)}: {len(rows)} routes shown')

    async def reload_stats():
        hubs = refs['sel_hubs'].value
        if not hubs:
            for k in ('s_wrev', 's_drev', 's_inv', 's_rinv', 's_circ',
                       's_waves', 's_owned', 's_lineid', 's_rval',
                       's_fleet', 's_idle', 's_routes'):
                if k in refs:
                    refs[k].set_text('—')
            return

        all_stats = {}
        for h in hubs:
            s = await run.io_bound(hub_financial_stats, h)
            for k, v in s.items():
                all_stats[k] = all_stats.get(k, 0) + (v or 0)

        refs['s_wrev'].set_text(_fmt_money(all_stats.get('weekly_rev', 0)))
        refs['s_drev'].set_text(_fmt_money(all_stats.get('daily_rev', 0)))
        refs['s_inv'].set_text(_fmt_money(all_stats.get('aircraft_invested', 0)))
        refs['s_rinv'].set_text(_fmt_money(all_stats.get('route_invested', 0)))
        refs['s_circ'].set_text(str(all_stats.get('circuits', 0)))
        refs['s_waves'].set_text(str(all_stats.get('total_waves', 0)))
        refs['s_owned'].set_text(str(all_stats.get('owned_routes', 0)))
        refs['s_lineid'].set_text(str(all_stats.get('with_line_id', 0)))
        refs['s_rval'].set_text(_fmt_money(all_stats.get('route_value', 0)))
        refs['s_fleet'].set_text(str(all_stats.get('fleet_total', 0)))
        refs['s_idle'].set_text(str(all_stats.get('fleet_idle', 0)))
        refs['s_routes'].set_text(f'{all_stats.get("total_routes", 0):,}')

        total_inv = all_stats.get('total_invested', 0)
        wrev = all_stats.get('weekly_rev', 0)
        if wrev > 0:
            payback_days = total_inv / wrev * 7
            refs['s_payback'].set_text(f'{payback_days:.0f} days')
        else:
            refs['s_payback'].set_text('—')

    async def full_reload():
        await reload()
        await reload_stats()

    with container:
        with ui.element('div').classes('am-section-header'):
            with ui.element('div'):
                ui.label('Hub Management').classes('am-section-title')
                ui.label('Routes, fleet, and financial overview').classes('am-section-sub')
            with ui.element('div').classes('am-section-actions'):
                ui.button('Clear Filters', on_click=lambda: _clear_filters()) \
                    .props('flat dense').classes('am-wf-danger')

        with ui.element('div').classes('am-panel').style('margin-bottom:12px; display:flex; gap:16px; align-items:flex-end; flex-wrap:wrap;'):
            with ui.element('div').style('display:flex; flex-direction:column; gap:4px;'):
                ui.label('HUBS').classes('am-metric-label')
                hubs = list_hubs_with_routes()
                hub_opts = {h['hub_iata']: h['hub_iata'] for h in hubs}
                refs['sel_hubs'] = ui.select(hub_opts, multiple=True, label='Select Hubs',
                                              on_change=full_reload) \
                    .props('dense dark outlined use-chips').style('width:200px;')
                refs['sel_hubs'].value = ['HKG']

            with ui.element('div').style('display:flex; flex-direction:column; gap:4px;'):
                ui.label('CATEGORY').classes('am-metric-label')
                cat_opts = {str(i): str(i) for i in range(1, 11)}
                refs['sel_cats'] = ui.select(cat_opts, multiple=True, label='All Categories',
                                             on_change=reload) \
                    .props('dense dark outlined use-chips').style('width:200px;')

            with ui.element('div').style('display:flex; flex-direction:column; gap:4px;'):
                ui.label('CLASS (DEMAND > 0)').classes('am-metric-label')
                class_opts = {'eco': 'Economy', 'bus': 'Business', 'fir': 'First', 'cargo': 'Cargo'}
                refs['sel_classes'] = ui.select(class_opts, multiple=True, label='All Classes',
                                                on_change=reload) \
                    .props('dense dark outlined use-chips').style('width:200px;')

            with ui.element('div').style('display:flex; flex-direction:column; gap:4px;'):
                ui.label('OWNERSHIP').classes('am-metric-label')
                own_opts = {'owned': 'Owned', 'unowned': 'Not Owned'}
                refs['sel_owned'] = ui.select(own_opts, multiple=True, label='All',
                                              on_change=reload) \
                    .props('dense dark outlined use-chips').style('width:160px;')

        def _clear_filters():
            refs['sel_hubs'].value = ['HKG']
            refs['sel_cats'].value = []
            refs['sel_classes'].value = []
            refs['sel_owned'].value = []
            asyncio.ensure_future(full_reload())

        with ui.element('div').style(
            'display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); '
            'gap:8px; margin-bottom:16px;'
        ):
            def stat_card(ref_key, label, accent='var(--text-hi)'):
                with ui.element('div').classes('am-stat'):
                    ui.label(label).classes('am-stat-label')
                    v = ui.label('—').classes('am-stat-value').style(f'color:{accent};')
                    refs[ref_key] = v

            with ui.element('div').style(
                'display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); '
                'gap:8px; width:100%;'
            ):
                ui.label('FINANCIAL').style(
                    'font-size:9px; font-weight:600; letter-spacing:1.5px; '
                    'color:var(--text-dim); grid-column:1/-1; padding-top:4px;'
                )
                stat_card('s_wrev',    'WEEKLY REVENUE',   'var(--green)')
                stat_card('s_drev',    'DAILY REVENUE',    'var(--green)')
                stat_card('s_inv',     'AIRCRAFT INVESTED', 'var(--cyan)')
                stat_card('s_rinv',    'ROUTE INVESTED',    'var(--cyan)')
                stat_card('s_payback', 'PAYBACK PERIOD',   'var(--accent)')

                ui.label('OPERATIONS').style(
                    'font-size:9px; font-weight:600; letter-spacing:1.5px; '
                    'color:var(--text-dim); grid-column:1/-1; padding-top:8px;'
                )
                stat_card('s_circ',  'CIRCUITS',         'var(--cyan)')
                stat_card('s_waves', 'TOTAL WAVES',      'var(--cyan)')
                stat_card('s_fleet', 'FLEET SIZE',       'var(--text-hi)')
                stat_card('s_idle',  'IDLE AIRCRAFT',    'var(--red)')

                ui.label('ROUTES').style(
                    'font-size:9px; font-weight:600; letter-spacing:1.5px; '
                    'color:var(--text-dim); grid-column:1/-1; padding-top:8px;'
                )
                stat_card('stat_routes', 'TOTAL ROUTES',   'var(--text-hi)')
                stat_card('s_routes',    'DB ROUTES',       'var(--text-dim)')
                stat_card('s_owned',     'OWNED ROUTES',    'var(--green)')
                stat_card('s_lineid',    'WITH LINE ID',    'var(--accent)')
                stat_card('s_rval',      'ROUTE VALUE',     'var(--cyan)')

        cols = [
            {'name': 'hub',     'label': 'HUB',         'field': 'hub',     'align': 'left',   'sortable': True},
            {'name': 'iata',    'label': 'IATA',        'field': 'iata',    'align': 'left',   'sortable': True},
            {'name': 'name',    'label': 'DESTINATION',  'field': 'name',    'align': 'left',   'sortable': True},
            {'name': 'dist',    'label': 'DIST',         'field': 'dist',    'align': 'right',  'sortable': True},
            {'name': 'cat',     'label': 'CAT',          'field': 'cat',     'align': 'center', 'sortable': True},
            {'name': 'eco',     'label': 'ECO',          'field': 'eco',     'align': 'right',  'sortable': True},
            {'name': 'bus',     'label': 'BUS',          'field': 'bus',     'align': 'right',  'sortable': True},
            {'name': 'fir',     'label': 'FIR',          'field': 'fir',     'align': 'right',  'sortable': True},
            {'name': 'cargo',   'label': 'CARGO',        'field': 'cargo',   'align': 'right',  'sortable': True},
            {'name': 'price',   'label': 'PRICE',        'field': 'price',   'align': 'right',  'sortable': True},
            {'name': 'owned',   'label': 'OWNED',        'field': 'owned',   'align': 'center', 'sortable': True},
            {'name': 'line_id', 'label': 'LINE ID',      'field': 'line_id', 'align': 'right',  'sortable': True},
        ]
        t = ui.table(columns=cols, rows=[], row_key='iata') \
              .classes('w-full').style('flex:1; overflow:hidden;')
        t.props('dense virtual-scroll')
        refs['table'] = t

        with ui.element('div').classes('am-status-bar'):
            refs['status'] = ui.label('').style('color:var(--text-dim);')

        asyncio.ensure_future(full_reload())
        return full_reload
