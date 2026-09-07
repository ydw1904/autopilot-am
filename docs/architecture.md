# Architecture

## Project Overview

Agent control plane for the browser game [Airlines Manager](https://www.airlines-manager.com):
an **MCP server exposing 45 tools** over a live, logged-in game session, plus the
optimization engine and browser-automation layer it drives. Goal: maximize weekly
revenue by selecting circuits (route sets), seat configs, schedules, and prices.

Two API surfaces, one server. The mobile app's JSON API covers features the
browser lacks (the second-hand aircraft market, daily login rewards) **and**
most of what the CDP tools scrape — hubs, routes, per-route pricing and demand,
fleet, seat config, schedules (read). Where both exist the mobile path is
primary and CDP is the fallback, because the JSON is structured, needs no
browser, and reports refusals as errors instead of silent no-ops. The mobile
tools authenticate with the mobile access_token, not the web session (see
"Mobile API surface" below).

**Still CDP-only:** buying routes and aircraft through the web flow, the
demand refresh, the raw-ideal pricing mode, and one route's details page
(`route_details.py` — the mobile API has no per-line financials at all). (Flight schedules moved to the
mobile API on 2026-08-27.) Each of those is written up with what has already been
tried in [`tickets/013-mobile-api-remaining-gaps.md`](tickets/013-mobile-api-remaining-gaps.md)
— read it before attacking any of them, especially the schedule writes.

**Language:** Python 3.10+. Browser automation via Chrome DevTools Protocol (CDP,
primary) with an optional OpenClaw backend in `scraping/`. Both circuit optimization
phases run in native **Rust** (`code/native/beam_search.rs`) behind a ctypes wrapper,
with Python fallbacks. The UI is a React/Vite browser app served by FastAPI. Python
deps are
declared with minimum versions in
`code/requirements.txt` (`mcp`, `httpx`, `websocket-client`, `numpy`,
`colorama`, `fastapi`, `uvicorn`; `pytest` for the test suite).

**Pure logic has a test suite:** `.venv/bin/python -m pytest code/tests/ -q`
(offline, no Chrome, ~1s). It covers the pricing/flight-time formulas, the
schedule builder, and `db.py`. Anything that touches CDP is still verified
manually by running scripts with `--dry-run` / `--phase1-only`, and by booting
the MCP server (see [Verification](#verification)).

## Three surfaces, one core

Everything funnels through two shared layers — touch these and you touch everything:

- **`code/cdp.py`** — the single Chrome DevTools Protocol client (WebSocket transport,
  AM-tab finder, shared constants). Also strips proxy env vars on import, since CDP is
  always localhost and a system proxy breaks both `httpx` and `websocket-client`.
- **`code/db.py`** — the single SQLite access layer (`DB` path, `get_player_hub_id`,
  `mark_route_owned`, etc.).

The three surfaces on top:

1. **MCP server** (`code/mcp_server.py`) — 45 typed tools; the agent-facing control
   plane. Live-state tools call CDP directly; heavier ops shell out to the CLI scripts;
   the mobile tools call the mobile HTTP API via `mobile_api.py`.
   **Mutating tools default to `dry_run=True`.**
2. **CLI scripts** (`code/*.py`) — each game operation is a standalone `argparse`
   script, runnable and testable without an agent. The heavy ones the MCP server
   wraps (`circuit_planner`, `circuit_scheduler`, `aircraft_buyer`, `auto_pricer`,
   `masstool`) take `--json`: stdout carries exactly one JSON document and the
   human report moves to stderr, so the server parses a result instead of
   scraping ASCII tables. Without the flag the human report is unchanged.
3. **Browser app** (`code/api_server.py` + `web/src/`) — a React/TypeScript UI
   compiled by Vite and served under `/app/` alongside the REST API. `run_web.sh`
   installs dependencies when needed, builds the frontend, then starts FastAPI.
   For development, `run_dev.sh` serves Vite on port 3000 with hot reload and
   FastAPI on port 8000 with Python source auto-reload.

For persistent local operation on macOS, `launchd/install.sh` installs user
LaunchAgents for `run_dev.sh` and the observe-only SHM watcher. Both use
`KeepAlive`, start at login, and log under `~/.airlines_manager/`. The watcher
retains its database lock, so launchd cannot create a second polling loop.

## Directory Layout

```
airlines-manager/
├── AGENTS.md / README.md / MCP_SETUP.md / CHANGELOG.md
├── .mcp.json                       ← Claude Code MCP registration
└── code/
    ├── mcp_server.py               ← MCP server: 45 tools (25 web/CDP + 20 mobile)
    ├── cdp.py                      ← shared CDP client  ← SHARED LAYER
    ├── db.py                       ← shared SQLite layer ← SHARED LAYER
    ├── circuit_planner.py          ← PRIMARY: Phase 1 + Phase 2 optimization
    ├── circuit_planner_native.py   ← ctypes wrapper for both Rust optimizer phases
    ├── native/beam_search.rs       ← native optimizer; build with native/build.sh
    │
    ├── circuit_route_buyer.py      ← buy routes (CDP, country-listing flow)
    ├── aircraft_buyer.py           ← buy aircraft (CDP); reads circuit config from DB
    ├── circuit_scheduler.py        ← schedule a circuit's aircraft (mobile, CDP fallback)
    ├── auto_pricer.py              ← set corrected ideal prices in bulk (CDP, fallback)
    │
    ├── aircraft_numberer.py        ← assign canonical <HUB>-C<NNN>-<MMM> names
    ├── aircraft_reconfigurator.py  ← align aircraft hub/seat config to circuit plan
    ├── circuit_renamer.py          ← rename a circuit in DB + all its in-game aircraft
    ├── mass_renamer.py             ← rename in-game aircraft by prefix
    ├── mass_unscheduler.py         ← clear schedules for aircraft by prefix
    │
    ├── warehouse_sync.py           ← scrape fleet → DB `fleet` table
    ├── masstool.py                 ← live route prices/remaining demand (mobile, CDP fallback)
    ├── route_details.py            ← one route's /network/showline page (CDP-only)
    ├── scrape_line_ids.py          ← line_ids from /network/planning → DB
    ├── scrape_audit_line_ids.py    ← line_ids from /marketing/internalaudit → DB
    ├── scrape_internal_audits.py   ← refresh owned-route demand via /marketing/pricing
    │
    ├── mobile_api.py               ← MOBILE app JSON API client (httpx, access_token)
    ├── mobile_store.py             ← reference store for mobile ids (mobile_* tables)
    ├── mobile_pricer.py            ← PRIMARY pricer: auto_pricer over the mobile API
    ├── mobile_renamer.py           ← PRIMARY renamer: mass_renamer over the mobile API
    ├── mobile_reconfigurator.py    ← aircraft_reconfigurator over the mobile API
    ├── mobile_login.py             ← press OK/Login over adb when the session dies
    ├── booster_sync.py             ← booster drop tables + livery names/artwork → DB
    ├── skin_name_sync.py           ← livery names + Playrion/market origin → DB
    ├── purchase_date_sync.py       ← paced per-aircraft purchase-date backfill → DB
    ├── shm_watcher.py              ← watch the SHM for named liveries, buy on sight
    │
    └── scraping/                   ← demand-scrape core + CDP/OpenClaw backends
├── web/src/                         ← React browser UI; Vite output is web/dist/
│   ├── components/MenuSelect.tsx ← the ONE dropdown; never use a native <select>
│   ├── components/TagPicker.tsx  ← tag combobox: free text + preset/known tags
│   ├── components/AircraftEditor.tsx ← one aircraft, every write: name/hub/
│   │                                livery/seats/market/scrap (mobile API)
│   ├── components/Network.tsx    ← routes, map/globe, and one route's detail page
│   ├── components/Pricing.tsx    ← live price vs the audit's recommendation
│   └── components/Ops.tsx        ← delivery queue, dailies, cache freshness
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
## Data Model — SQLite (`db/am_aircraft.db`, not committed)

| Table | Purpose |
|-------|---------|
| `aircraft` | specs: model, category, speed_kmh, range_km, max_pax, max_tonnage, gross_price |
| `routes` | per-hub routes: hub_iata, dest_iata, distance_km, dest_category, stars, {eco,bus,fir,cargo}_demand, gross_price, is_owned, line_id |
| `hubs` | hub airports: hub_id, iata, name, country_code, category, price |
| `player_hubs` | the player's owned hubs (drives "for each owned hub" scrapers) |
| `circuits` / `circuit_routes` | saved circuits and their routes |
| `fleet` | scraped aircraft inventory (from `warehouse_sync`), incl. the livery as `skin_img` (picture filename, the only livery signal the web side carries) and the `skin_id` it resolves to via `mobile_skins` |
| `routes_demand_snapshot` | pre-overwrite demand snapshots (from `scrape_internal_audits`) |
| `mobile_models` / `mobile_skins` | mobile model specs + liveries: id, name, `source` (`manufacturer`/`playrion`/`market`), creator, duty free price + sold counter (from `skin_name_sync`) |
| `mobile_aircraft` | mobile account fleet (SHM auction reads are live-only; nothing is logged) |
| `mobile_boosters` / `mobile_booster_cards` | booster windows/prices/pity + published drop tables (from `booster_sync`) |
| `mobile_challenges` / `mobile_challenge_rewards` | the challenge ladder: window, rank, and every objective's reward slot on both the free and battle-pass tracks (from `skin_name_sync --challenge`) |
| `mobile_shop_offers` / `mobile_shop_offer_items` | the shop feed and the liveries each pack/gift/offer contains (from `skin_name_sync --shop`) |
| `mobile_skin_images` | livery PNG bytes (from `booster_sync --images`) |
| `mobile_skin_overview` | VIEW: livery + source/creator/price + owned-aircraft count + best drop rate + which boosters/challenges/shop offers carry it + artwork cached. Rebuilt on every `MobileStore()` open, so its columns can grow |
| `shm_watch` | liveries under standing order: price cap, copies wanted, copies bought |
| `shm_sightings` | every SHM listing the watcher has seen — the price history a `--max` is picked from |
| `shm_buys` | every buy the watcher decided on, dry runs included (the daily-budget ledger) |
| `shm_model_checks` | when each watched model was last polled, so wide watchlists rotate |

Aircraft **aliases** (e.g. `B742` → `747-200B`) live in the `ALIASES` dict in
`circuit_planner.py` (mirrored in `aircraft_buyer.py`).
