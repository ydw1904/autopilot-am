# AGENTS.md — Guide for AI Agents Working on This Codebase

For what the project *is* and how to drive it via the MCP server, see `README.md`.
This file is for an AI agent (Claude Code, etc.) editing the code.

## Project Overview

Agent control plane for the browser game [Airlines Manager](https://www.airlines-manager.com):
an **MCP server exposing 24 tools** over a live, logged-in game session, plus the
optimization engine and browser-automation layer it drives. Goal: maximize weekly
revenue by selecting circuits (route sets), seat configs, schedules, and prices.

**Language:** Python 3.10+. Browser automation via Chrome DevTools Protocol (CDP,
primary) with an optional OpenClaw backend in `scraping/`. The circuit beam search
has a hot path in native **C++** (`code/native/beam_search.cpp`) behind a ctypes
wrapper. GUI is **NiceGUI**. Deps pinned in `code/requirements.txt`
(`mcp`, `httpx`, `websocket-client`, `numpy`, `colorama`, `nicegui`).

**No test suite.** Verify changes by running scripts with `--dry-run` /
`--phase1-only`, and by booting the MCP server (see [Verification](#verification)).

## Three surfaces, one core

Everything funnels through two shared layers — touch these and you touch everything:

- **`code/cdp.py`** — the single Chrome DevTools Protocol client (WebSocket transport,
  AM-tab finder, shared constants). Also strips proxy env vars on import, since CDP is
  always localhost and a system proxy breaks both `httpx` and `websocket-client`.
- **`code/db.py`** — the single SQLite access layer (`DB` path, `get_player_hub_id`,
  `mark_route_owned`, etc.).

The three surfaces on top:

1. **MCP server** (`code/mcp_server.py`) — 24 typed tools; the agent-facing control
   plane. Live-state tools call CDP directly; heavier ops shell out to the CLI scripts.
   **Mutating tools default to `dry_run=True`.**
2. **CLI scripts** (`code/*.py`) — each game operation is a standalone `argparse`
   script, runnable and testable without an agent.
3. **GUI** (`code/gui_app.py` + `code/gui/`) — a NiceGUI desktop control panel over the
   same CLI/core code.

## Directory Layout

```
airlines-manager/
├── AGENTS.md / README.md / MCP_SETUP.md / CHANGELOG.md
├── .mcp.json                       ← Claude Code MCP registration
└── code/
    ├── mcp_server.py               ← MCP server: 24 tools
    ├── cdp.py                      ← shared CDP client  ← SHARED LAYER
    ├── db.py                       ← shared SQLite layer ← SHARED LAYER
    ├── circuit_planner.py          ← PRIMARY: Phase 1 + Phase 2 optimization
    ├── circuit_planner_native.py   ← ctypes wrapper for the C++ beam search
    ├── native/beam_search.cpp      ← native beam search; build with native/build.sh
    │
    ├── circuit_route_buyer.py      ← buy routes (CDP, country-listing flow)
    ├── aircraft_buyer.py           ← buy aircraft (CDP); reads circuit config from DB
    ├── circuit_scheduler.py        ← schedule flights for a circuit's aircraft (CDP)
    ├── auto_pricer.py              ← set corrected ideal prices in bulk (CDP)
    │
    ├── aircraft_numberer.py        ← assign canonical <HUB>-C<NNN>-<MMM> names
    ├── aircraft_reconfigurator.py  ← align aircraft hub/seat config to circuit plan
    ├── circuit_renamer.py          ← rename a circuit in DB + all its in-game aircraft
    ├── mass_renamer.py             ← rename in-game aircraft by prefix
    ├── mass_unscheduler.py         ← clear schedules for aircraft by prefix
    │
    ├── warehouse_sync.py           ← scrape fleet → DB `fleet` table
    ├── masstool.py                 ← live route prices/remaining demand via pricingAjax
    ├── scrape_line_ids.py          ← line_ids from /network/planning → DB
    ├── scrape_audit_line_ids.py    ← line_ids from /marketing/internalaudit → DB
    ├── scrape_internal_audits.py   ← refresh owned-route demand via /marketing/pricing
    │
    ├── scraping/                   ← demand-scrape core + CDP/OpenClaw backends
    └── gui/                        ← NiceGUI pages (planner, hub, library, mass,
                                       warehouse, scraper, log) + state/workers/theme
```

Generated state is **not** committed: the SQLite DB (`db/*.db`) and the `data/`
directory are local. The repo ships the code that produces and consumes them.

## Key Concepts (brief — full formulas in README.md)

- **Circuit:** a set of routes whose round-trip flight times sum to ≤168 h (one game
  week). All routes in a circuit share one aircraft seat config.
- **Wave:** 7 aircraft (one per day, Mon–Sun) flying the whole circuit once daily.
  More waves = more daily flights per route.
- **Capacity:** `2 × seats × waves` per day per route per class (×2 for round trip).
- **SuperSim pricing:** when capacity < demand, price rises up to ~33%
  (`FLOOR(audit × (1 − (cap−dem)/(3·dem)))`).
- **Demand constraint:** capacity must never exceed demand for any route/class — the
  game treats violations as "negative demand."
- **Shared config coupling:** since all routes in a circuit share one config, the
  lowest-demand class on any single route caps that class for the whole circuit. This
  is why Phase 2 grid-searches the full config rather than scoring routes independently.
- **Sequential exclusion:** after circuit N is found its routes are locked out; N+1 is
  built from the remainder. Greedy sequential, not joint optimization.

## Module Reference (high-traffic ones)

### `circuit_planner.py` — primary optimizer
Two phases. **Phase 1** (`search_circuits`): beam search over route combinations,
each scored by `quick_revenue_estimate()`; filtered by a demand-balance ratio
(`--match`). The hot loop is delegated to the native C++ search via
`circuit_planner_native.search_circuits_native` when the dylib is built. **Phase 2**
(`optimize_circuit`): coarse + fine grid search over `(eco,bus,fir,cargo)` seats and
wave count, using SuperSim pricing. Pricing helpers (`ideal_eco/bus/fir/cargo`,
`supersim_price`, `daily_turnover`) and the `ALIASES` map live here.

### `circuit_planner_native.py` + `native/beam_search.cpp`
ctypes wrapper + native beam search. Drop-in for the older Rust/pyo3 extension: same
module name, same `search_circuits_native` signature, same return shape
`[(score, total_time, [route_idx, …]), …]`. Build with `code/native/build.sh`
(needs `-ffp-contract=off` to match Python float math).

### `circuit_route_buyer.py` — route purchaser (CDP)
Default flow drives the game's country-listing page and submits the real form with
native `form.submit()`. The game silently rejects `fetch()`-based purchase POSTs
(returns `200 OK` without buying), so do **not** "simplify" this back to `fetch()`.
Can take IATAs directly or `--circuit NAME` (loads routes from DB).

### `aircraft_buyer.py` — aircraft purchaser (CDP)
Find aircraft on the list page → click Buy (AJAX configure form) → set hub / seat
config / quantity → submit via jQuery trigger. Reads circuit config from DB; has a
game-id lookup for aircraft models. Also exposes `get_balance()`, reused by the MCP
server.

### `auto_pricer.py` — corrected ideal-price setter
The game's displayed "Ideal price" is correct for **eco** and **cargo** but **wrong**
for business/first. Always derive: `bus = floor(eco × 1.33)`, `fir = floor(eco × 2.3)`.
Modes: `ideal` (corrected, default), `percent --pct N`, `raw-ideal` (uncorrected).

### `circuit_scheduler.py` — flight scheduler
Reads circuit config from DB, resolves game ids for aircraft + routes, submits
schedules via the planning AJAX API. Exposes `get_lines_at_hub()` (reused by MCP).

### Fleet / data-sync scripts
`aircraft_numberer`, `aircraft_reconfigurator`, `circuit_renamer`, `mass_renamer`,
`mass_unscheduler`, `warehouse_sync`, `masstool`, `scrape_line_ids`,
`scrape_audit_line_ids`, `scrape_internal_audits` — each is a focused CDP/DB CLI; see
its module docstring. Most are also wrapped as MCP tools.

## Data Model — SQLite (`db/am_aircraft.db`, not committed)

| Table | Purpose |
|-------|---------|
| `aircraft` | specs: model, category, speed_kmh, range_km, max_pax, max_tonnage, gross_price |
| `routes` | per-hub routes: hub_iata, dest_iata, distance_km, dest_category, {eco,bus,fir,cargo}_demand, audit_price_*, line_id, gross_price |
| `hubs` | hub airports: hub_id, iata, name, country_code, category, price |
| `player_hubs` | the player's owned hubs (drives "for each owned hub" scrapers) |
| `circuits` / `circuit_routes` | saved circuits and their routes |
| `fleet` | scraped aircraft inventory (from `warehouse_sync`) |
| `routes_demand_snapshot` | pre-overwrite demand snapshots (from `scrape_internal_audits`) |

Aircraft **aliases** (e.g. `B742` → `747-200B`) live in the `ALIASES` dict in
`circuit_planner.py` (mirrored in `aircraft_buyer.py`).

## Code Conventions

- **Paths:** never hardcode. Use `db.py`'s `DB` constant and the
  `os.path.dirname(os.path.abspath(__file__))` pattern. The repo was cleaned of
  hardcoded absolute paths — don't reintroduce them.
- **Shared layers first:** new CDP work goes through `cdp.py`; new DB work through
  `db.py`. Don't add a per-script CDP client or a second DB-path definition.
- **CLI:** every script uses `argparse` and a `main()`; mutating scripts expose
  `--dry-run`.
- **DB access:** direct `sqlite3`, no ORM. Parameterize queries.
- **MCP tools:** mutating tools default `dry_run=True` and return a structured `dict`.
  Keep that contract when adding tools.
- **Purchase reliability:** use the country-listing `form.submit()` flow, not `fetch()`
  (see `circuit_route_buyer.py` above).

## Verification

No automated tests — verify manually.

```bash
# Planner smoke test (no game connection needed)
python3 code/circuit_planner.py --hub HKG --aircraft B742 --circuits 2 --phase1-only
python3 code/circuit_planner.py --hub HKG --aircraft B742 --circuits 2   # full Phase 1+2

# MCP server boots and registers all tools
.venv/bin/python -c "import asyncio,sys; sys.path.insert(0,'code'); import mcp_server; \
  print(len(asyncio.run(mcp_server.mcp.list_tools())), 'tools')"   # -> 24 tools

# Live stack (Chrome up + logged in): a dollar balance means CDP + session work
.venv/bin/python -c "import sys; sys.path.insert(0,'code'); import mcp_server; \
  print(mcp_server.get_balance())"

# DB sanity
sqlite3 db/am_aircraft.db "SELECT COUNT(*) FROM routes WHERE hub_iata='HKG' AND eco_demand>0"
```

Rebuild the native search after editing `native/beam_search.cpp`:
`bash code/native/build.sh` (the planner falls back to pure Python if the dylib is
absent).

## Common Tasks

- **Add an aircraft:** confirm it's in `aircraft`, add to `ALIASES` in
  `circuit_planner.py` (and `aircraft_buyer.py` if purchasing).
- **Add a hub:** confirm routes exist (`SELECT COUNT(*) … WHERE hub_iata='XXX'`), run
  the planner with `--phase1-only`; for buying, get the numeric hub id from the game URL
  (differs from the IATA code).
- **Change pricing:** edit the `ideal_*` / `supersim_price` / `daily_turnover` helpers
  in `circuit_planner.py`. Remember bus/fir are eco multipliers, not independent.
- **Add an MCP tool:** wrap the corresponding CLI/core function, give it a typed
  signature, default any mutation to `dry_run=True`, return a `dict`.

## Known Issues

1. **No automated test suite** — verification is manual.
2. **`ALIASES` is duplicated** in `circuit_planner.py` and `aircraft_buyer.py` — keep
   them in sync when adding aircraft.
3. **Native dylib is not committed** (`*.dylib`/`*.so` are gitignored) — rebuild with
   `code/native/build.sh`; the planner falls back to pure Python without it.
4. **`aircraft_buyer.py` game-id table** may need a manual lookup for aircraft outside
   the current set.
