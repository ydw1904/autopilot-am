"""Log page — activity history across all modules."""

import os, sys, subprocess
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nicegui import ui
from gui.logbuf import get_all, clear as clear_log

_LEVEL_COLOR = {
    'info':  '#94a3b8',
    'ok':    '#22c55e',
    'warn':  '#f5a623',
    'error': '#ef4444',
}
_SRC_COLOR = {
    'library': '#22d3ee',
    'planner': '#a855f7',
    'scraper': '#f59e0b',
    'chrome':  '#22c55e',
    'app':     '#475569',
    'numberer':'#22d3ee',
    'scheduler':'#22d3ee',
}


def _detect_level(msg: str) -> str:
    low = msg.lower()
    if any(w in low for w in ('error', 'failed', 'fail', 'refused')): return 'error'
    if any(w in low for w in ('warn', 'timeout', 'skip')): return 'warn'
    if any(w in low for w in ('done', 'success', 'ready', 'launched', 'purchased', 'scheduled', 'numbered', 'renamed', 'saved')): return 'ok'
    return 'info'


def build(container):
    refs  = {}
    state = {'filter': 'all'}

    def refresh():
        raw     = get_all()
        entries = []
        for ts, source, msg in raw:
            lvl = _detect_level(msg)
            entries.append({'t': ts, 'src': source, 'lvl': lvl, 'msg': msg})

        filt    = state['filter']
        visible = entries if filt == 'all' else [e for e in entries if e['lvl'] == filt]

        if 'log_container' in refs:
            refs['log_container'].clear()
            with refs['log_container']:
                for e in reversed(visible):
                    src_color = _SRC_COLOR.get(e['src'], '#475569')
                    lvl_color = _LEVEL_COLOR.get(e['lvl'], '#94a3b8')
                    with ui.element('div').classes('am-log-entry'):
                        ui.label(e['t']).classes('am-log-t')
                        ui.label(e['src']).classes('am-log-src').style(
                            f'background:{src_color}18; color:{src_color};'
                        )
                        ui.label(e['lvl'].upper()).classes('am-log-lvl').style(
                            f'color:{lvl_color};'
                        )
                        ui.label(e['msg']).classes('am-log-msg').style(
                            f'color:{lvl_color};'
                        )

        if 'status_lbl' in refs:
            refs['status_lbl'].set_text(f'{len(visible)} entries')

    def set_filter(key: str):
        state['filter'] = key
        for k, pill in refs.get('pills', {}).items():
            if k == key: pill.classes(add='active')
            else:        pill.classes(remove='active')
        refresh()

    def copy_all():
        raw = get_all()
        if not raw: refs['status_lbl'].set_text('nothing to copy'); return
        text = '\n'.join(f'[{ts}] {src}  {msg}' for ts, src, msg in raw)
        try:
            subprocess.run(['pbcopy'], input=text, text=True, check=True)
            refs['status_lbl'].set_text(f'copied {len(raw)} line(s)')
        except Exception as e:
            refs['status_lbl'].set_text(f'copy failed: {e}')

    def do_clear():
        clear_log()
        refresh()
        refs['status_lbl'].set_text('cleared')

    with container:
        # Section header with filter pills
        with ui.element('div').classes('am-section-header'):
            with ui.element('div'):
                ui.label('Activity Log').classes('am-section-title')
                ui.label('Operation history across all modules').classes('am-section-sub')
            with ui.element('div').classes('am-section-actions'):
                pills = {}
                for key in ['all', 'info', 'ok', 'warn', 'error']:
                    pill = ui.button(key.upper(), on_click=lambda _k=key: set_filter(_k)) \
                              .props('flat no-caps no-ripple dense') \
                              .classes('am-pill' + (' active' if key == 'all' else ''))
                    pills[key] = pill
                refs['pills'] = pills
                ui.element('div').style('width:1px; background:var(--border); margin:0 4px;')
                ui.button('Copy', on_click=copy_all) \
                  .props('flat no-caps no-ripple dense').classes('am-wf')
                ui.button('Clear', on_click=do_clear) \
                  .props('flat no-caps no-ripple dense').classes('am-wf am-wf-danger')

        # Log entries container
        with ui.element('div').classes('am-log-wrap').style(
            'flex:1; min-height:250px; overflow-y:auto;'
        ):
            log_container = ui.element('div')
            refs['log_container'] = log_container

        # Status bar
        with ui.element('div').classes('am-status-bar'):
            refs['status_lbl'] = ui.label('').style('color:var(--text-dim);')

        # Auto-refresh every 2s
        ui.timer(2.0, refresh)
        refresh()
