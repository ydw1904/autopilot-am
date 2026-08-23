"""Design tokens and CSS — v3.0 "Crane" light theme.

Lufthansa (navy + gold), Apple (restraint, hairlines, soft shadows),
Hermès (warm neutrals). Light by default.
"""

# Python tokens (imported by page modules)
NAVY    = '#05164D'
GOLD    = '#FFAD00'
BG      = '#F5F2EC'
BG2     = '#F1EEE6'
PANEL   = '#FFFFFF'
PANEL2  = '#F8F6F1'
BORDER  = '#E5E1D6'
BORDER2 = '#CFC9BA'
TEXT    = '#4E4B43'
TEXT_HI = '#0A1E3C'
DIM     = '#8B877C'
DIM2    = '#B6B1A4'
CYAN    = '#1D6FB8'
AMBER   = '#9E7600'
GREEN   = '#1E7E46'
RED     = '#C8102E'

STATUS_COLORS = {
    'planned':   AMBER,
    'bought':    CYAN,
    'completed': GREEN,
}

CSS = r"""
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');

/* ── Design tokens ── */
:root {
    --bg:       #F5F2EC;
    --bg2:      #F1EEE6;
    --panel:    #FFFFFF;
    --panel2:   #F8F6F1;
    --border:   #E5E1D6;
    --border2:  #CFC9BA;
    --text:     #4E4B43;
    --text-hi:  #0A1E3C;
    --text-dim: #8B877C;
    --text-dim2:#B6B1A4;
    --navy:     #05164D;
    --gold:     #FFAD00;
    --accent:   #9E7600;
    --cyan:     #1D6FB8;
    --green:    #1E7E46;
    --red:      #C8102E;
    --purple:   #7C5CBF;
    --shadow-sm: 0 1px 2px rgba(10,30,60,0.05);
    --shadow-md: 0 4px 16px rgba(10,30,60,0.08);
    --shadow-lg: 0 10px 32px rgba(10,30,60,0.12);
}

/* ── Base reset ── */
*, *::before, *::after { box-sizing: border-box; }
body, html {
    background: var(--bg) !important;
    color: var(--text) !important;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'SF Pro Text',
                 'Helvetica Neue', Arial, sans-serif !important;
    font-feature-settings: 'tnum' 1, 'cv05' 1;
    height: 100vh; overflow: hidden; margin: 0;
    -webkit-font-smoothing: antialiased;
}
.nicegui-content {
    background: var(--bg) !important;
    padding: 0 !important;
    min-height: unset !important;
    height: 100%;
    display: flex; flex-direction: column;
}

/* ── Quasar layout ── */
.q-drawer {
    background: var(--panel) !important;
    border-right: 1px solid var(--border) !important;
}
.q-drawer__content { overflow: hidden !important; display: flex; flex-direction: column; height: 100vh; }
.q-layout__shadow { display: none !important; }
/* Do NOT override padding — Quasar uses padding-left here to offset the permanent drawer */
.q-page-container { overflow-y: auto !important; }
.q-page { min-height: unset !important; height: 100% !important; overflow-y: auto !important; }

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 4px; border: 2px solid var(--bg); }
::-webkit-scrollbar-thumb:hover { background: var(--text-dim2); }

/* ── QTable ── */
.q-table__container {
    background: var(--panel) !important;
    border: 1px solid var(--border) !important;
    border-radius: 12px !important;
    overflow: hidden !important;
    box-shadow: var(--shadow-sm) !important;
}
.q-table { background: transparent !important; font-family: 'JetBrains Mono', ui-monospace, monospace !important; }
.q-table thead tr { background: var(--panel2) !important; }
.q-table thead th {
    color: var(--text-dim) !important; font-size: 10px !important;
    font-weight: 600 !important; letter-spacing: 0.8px !important;
    text-transform: uppercase !important;
    border-bottom: 1px solid var(--border) !important;
    padding: 10px 14px !important; white-space: nowrap !important;
    position: sticky; top: 0; z-index: 1;
    background: var(--panel2) !important;
}
.q-table tbody td {
    color: var(--text); font-size: 12px;
    padding: 8px 14px !important;
    border-bottom: 1px solid var(--panel2) !important;
    white-space: nowrap !important;
}
.q-table tbody tr:hover td { background: rgba(5,22,77,0.025) !important; }
.q-table tbody tr.selected td {
    background: rgba(255,173,0,0.10) !important;
    box-shadow: inset 2px 0 0 var(--gold) !important;
}
.q-table tbody tr:nth-child(even) td { background: rgba(5,22,77,0.012); }
.q-table tbody tr:hover:nth-child(even) td { background: rgba(5,22,77,0.025) !important; }
.q-table__bottom {
    background: var(--panel2) !important;
    border-top: 1px solid var(--border) !important;
    color: var(--text-dim) !important;
    font-size: 11px !important;
}

/* ── QBtn ── */
.q-btn {
    text-transform: none !important;
    font-family: 'Inter', sans-serif !important;
    letter-spacing: 0.1px !important;
    font-weight: 500 !important;
}

/* ── QField / inputs (component-level `dark` props are overridden to light) ── */
.q-field__control {
    background: var(--panel) !important;
    border: 1px solid var(--border2) !important;
    border-radius: 8px !important;
    color: var(--text-hi) !important;
}
.q-field--dark .q-field__control { background: var(--panel) !important; color: var(--text-hi) !important; }
.q-field--outlined .q-field__control:before { border: none !important; }
.q-field--outlined .q-field__control:after { border: none !important; }
.q-field--focused .q-field__control {
    border-color: var(--navy) !important;
    box-shadow: 0 0 0 3px rgba(255,173,0,0.25) !important;
}
.q-field__label { color: var(--text-dim) !important; font-size: 11px !important; font-family: 'Inter', sans-serif !important; letter-spacing: 0.2px !important; }
.q-field__native, .q-field__input {
    color: var(--text-hi) !important; font-size: 12px !important;
    font-family: 'JetBrains Mono', ui-monospace, monospace !important;
}
.q-field__marginal { color: var(--text-dim) !important; }
.q-field .q-icon { color: var(--text-dim) !important; }
.q-chip { background: var(--bg2) !important; color: var(--text-hi) !important; font-size: 11px !important; }
.q-chip .q-icon { color: var(--text-dim) !important; }

/* ── QMenu / dropdowns ── */
.q-menu {
    background: var(--panel) !important;
    border: 1px solid var(--border) !important;
    border-radius: 10px !important;
    box-shadow: var(--shadow-lg) !important;
    color: var(--text) !important;
}
.q-item { color: var(--text) !important; font-size: 12px !important; min-height: 32px !important; }
.q-item:hover, .q-item.q-manual-focusable--focused { background: rgba(5,22,77,0.05) !important; }
.q-item--active { color: var(--navy) !important; font-weight: 600 !important; }
.q-separator { background: var(--border) !important; }

/* ── QDialog ── */
.q-dialog__inner .q-card {
    background: var(--panel) !important;
    border: 1px solid var(--border);
    border-radius: 14px !important;
    box-shadow: var(--shadow-lg) !important;
    color: var(--text) !important;
}

/* ── QCheckbox / QToggle ── */
.q-checkbox__bg { border-color: var(--border2) !important; border-radius: 4px !important; }
.q-checkbox__inner--truthy .q-checkbox__bg { background: var(--navy) !important; border-color: var(--navy) !important; }
.q-checkbox__label { color: var(--text) !important; font-size: 12px !important; }

/* ── QTooltip ── */
.q-tooltip { background: var(--navy) !important; color: #fff !important; font-size: 11px !important; border-radius: 6px !important; }

/* ── Animations ── */
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes pulseDot { 0%,100% { opacity:1; box-shadow:0 0 0 0 rgba(255,173,0,0.35); } 50% { opacity:0.75; box-shadow:0 0 0 4px rgba(255,173,0,0); } }

/* ─────────────────────────────────────────────────────── */
/* ── Page layout                                       ── */
/* ─────────────────────────────────────────────────────── */

.am-page {
    display: flex; flex-direction: column;
    width: 100%;
    min-height: 100%;
    max-height: 100vh;
    overflow-y: auto;
    padding: 28px 32px 0;
}
.nicegui-content > .am-page,
.q-page > .am-page,
.q-page-container > .am-page { width: 100%; }
.q-page-container, .q-page { width: 100% !important; }

/* ── Section header ── */
.am-section-header {
    display: flex; justify-content: space-between; align-items: flex-end;
    margin-bottom: 20px; padding-bottom: 16px;
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
}
.am-section-title {
    font-size: 21px; font-weight: 700; color: var(--text-hi);
    font-family: 'Inter', sans-serif;
    letter-spacing: -0.3px;
    line-height: 1.2;
}
.am-section-sub {
    font-size: 12px; color: var(--text-dim);
    font-family: 'Inter', sans-serif; margin-top: 4px;
}
.am-section-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }

/* ── Status bar ── */
.am-status-bar {
    display: flex; align-items: center; gap: 8px;
    padding: 10px 4px; border-top: 1px solid var(--border);
    background: transparent; flex-shrink: 0;
    font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--text-dim);
    margin-top: auto;
}
.am-spinner {
    width: 12px; height: 12px; border-radius: 50%;
    border: 2px solid rgba(255,173,0,0.25);
    border-top-color: var(--gold);
    animation: spin 0.8s linear infinite;
    flex-shrink: 0;
}

/* ── Panel card ── */
.am-panel {
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 14px; padding: 20px;
    box-shadow: var(--shadow-sm);
}

/* ── Stat card ── */
.am-stat {
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 12px; padding: 14px 18px; min-width: 130px;
    box-shadow: var(--shadow-sm);
}
.am-stat-label {
    font-size: 10px; color: var(--text-dim);
    font-family: 'Inter', sans-serif; font-weight: 600;
    letter-spacing: 0.9px; text-transform: uppercase; margin-bottom: 6px;
}
.am-stat-value {
    font-size: 24px; font-weight: 700;
    font-family: 'Inter', sans-serif; line-height: 1.1;
    color: var(--text-hi); letter-spacing: -0.4px;
}
.am-stat-sub { font-size: 10px; color: var(--text-dim2); font-family: 'Inter', sans-serif; margin-top: 5px; }

/* ── Tags / badges ── */
.am-tag {
    border-radius: 20px; font-size: 10px; font-weight: 600;
    padding: 3px 10px; font-family: 'Inter', sans-serif;
    letter-spacing: 0.3px; white-space: nowrap; display: inline-block;
    border: 1px solid;
}
.am-tag-planned  { background: rgba(255,173,0,0.14); color: #8A6400; border-color: rgba(255,173,0,0.4); }
.am-tag-bought   { background: rgba(29,111,184,0.10); color: #1D6FB8; border-color: rgba(29,111,184,0.28); }
.am-tag-completed{ background: rgba(30,126,70,0.10);  color: #1E7E46; border-color: rgba(30,126,70,0.28); }
.am-tag-archived { background: rgba(139,135,124,0.12);color: #8B877C; border-color: rgba(139,135,124,0.28); }
.am-tag-cyan     { background: rgba(29,111,184,0.10); color: #1D6FB8; border-color: rgba(29,111,184,0.28); }
.am-tag-amber    { background: rgba(255,173,0,0.14); color: #8A6400; border-color: rgba(255,173,0,0.4); }
.am-tag-green    { background: rgba(30,126,70,0.10);  color: #1E7E46; border-color: rgba(30,126,70,0.28); }
.am-tag-red      { background: rgba(200,16,46,0.08);  color: #C8102E; border-color: rgba(200,16,46,0.24); }
.am-tag-slate    { background: rgba(139,135,124,0.12);color: #8B877C; border-color: rgba(139,135,124,0.28); }

/* ── Filter pills ── */
.am-pill {
    padding: 5px 15px; border-radius: 20px;
    border: 1px solid var(--border2);
    font-size: 12px; font-family: 'Inter', sans-serif; font-weight: 500;
    cursor: pointer; background: var(--panel);
    color: var(--text-dim); transition: all 0.12s ease;
    line-height: 1.4;
}
.am-pill:hover { border-color: var(--text-dim2); color: var(--text-hi); }
.am-pill.active {
    background: var(--navy);
    border-color: var(--navy); color: #FFFFFF; font-weight: 600;
}

/* ── Workflow buttons ── */
.am-wf {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 7px 16px; border-radius: 8px;
    border: 1px solid var(--border2) !important;
    font-size: 12px !important; font-weight: 500 !important;
    font-family: 'Inter', sans-serif !important;
    cursor: pointer; white-space: nowrap; letter-spacing: 0.1px;
    background: var(--panel) !important; color: var(--text) !important;
    box-shadow: var(--shadow-sm);
    transition: all 0.12s ease;
}
.am-wf:hover { border-color: var(--text-dim2) !important; color: var(--text-hi) !important; box-shadow: var(--shadow-md); }
.am-wf-cyan { background: var(--navy) !important; border-color: var(--navy) !important; color: #FFFFFF !important; }
.am-wf-cyan:hover { background: #0A2470 !important; border-color: #0A2470 !important; }
.am-wf-success { background: #1E7E46 !important; border-color: #1E7E46 !important; color: #FFFFFF !important; }
.am-wf-success:hover { background: #175F36 !important; border-color: #175F36 !important; }
.am-wf-danger { background: #FFFFFF !important; border-color: rgba(200,16,46,0.5) !important; color: #C8102E !important; }
.am-wf-danger:hover { background: #C8102E !important; border-color: #C8102E !important; color: #FFFFFF !important; }

/* ── Sidebar nav ── */
.am-nav-item {
    display: flex; align-items: center; gap: 10px;
    padding: 8px 10px; border-radius: 8px;
    cursor: pointer; position: relative;
    transition: background 0.12s ease; user-select: none;
}
.am-nav-item:hover:not(.active) { background: rgba(5,22,77,0.04); }
.am-nav-item.active { background: rgba(255,173,0,0.14); }
.am-nav-icon {
    font-size: 14px; width: 20px; text-align: center;
    flex-shrink: 0; color: var(--text-dim);
    font-style: normal;
}
.am-nav-label {
    font-size: 12.5px; font-weight: 600; color: var(--text);
    font-family: 'Inter', sans-serif; letter-spacing: 0.1px;
}
.am-nav-sub {
    font-size: 10px; color: var(--text-dim2);
    font-family: 'Inter', sans-serif; margin-top: 1px; font-weight: 400;
}
.am-nav-indicator {
    position: absolute; left: -6px; top: 50%; transform: translateY(-50%);
    width: 3px; height: 18px; border-radius: 2px;
    background: var(--gold); opacity: 0;
}
.am-nav-item.active .am-nav-icon  { color: var(--navy); }
.am-nav-item.active .am-nav-label { color: var(--navy); }
.am-nav-item.active .am-nav-indicator { opacity: 1; }

/* ── Log entry ── */
.am-log-wrap {
    flex: 1; overflow: auto;
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 12px; padding: 14px 18px;
    font-family: 'JetBrains Mono', monospace;
    box-shadow: var(--shadow-sm);
}
.am-log-entry {
    display: flex; gap: 12px; align-items: flex-start;
    padding: 5px 0; border-bottom: 1px solid var(--panel2);
}
.am-log-entry:last-child { border-bottom: none; }
.am-log-t    { font-size: 10px; color: var(--text-dim2); white-space: nowrap; flex-shrink: 0; }
.am-log-src  { font-size: 9px; padding: 1px 6px; border-radius: 10px; white-space: nowrap; flex-shrink: 0; font-weight: 600; }
.am-log-lvl  { font-size: 9px; white-space: nowrap; flex-shrink: 0; }
.am-log-msg  { font-size: 11px; flex: 1; }
.am-log-info  { color: #4E4B43; }
.am-log-ok    { color: #1E7E46; }
.am-log-warn  { color: #9E7600; }
.am-log-error { color: #C8102E; }

/* ── Progress bar ── */
.am-progress-track { height: 6px; background: var(--bg2); border-radius: 3px; }
.am-progress-fill {
    height: 100%; border-radius: 3px;
    background: linear-gradient(90deg, var(--navy), var(--gold));
    transition: width 0.3s;
}

/* ── MetricRow ── */
.am-metric-label {
    font-size: 9px; color: var(--text-dim2);
    font-family: 'Inter', sans-serif; font-weight: 600;
    letter-spacing: 0.7px; text-transform: uppercase;
}
.am-metric-value { font-size: 14px; font-weight: 600; font-family: 'Inter', sans-serif; color: var(--text-hi); }

/* ── Section label ── */
.am-section-label {
    font-size: 10px; color: var(--text-dim);
    font-family: 'Inter', sans-serif; font-weight: 600;
    letter-spacing: 0.9px; text-transform: uppercase;
    margin-bottom: 8px;
}

/* ── Card Grids ── */
.am-card-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
    gap: 14px;
    width: 100%;
}
.am-livery-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    gap: 18px;
    width: 100%;
}

/* ── Aircraft Card ── */
.am-aircraft-card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 14px 16px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    transition: border-color 0.15s, box-shadow 0.15s, transform 0.15s;
    position: relative;
    overflow: hidden;
    box-shadow: var(--shadow-sm);
}
.am-aircraft-card:hover {
    border-color: var(--border2);
    box-shadow: var(--shadow-md);
    transform: translateY(-1px);
}

/* ── Livery Card ── */
.am-livery-card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    transition: border-color 0.15s, box-shadow 0.15s, transform 0.15s;
    position: relative;
    overflow: hidden;
    box-shadow: var(--shadow-sm);
}
.am-livery-card:hover {
    border-color: var(--border2);
    box-shadow: var(--shadow-md);
    transform: translateY(-2px);
}
.am-livery-img-wrap {
    width: 100%;
    height: 130px;
    background: linear-gradient(180deg, #FBFAF6 0%, #F1EEE6 100%);
    border: 1px solid var(--border);
    border-radius: 10px;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
    position: relative;
}
.am-livery-img-wrap img {
    max-width: 100%;
    max-height: 100%;
    object-fit: contain;
    filter: drop-shadow(0 4px 8px rgba(10,30,60,0.18));
    transition: transform 0.25s ease-in-out;
}
.am-livery-card:hover .am-livery-img-wrap img {
    transform: scale(1.04);
}

/* ── Rarity badges ── */
.am-rarity-r4 { background: rgba(255,173,0,0.16) !important; color: #8A6400 !important; border-color: rgba(255,173,0,0.45) !important; font-weight: 700 !important; }
.am-rarity-r3 { background: rgba(124,92,191,0.12) !important; color: #6B4FB3 !important; border-color: rgba(124,92,191,0.32) !important; font-weight: 600 !important; }
.am-rarity-r2 { background: rgba(29,111,184,0.10) !important; color: #1D6FB8 !important; border-color: rgba(29,111,184,0.30) !important; }
.am-rarity-r1 { background: rgba(30,126,70,0.10) !important; color: #1E7E46 !important; border-color: rgba(30,126,70,0.30) !important; }
.am-rarity-r0 { background: rgba(139,135,124,0.12) !important; color: #8B877C !important; border-color: rgba(139,135,124,0.30) !important; }

/* ── Ownership Badges ── */
.am-badge-owned {
    background: rgba(30,126,70,0.10) !important;
    color: #1E7E46 !important;
    border: 1px solid rgba(30,126,70,0.30) !important;
    border-radius: 20px;
    padding: 3px 10px;
    font-size: 10px;
    font-weight: 600;
    font-family: 'Inter', sans-serif;
    letter-spacing: 0.3px;
    display: inline-flex;
    align-items: center;
    gap: 4px;
}
.am-badge-unowned {
    background: rgba(139,135,124,0.10) !important;
    color: #8B877C !important;
    border: 1px solid rgba(139,135,124,0.25) !important;
    border-radius: 20px;
    padding: 3px 10px;
    font-size: 10px;
    font-weight: 500;
    font-family: 'Inter', sans-serif;
    letter-spacing: 0.3px;
    display: inline-flex;
    align-items: center;
    gap: 4px;
}

/* ── Aircraft Chips in Liveries ── */
.am-plane-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    background: var(--panel2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 3px 8px;
    font-size: 10px;
    font-family: 'JetBrains Mono', monospace;
    color: var(--text-hi);
    white-space: nowrap;
    user-select: none;
    transition: all 0.12s ease;
}
.am-plane-chip:hover {
    border-color: var(--navy);
    color: var(--navy);
}
.am-plane-chip-hub {
    font-size: 9px;
    color: var(--text-dim);
    background: var(--bg2);
    padding: 1px 4px;
    border-radius: 3px;
}
"""
