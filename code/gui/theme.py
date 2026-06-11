"""Design tokens and CSS — v2.0 sidebar layout."""

# Legacy tokens (still imported by some modules)
BG      = '#0c1220'
BG2     = '#0a1628'
PANEL   = '#111b2e'
PANEL2  = '#0f1f36'
BORDER  = '#1e3a5f'
BORDER2 = '#2d5a8e'
TEXT    = '#94a3b8'
TEXT_HI = '#e2e8f0'
DIM     = '#475569'
CYAN    = '#22d3ee'
AMBER   = '#f5a623'
GREEN   = '#22c55e'
RED     = '#ef4444'

STATUS_COLORS = {
    'planned':   AMBER,
    'bought':    CYAN,
    'completed': GREEN,
}

CSS = r"""
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=DM+Mono:wght@400;500&display=swap');

/* ── Design tokens ── */
:root {
    --bg:       #0c1220;
    --bg2:      #0a1628;
    --panel:    #111b2e;
    --panel2:   #0f1f36;
    --border:   #1e3a5f;
    --border2:  #2d5a8e;
    --text:     #94a3b8;
    --text-hi:  #e2e8f0;
    --text-dim: #475569;
    --text-dim2:#334155;
    --accent:   #f5a623;
    --cyan:     #22d3ee;
    --green:    #22c55e;
    --red:      #ef4444;
    --purple:   #a855f7;
}

[data-theme="light"] {
    --bg: #f1f5f9; --bg2: #e2e8f0; --panel: #ffffff; --panel2: #f8fafc;
    --border: #cbd5e1; --border2: #94a3b8;
    --text: #334155; --text-hi: #0f172a; --text-dim: #64748b; --text-dim2: #94a3b8;
    --accent: #d97706; --cyan: #0891b2; --green: #16a34a; --red: #dc2626;
}

/* ── Base reset ── */
*, *::before, *::after { box-sizing: border-box; }
body, html {
    background: var(--bg) !important;
    color: var(--text) !important;
    font-family: 'DM Sans', system-ui, sans-serif !important;
    height: 100vh; overflow: hidden; margin: 0;
}
.nicegui-content {
    background: var(--bg) !important;
    padding: 0 !important;
    min-height: unset !important;
    height: 100%;
    display: flex; flex-direction: column;
}

/* ── Quasar layout ── */
.q-drawer { background: var(--bg2) !important; border-right: 1px solid var(--border) !important; }
.q-drawer__content { overflow: hidden !important; display: flex; flex-direction: column; height: 100vh; }
.q-layout__shadow { display: none !important; }
/* Do NOT override padding — Quasar uses padding-left here to offset the permanent drawer */
.q-page-container { overflow-y: auto !important; }
.q-page { min-height: unset !important; height: 100% !important; overflow-y: auto !important; }

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: var(--bg2); }
::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--cyan); }

/* ── QTable ── */
.q-table__container {
    background: transparent !important;
    border: 1px solid var(--border) !important;
    border-radius: 8px !important;
    overflow: hidden !important;
}
.q-table { background: transparent !important; font-family: 'DM Mono', monospace !important; }
.q-table thead tr { background: var(--bg2) !important; }
.q-table thead th {
    color: var(--cyan) !important; font-size: 10px !important;
    font-weight: 600 !important; letter-spacing: 0.6px !important;
    border-bottom: 1px solid var(--border) !important;
    padding: 8px 12px !important; white-space: nowrap !important;
    position: sticky; top: 0; z-index: 1;
}
.q-table tbody td {
    color: var(--text); font-size: 11px;
    padding: 7px 12px !important;
    border-bottom: 1px solid var(--panel2) !important;
    white-space: nowrap !important;
}
.q-table tbody tr:hover td { background: rgba(255,255,255,0.03) !important; }
.q-table tbody tr.selected td { background: rgba(245,166,35,0.08) !important; border-left: 2px solid var(--accent) !important; }
.q-table tbody tr:nth-child(even) td { background: rgba(255,255,255,0.015); }
.q-table__bottom { background: var(--bg2) !important; border-top: 1px solid var(--border) !important; color: var(--text-dim) !important; }

/* ── QBtn ── */
.q-btn { text-transform: none !important; font-family: 'DM Sans', sans-serif !important; letter-spacing: 0.2px !important; }

/* ── QField / inputs ── */
.q-field--dark .q-field__control { background: var(--panel2) !important; border: 1px solid var(--border); border-radius: 6px; }
.q-field--focused .q-field__control { border-color: var(--accent) !important; }
.q-field__label { color: var(--text-dim) !important; font-size: 10px !important; font-family: 'DM Mono', monospace !important; letter-spacing: 0.5px !important; }
.q-field__native, .q-field__input { color: var(--text-hi) !important; font-size: 12px !important; font-family: 'DM Mono', monospace !important; }

/* ── QMenu ── */
.q-menu { background: var(--panel2) !important; border: 1px solid var(--border) !important; }
.q-item { color: var(--text) !important; font-size: 12px !important; }
.q-item:hover { background: rgba(255,255,255,0.04) !important; }
.q-separator { background: var(--border) !important; }

/* ── QDialog ── */
.q-dialog__inner .q-card { background: var(--panel) !important; border: 1px solid var(--border); }

/* ── QCheckbox ── */
.q-checkbox__bg { border-color: var(--border2) !important; }
.q-checkbox__inner--truthy .q-checkbox__bg { background: var(--accent) !important; border-color: var(--accent) !important; }

/* ── Animations ── */
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes pulseDot { 0%,100% { opacity:1; box-shadow:0 0 4px currentColor; } 50% { opacity:0.6; box-shadow:0 0 10px currentColor; } }

/* ─────────────────────────────────────────────────────── */
/* ── Page layout                                       ── */
/* ─────────────────────────────────────────────────────── */

.am-page {
    display: flex; flex-direction: column;
    width: 100%;
    min-height: 100%;
    max-height: 100vh;
    overflow-y: auto;
    padding: 18px 20px 0;
}
.nicegui-content > .am-page,
.q-page > .am-page,
.q-page-container > .am-page { width: 100%; }
.q-page-container, .q-page { width: 100% !important; }

/* ── Section header ── */
.am-section-header {
    display: flex; justify-content: space-between; align-items: flex-start;
    margin-bottom: 16px; padding-bottom: 12px;
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
}
.am-section-title {
    font-size: 15px; font-weight: 700; color: var(--text-hi);
    font-family: 'DM Sans', sans-serif;
}
.am-section-sub {
    font-size: 11px; color: var(--text-dim);
    font-family: 'DM Mono', monospace; margin-top: 3px;
}
.am-section-actions { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }

/* ── Status bar ── */
.am-status-bar {
    display: flex; align-items: center; gap: 8px;
    padding: 7px 14px; border-top: 1px solid var(--border);
    background: var(--bg2); flex-shrink: 0;
    font-family: 'DM Mono', monospace; font-size: 11px; color: var(--text-dim);
    margin-top: auto;
}
.am-spinner {
    width: 12px; height: 12px; border-radius: 50%;
    border: 2px solid rgba(245,166,35,0.2);
    border-top-color: var(--accent);
    animation: spin 0.8s linear infinite;
    flex-shrink: 0;
}

/* ── Panel card ── */
.am-panel {
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 10px; padding: 16px;
}

/* ── Stat card ── */
.am-stat {
    background: var(--panel2); border: 1px solid var(--border);
    border-radius: 8px; padding: 12px 16px; min-width: 120px;
}
.am-stat-label {
    font-size: 10px; color: var(--text-dim);
    font-family: 'DM Mono', monospace; letter-spacing: 0.5px; margin-bottom: 4px;
}
.am-stat-value {
    font-size: 22px; font-weight: 700;
    font-family: 'DM Sans', sans-serif; line-height: 1;
}
.am-stat-sub { font-size: 10px; color: var(--text-dim2); font-family: 'DM Mono', monospace; margin-top: 4px; }

/* ── Tags / badges ── */
.am-tag {
    border-radius: 4px; font-size: 10px; font-weight: 600;
    padding: 2px 7px; font-family: 'DM Mono', monospace;
    letter-spacing: 0.4px; white-space: nowrap; display: inline-block;
    border: 1px solid;
}
.am-tag-planned  { background: rgba(245,166,35,0.12); color: #f5a623; border-color: rgba(245,166,35,0.25); }
.am-tag-bought   { background: rgba(34,211,238,0.10); color: #22d3ee; border-color: rgba(34,211,238,0.2); }
.am-tag-completed{ background: rgba(34,197,94,0.10);  color: #22c55e; border-color: rgba(34,197,94,0.2); }
.am-tag-archived { background: rgba(100,116,139,0.12);color: #64748b; border-color: rgba(100,116,139,0.2); }
.am-tag-cyan     { background: rgba(34,211,238,0.10); color: #22d3ee; border-color: rgba(34,211,238,0.2); }
.am-tag-amber    { background: rgba(245,166,35,0.12); color: #f5a623; border-color: rgba(245,166,35,0.25); }
.am-tag-green    { background: rgba(34,197,94,0.10);  color: #22c55e; border-color: rgba(34,197,94,0.2); }
.am-tag-red      { background: rgba(239,68,68,0.10);  color: #ef4444; border-color: rgba(239,68,68,0.2); }
.am-tag-slate    { background: rgba(100,116,139,0.12);color: #64748b; border-color: rgba(100,116,139,0.2); }

/* ── Filter pills ── */
.am-pill {
    padding: 4px 14px; border-radius: 20px;
    border: 1px solid var(--border);
    font-size: 11px; font-family: 'DM Sans', sans-serif;
    cursor: pointer; background: transparent;
    color: var(--text-dim); transition: all 0.1s;
    line-height: 1.4;
}
.am-pill:hover { border-color: var(--border2); color: var(--text); }
.am-pill.active {
    background: rgba(245,166,35,0.12);
    border-color: var(--accent); color: var(--accent); font-weight: 600;
}

/* ── Workflow buttons ── */
.am-wf {
    display: inline-flex; align-items: center; gap: 5px;
    padding: 6px 14px; border-radius: 6px;
    border: 1px solid var(--border) !important;
    font-size: 12px !important; font-weight: 500 !important;
    font-family: 'DM Sans', sans-serif !important;
    cursor: pointer; white-space: nowrap; letter-spacing: 0.2px;
    background: var(--panel2) !important; color: var(--text) !important;
}
.am-wf:hover { background: var(--panel) !important; border-color: var(--border2) !important; color: var(--text-hi) !important; }
.am-wf-cyan { background: #0c6880 !important; border-color: var(--cyan) !important; color: var(--cyan) !important; }
.am-wf-cyan:hover { background: #0e7490 !important; }
.am-wf-success { background: #166534 !important; border-color: var(--green) !important; color: var(--green) !important; }
.am-wf-success:hover { background: #15803d !important; }
.am-wf-danger { background: #450a0a !important; border-color: var(--red) !important; color: var(--red) !important; }
.am-wf-danger:hover { background: #7f1d1d !important; }

/* ── Sidebar nav ── */
.am-nav-item {
    display: flex; align-items: center; gap: 8px;
    padding: 8px; border-radius: 6px;
    cursor: pointer; position: relative;
    transition: background 0.15s; user-select: none;
}
.am-nav-item:hover:not(.active) { background: rgba(255,255,255,0.04); }
.am-nav-item.active { background: rgba(245,166,35,0.12); }
.am-nav-icon {
    font-size: 14px; width: 18px; text-align: center;
    flex-shrink: 0; color: var(--text-dim);
    font-style: normal;
}
.am-nav-label {
    font-size: 11px; font-weight: 600; color: var(--text);
    font-family: 'DM Sans', sans-serif;
}
.am-nav-sub {
    font-size: 8px; color: var(--text-dim2);
    font-family: 'DM Mono', monospace; margin-top: 1px;
}
.am-nav-indicator {
    position: absolute; right: 0; top: 50%; transform: translateY(-50%);
    width: 3px; height: 16px; border-radius: 2px;
    background: var(--accent); opacity: 0;
}
.am-nav-item.active .am-nav-icon  { color: var(--accent); }
.am-nav-item.active .am-nav-label { color: var(--accent); }
.am-nav-item.active .am-nav-indicator { opacity: 1; }

/* ── Log entry ── */
.am-log-wrap {
    flex: 1; overflow: auto;
    background: var(--bg2); border: 1px solid var(--border);
    border-radius: 8px; padding: 12px 16px;
    font-family: 'DM Mono', monospace;
}
.am-log-entry {
    display: flex; gap: 12px; align-items: flex-start;
    padding: 5px 0; border-bottom: 1px solid var(--panel2);
}
.am-log-entry:last-child { border-bottom: none; }
.am-log-t    { font-size: 10px; color: var(--text-dim2); white-space: nowrap; flex-shrink: 0; }
.am-log-src  { font-size: 9px; padding: 1px 6px; border-radius: 3px; white-space: nowrap; flex-shrink: 0; }
.am-log-lvl  { font-size: 9px; white-space: nowrap; flex-shrink: 0; }
.am-log-msg  { font-size: 11px; flex: 1; }
.am-log-info  { color: #94a3b8; }
.am-log-ok    { color: #22c55e; }
.am-log-warn  { color: #f5a623; }
.am-log-error { color: #ef4444; }

/* ── Progress bar ── */
.am-progress-track { height: 6px; background: var(--border); border-radius: 3px; }
.am-progress-fill {
    height: 100%; border-radius: 3px;
    background: linear-gradient(90deg, #22d3ee, #22c55e);
    transition: width 0.3s;
}

/* ── MetricRow ── */
.am-metric-label { font-size: 9px; color: var(--text-dim2); font-family: 'DM Mono', monospace; letter-spacing: 0.5px; }
.am-metric-value { font-size: 14px; font-weight: 700; font-family: 'DM Sans', sans-serif; }

/* ── Section label ── */
.am-section-label {
    font-size: 9px; color: var(--text-dim2);
    font-family: 'DM Mono', monospace; letter-spacing: 0.5px;
    margin-bottom: 6px;
}
"""
