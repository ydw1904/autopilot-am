# AGENTS.md — Guide for AI Agents Working on This Codebase

For what the project *is* and how to drive it via the MCP server, see `README.md`.
This file is for an AI agent (Claude Code, etc.) editing the code.

## Project Overview

Agent control plane for the browser game [Airlines Manager](https://www.airlines-manager.com):
an **MCP server exposing 36 tools** over a live, logged-in game session, plus the
optimization engine and browser-automation layer it drives. Goal: maximize weekly
revenue by selecting circuits (route sets), seat configs, schedules, and prices.

Two API surfaces, one server: 25 **web/CDP** tools drive the browser game; 11
**mobile** tools (`mobile_*`, `shm_*`) hit the mobile app's JSON API for features
the browser lacks — the second-hand aircraft market and daily login rewards. The
mobile tools authenticate with the mobile access_token, not the web session, and
are otherwise independent of CDP (see "Mobile API surface" below).

**Language:** Python 3.10+. Browser automation via Chrome DevTools Protocol (CDP,
primary) with an optional OpenClaw backend in `scraping/`. The circuit beam search
has a hot path in native **C++** (`code/native/beam_search.cpp`) behind a ctypes
wrapper. GUI is **NiceGUI**. Deps declared with minimum versions in
`code/requirements.txt` (`mcp`, `httpx`, `websocket-client`, `numpy`,
`colorama`, `nicegui`; `pytest` for the test suite).

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

1. **MCP server** (`code/mcp_server.py`) — 36 typed tools; the agent-facing control
   plane. Live-state tools call CDP directly; heavier ops shell out to the CLI scripts;
   the mobile tools call the mobile HTTP API via `mobile_api.py`.
   **Mutating tools default to `dry_run=True`.**
2. **CLI scripts** (`code/*.py`) — each game operation is a standalone `argparse`
   script, runnable and testable without an agent. The heavy ones the MCP server
   wraps (`circuit_planner`, `circuit_scheduler`, `aircraft_buyer`, `auto_pricer`,
   `masstool`) take `--json`: stdout carries exactly one JSON document and the
   human report moves to stderr, so the server parses a result instead of
   scraping ASCII tables. Without the flag the human report is unchanged.
3. **GUI** (`code/gui_app.py` + `code/gui/`) — a NiceGUI desktop control panel over the
   same CLI/core code.

## Directory Layout

```
airlines-manager/
├── AGENTS.md / README.md / MCP_SETUP.md / CHANGELOG.md
├── .mcp.json                       ← Claude Code MCP registration
└── code/
    ├── mcp_server.py               ← MCP server: 36 tools (25 web/CDP + 11 mobile)
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
    ├── mobile_api.py               ← MOBILE app JSON API client (httpx, access_token)
    ├── mobile_store.py             ← reference store for mobile ids (mobile_* tables)
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

### Mobile API surface (`mobile_api.py`, `mobile_store.py`)
The mobile app exposes a JSON API the browser game does **not**: the second-hand
aircraft market (auction) and daily login rewards. These endpoints (`/api/{player_id}/…`)
authenticate with the **mobile access_token**, not the web session cookies — the
web/CDP session gets **401** from them, so they can't be driven through `cdp.py`.

- **`mobile_api.py`** — httpx client (`trust_env=False`, like cdp.py). `AMSession`
  loads/saves `~/.airlines_manager/session.json`; `import_from_capture()` pulls the
  newest token from a mitmproxy capture of the app (the token expires ~daily).
  `AMClient` methods: `auctions/put_up/bid` (SHM), `fleet/aircraft/model_skins`,
  `shop_offers/claim_offer` + `wheel_*` + `slot_*` (daily). No request signing.
- **Quirks baked in:** fleet paging is **1-based** (page 0 aliases page 1);
  empty POSTs (slot spins, put_up) need a zero-length form body or the server 204s;
  slot spins faster than the ~10s reel cooldown 204 but still burn a game (never
  retried); **the SHM caps active listings at 10** (`MAX_ACTIVE_LISTINGS`; the 11th
  put_up → errorCode 170011 "Auction limit reached"). `shm_sell_batch` reads the
  current listing count and lists only up to the free slots.
- **Model ids are shared across surfaces:** the mobile `aircraftListId` and the web
  purchase box's `aircraft[id]` are the same id space (spot-checked on 27 models via
  the auction feed, no mismatches), so one table serves both —
  `aircraft_buyer.AIRCRAFT_GAME_IDS`, keyed by canonical `aircraft_aliases` names.
  Re-read it off `/aircraft/buy/new/{haul}` if the game renumbers.
- **`mobile_store.py`** — best-effort reference store (mobile_* tables in the shared
  DB) populated as the client reads: model specs, skin/livery ids + Playrion status,
  the mobile fleet, and a market price-history log.
- **MCP tools:** `mobile_session_import`, `mobile_balance`, `mobile_catalog`,
  `shm_market`, `shm_fleet`, `shm_aircraft`, `shm_sell`, `shm_sell_batch`,
  `mobile_daily_status`, `mobile_daily_bonuses`, `mobile_daily_slot`. Mutating ones
  default `dry_run=True`. `mobile_daily_slot` is intentionally slow (~9s/spin).
- **`daily_routine.py`** — the freebies, once a day, tasks in random order with a
  `--jitter` start delay. Tasks: `currencies`, `slots`, `donate` (the last one via
  `alliance_donator`, so that task alone needs **Chrome up and logged in** — the
  mobile refresh cannot fix a dead web session). Calls the MCP tools directly; on
  a mobile auth error it runs
  `refresh_mobile_session.sh` once and retries that task (so BlueStacks has to be
  up). Slots are event-gated: it skips them unless `specialEvent` shows a running
  spin milestone. Scheduled by the `com.lobster.am-daily-routine` LaunchAgent at
  09:00 Asia/Shanghai = **01:00 UTC**, just after the game's daily reset; the
  jitter spreads the real start across 01:00–01:35 UTC. Log:
  `~/.airlines_manager/daily.log`.
- **Not covered yet — boosters and Bob.** `booster` and `booster/history` read
  fine, and the free Economy pack is purchase option **id 1** (`freeWithAds`, 8h
  cooldown; the account's `bypassAds` runs to 2026-09-04). But `booster/ads/purchase`
  rejects `purchaseId`/`id`/`boosterPurchaseId`/`offerId` with errorCode 10205, so
  the body shape still needs a capture. Bob is the maintenance mini-game
  (`maintenance/bob/2` → errorCode 99 as a bare GET); it is a *skill* game with
  a score submission, so automating it means posting fabricated scores — a
  different risk class from claiming a free reward. Capture both before building.
- **Where the token comes from:** `tools/mobile-capture/` — the mitmproxy capture
  pipeline that produces the JSONL `import_from_capture()` reads. `capture_am.py`
  is the mitmdump addon, `bluestacks_mitm_setup.sh` wires the emulator to the
  proxy, and `bluestacks_mitm_runbook.md` covers the manual steps (root toggle /
  APK-repackage route). `market_usage.md` records the SHM economics and the
  daily-reward gotchas. **Captures are gitignored — they hold live tokens.**

### Fleet / data-sync scripts
`aircraft_numberer`, `aircraft_reconfigurator`, `circuit_renamer`, `mass_renamer`,
`mass_unscheduler`, `warehouse_sync`, `masstool`, `scrape_line_ids`,
`scrape_audit_line_ids`, `scrape_internal_audits` — each is a focused CDP/DB CLI; see
its module docstring. Most are also wrapped as MCP tools.

### `alliance_donator.py` — daily treasury donation
Maxes the donate box at the bottom of `/alliance/profile`. It does **not** drag the
jQuery-UI slider; it reads the same state the page's own handler reads
(`#alliance-slider`'s `data-airline-money` + `data-donation-profile`,
`#donation-validation`'s `data-url`) and POSTs `donation=<amount>` to
`/alliance/donate` from the page's origin. Amount = `donationMax - airlineDonations`,
capped by cash minus `--reserve`. `donationMax` is a per-day ceiling, so the script
is idempotent — a second run donates 0. The airline pays the full amount; the
treasury gets it less `dollarTax` (10%). Cap observed 2026-08-04: **$200M/day**.

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
| `mobile_models` / `mobile_skins` | mobile model specs + skin/livery ids (creator, Playrion status) |
| `mobile_aircraft` / `mobile_market` | mobile account fleet + SHM auction price-history log |

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
  print(len(asyncio.run(mcp_server.mcp.list_tools())), 'tools')"   # -> 36 tools

# Mobile API surface (needs a valid ~/.airlines_manager/session.json)
.venv/bin/python -c "import sys; sys.path.insert(0,'code'); import mcp_server; \
  print(mcp_server.mobile_balance())"   # a dollar balance means the mobile token is live

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
5. **Mobile session expires (~daily)** — mobile tools then return an auth error; refresh
   with `mobile_session_import` (open the app so it emits a fresh call through a running
   mitmproxy capture, then import). Distinct account/token from the web session.
