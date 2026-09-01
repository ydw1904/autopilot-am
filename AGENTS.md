# AGENTS.md — Guide for AI Agents Working on This Codebase

For what the project *is* and how to drive it via the MCP server, see `README.md`.

**This is the single source of truth for every AI coding agent working in this repo**
— Claude Code, OpenCode, Codex, Cursor, Gemini CLI, or anything else. `CLAUDE.md` and
`GEMINI.md` are one-line pointers back here; do not duplicate guidance into them. If
you learn something durable about this codebase, edit *this* file.

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
demand refresh, and the raw-ideal pricing mode. (Flight schedules moved to the
mobile API on 2026-08-27.) Each of those is written up with what has already been
tried in [`tickets/013-mobile-api-remaining-gaps.md`](tickets/013-mobile-api-remaining-gaps.md)
— read it before attacking any of them, especially the schedule writes.

**Language:** Python 3.10+. Browser automation via Chrome DevTools Protocol (CDP,
primary) with an optional OpenClaw backend in `scraping/`. The circuit beam search
has a hot path in native **C++** (`code/native/beam_search.cpp`) behind a ctypes
wrapper. The UI is a React/Vite browser app served by FastAPI. Python deps are
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
    ├── circuit_planner_native.py   ← ctypes wrapper for the C++ beam search
    ├── native/beam_search.cpp      ← native beam search; build with native/build.sh
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
│   ├── components/Hangar.tsx     ← one aircraft, every write: name/hub/livery/
│   │                                seats/market/scrap (mobile API)
│   ├── components/Network.tsx    ← circuits + their routes + hub coverage (cache)
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
schedules. Mobile-primary: `_mobile_hub_view()` reads the hub's aircraft and
lines in two requests (`hub/masstool/{id}` + the fleet listing) and
`clear_schedule` / `submit_flights` take either an `AMClient` or a CDP handle —
so with a mobile session on disk the whole run needs no browser. Without one it
falls back to the planning page and the `/network/planning/0/ajax` POST. On the
mobile path `util` is only 0 or 100 (idle vs flying), which is all `--only-new`
needs. Exposes `get_lines_at_hub()` (reused by MCP).

### Mobile API surface (`mobile_api.py`, `mobile_store.py`)
The mobile app exposes a JSON API the browser game does **not**: the second-hand
aircraft market (auction) and daily login rewards. These endpoints (`/api/{player_id}/…`)
authenticate with the **mobile access_token**, not the web session cookies — the
web/CDP session gets **401** from them, so they can't be driven through `cdp.py`.

- **Sessions renew themselves over HTTP — the emulator is a one-time bootstrap.**
  Login is plain OAuth2 at `auth.airlines-manager.com/oauth/v2/token`, so once
  `import_from_capture()` has harvested the app's client id/secret, device id and
  credentials (it does this automatically now), `AMSession.renew()` mints a new
  token with one POST: refresh-token grant first, password grant as fallback.
  `AMClient` calls it automatically on an auth error and retries, so a long
  sweep can't die halfway. `refresh_mobile_session.sh` tries this first and only
  falls back to the BlueStacks/mitmproxy path if it can't (2.6s vs 22.6s).
  An aircraft's exact purchase timestamp is `profile.purchasedAt` on
  `aircraft/{id}` only. The compact `bfa/paged/aircraft` records do not carry it,
  so the fleet UI fetches one requested profile and caches the timestamp in
  `mobile_aircraft.purchased_at`; never add a thousands-request purchase-date
  backfill to the normal fleet sync.
  Three things that bite:
  **(1) `?version=40008` is a QUERY param on the token endpoint and is
  load-bearing.** Omit it and the grant still returns HTTP 200 with a
  usable-looking token, but the session is stamped `version: 0` and the newer
  endpoints reject it — error 10205 on `bfa/paged/aircraft`, "Update your game
  to access the new secondhand market features" on the auctions, while
  `aircraft/{id}` and `bfa/hub` keep working. `_token_request` refuses a
  version-0 grant so this fails loudly. Bump `APP_VERSION` when the app does.
  **(2) Refresh tokens are single-use and rotate** — replaying one gives
  `Invalid refresh token` (errorCode 10), so the new one must be persisted on
  every grant. Lose it once and only the password grant can recover.
  **(3) Tokens live 3h (`expires_in: 10800`), not ~daily** as the old
  capture-based flow assumed.
- **`mobile_api.py`** — httpx client (`trust_env=False`, like cdp.py). `AMSession`
  loads/saves `~/.airlines_manager/session.json` (0600 — it holds the token, the
  refresh token and, if bootstrapped from a password grant, the password);
  `import_from_capture()` pulls the newest token plus the OAuth material.
  `AMClient` methods: `auctions/put_up/bid` (SHM), `fleet/aircraft`,
  `shop_skins/model_skins/skin_catalog` (liveries), `reconfigure/assign_hub`
  (fleet config), `shop_offers/claim_offer` + `wheel_*` + `slot_*` (daily).
  No request signing.
- **`sell_for_scrap()` is the one UNVERIFIED write.** `aircraft/sellOrStopRent`
  is read off the APK metadata; the body was never captured, so `aircraftId` is
  an educated guess and a wrong guess is simply refused (`status: 0`). That is
  why it takes one id, has no batch form and no apply-to-all flag — an
  unverified irreversible write must not be able to cascade. Capture a real
  scrap before trusting it (ticket 013).
- **Quirks baked in:** fleet paging is **1-based** (page 0 aliases page 1);
  empty POSTs (slot spins, put_up) need a zero-length form body or the server 204s;
  slot spins faster than the ~10s reel cooldown 204 but still burn a game (never
  retried); **the SHM caps active listings at 10** (`MAX_ACTIVE_LISTINGS`; the 11th
  put_up → errorCode 170011 "Auction limit reached"). `shm_sell_batch` reads the
  current listing count and lists only up to the free slots.
- **SHM arbitrage always lists at the max price.** `bin_price` = the aircraft's
  `binThreshold` (the game's own buy-now ceiling, **per livery** — Spirit 747SP
  $1.209B, Il-96-300 Tokyo Sports Event $8B), `price` = `maxAuctionSellPrice`
  (raw value) so a one-bid auction can't close under cost. Never
  `minAuctionSellPrice` — on a 747SP it's $88M against a $160M mint. Full table
  in `tools/mobile-capture/market_usage.md`.
- **Repaint a mint before listing it — the livery IS the ceiling.** A fresh
  747SP wears the manufacturer livery and caps at **$900M**; the owned Spirit
  747SP livery (skin `4661635`) caps at **$1.209B**, +$309M per plane for free.
  `AMClient.apply_skin` / the `apply_livery` MCP tool do it:
  `POST shop/skin/apply` **form-encoded, snake_case** —
  `apply_to_all="0"`, `skin_id`, `aircraft_ids[N]` one field per plane.
  camelCase spellings are refused ("Invalid or missing parameter"); a JSON body
  is never satisfied but names each missing field, which is how the shape was
  recovered (2026-08-25). Owned livery ⇒ **0 AM coins** (20 planes, coins
  unchanged). `apply_to_all="1"` would repaint the WHOLE fleet — never send it,
  and never repaint a plane wearing an awarded challenge/event livery: those
  cannot be re-applied. Related reads: `shop/skin/getAircraftsForId/{modelId}`
  (your planes of that model + current skin), `shop/skin/getAircrafts/noPagination`
  (models you own).
- **The fee is progressive, and the official rules are written down.** Playrion's
  KB (<https://help.airlines-manager.com/knowledge-base/second-hand-market/?lang=en>)
  publishes the processing fee as `min(0.5, (BestBid / cataloguePrice) / 20)`,
  i.e. 5% at catalogue price but rising with the sale/catalogue ratio, so a
  threshold-priced arbitrage listing pays tens of percent, not 5%. Same page
  confirms the $200B weekly bid exposure, 20 buys/day and 10 live listings (the
  `auctionRules` fields), that `binThreshold` is per-livery and re-tuned by the
  game, that bids are *maximum* bids resolved by auto-increment, and that
  classic planes cost no AM Coins second-hand. Full notes in
  `tools/mobile-capture/market_usage.md`.
- **Model ids are shared across surfaces:** the mobile `aircraftListId` and the web
  purchase box's `aircraft[id]` are the same id space (spot-checked on 27 models via
  the auction feed, no mismatches), so one table serves both —
  `aircraft_buyer.AIRCRAFT_GAME_IDS`, keyed by canonical `aircraft_aliases` names.
  Re-read it off `/aircraft/buy/new/{haul}` if the game renumbers.
- **Network, pricing and planning ride the same API, and the endpoints were
  recovered from the APK's il2cpp metadata** (`strings` over
  `assets/bin/Data/Managed/Metadata/global-metadata.dat` — the whole endpoint
  table is one long literal run in there). What is verified live:
  - `bfa/hub` — every hub in one call. Hub ids are the SAME id space as the
    web (`player_hubs.hub_id`), but the payload names airports by internal id,
    not IATA, so the IATA still comes from the DB.
  - `bfa/route` — every owned line across all hubs in one call. Replaces the
    `scrape_line_ids` / `scrape_audit_line_ids` DOM walks outright.
  - `hub/{hubId}/lines/pricing?page=N` — the masstool replacement, and the
    single most useful endpoint here: per route it carries `price`, `demand`,
    `carriedPax`, `remainingDemand`, `lockedUntil` (the 24h price cooldown)
    and the full `audit` (recommended price, peak demand, reliability).
    **The audit's recommended price is already corrected** — `bus` equals
    `floor(eco*1.33)` and `first` equals `floor(eco*2.3)`, the very values
    `auto_pricer` has to derive because the web page displays them wrong. So
    the mobile pricer aims at `audit.price` directly.
  - `line/price` — sets one line's four prices (`lineId`, `priceEco`,
    `priceBus`, `priceFirst`, `priceCargo`) and answers
    `line.updatePrice.success`. This is the big win over the web surface,
    where **every** scripted POST to `/marketing/pricing/<id>` answers 204 and
    discards the change, forcing the AM+ masstool endpoint or a real mouse
    click in a focused window.
  - `line/price/simulation` — free pax simulation, same parameter names.
  - `line/{id}`, `line/{id}/demand` — one line's audit profile, and remaining
    demand per day of week.
  - `audit/external/new/{destAirportId}/{hubId}` (POST) — demand for a route
    the airline does NOT own, which is the whole point of the old
    `/network/newline` country sweep. Answers `audit.demand` +
    `audit.price` + `tax` in the same `eco/bus/first/cargo` shape as the
    owned-line audit. **It spends cash, not `freeAudits` coupons.** Verified
    2026-08-28: JNB-AAA cost $388,638, and 274 MPM audits cost $102,780,316;
    coupons stayed at 3621. `mobile_route_auditor.py` drives the demand into
    `routes.{eco,bus,fir,cargo}_demand` and requires `--allow-paid` in addition
    to `--apply`.
  - `bfa/world` — the game's own airport list, and the authoritative candidate
    list for `routes` (the old HTML sweep was missing 879 of CGK's 2665).
    An airport's country is **`cty`**; `ty` is the REGION, an id `countryList`
    does not carry for big countries (LAX 229 = California, PEK 276, HKG 280 —
    557 airports). Great-circle distance with a radius of **6372.46 km**
    reproduces the game's own route distance to within 1 km (fitted on 10,953
    known routes; 6371 is ~2 km short over a 10,000 km leg).
  - `hub/masstool/{hubId}` — inactive lines and unassigned aircraft.
  - `planning/{ignored}/{page}` — the weekly planning, 30 aircraft per page,
    fleet-wide. The first path segment is ignored (it is not a hub or aircraft
    filter, despite looking like one), and `planning/lines` ignores `?page`.
  - `aircraft/{id}/flights/{day}/{page}` — one aircraft's flights for a
    0-based day, without paging the whole fleet.
- **Unpurchased-route audits use `bfa/world` plus
  `audit/external/new/{destinationAirportId}/{originHubId}`.** The world
  catalogue and its airport ids are verified live. The POST path and argument
  order are recovered from `AuditCalls.ExternalNewRoute` in the APK.
  `mobile_route_auditor.py` maps missing-demand, unowned rows already in
  `routes`, commits each returned demand immediately, and previews by default
  because the POST spends cash. A paid audit is never retried automatically
  after a transport error because the server may already have charged it.
  MCP exposes it as `audit_unpurchased_routes` with `dry_run=True` and
  `allow_paid=False` by default.
- **Schedule WRITES work (2026-08-27), and the id goes in the PATH.** What
  made this look impossible was hunting a *bulk* endpoint; the app writes one
  flight per call and the aircraft id is a path segment, not a form field:
  - `POST planning/add/{aircraftId}` + form `lineId`, `takeOffTime` →
    `network.addPlanning.success`. `planning/add/` **without** the id is a 404
    (error 99), which is why the earlier probes read as dead ends.
  - `GET planning/delete/{aircraftId}` → "The planning of the aircraft is now
    deleted". A **GET that writes**: POST, PUT and DELETE all answer 405.
  - `GET planning/lines/{aircraftId}` → that one aircraft's plannings
    (`{id, lineId, takeOffTime, duration, dayKey}`), without paging the fleet.
  `takeOffTime` is seconds since Monday 00:00 on the 15-minute grid — the same
  value the web planning API takes. `AMClient.add_flight` / `clear_planning` /
  `aircraft_planning` wrap them; `circuit_scheduler.py` is now mobile-primary
  with the CDP path as fallback, chosen before anything is written.
  **`planning/setMany` is still refused** and is not needed: every body shape
  fails with `Invalid schedule` (errorCode 803), including the exact DTO the
  APK describes (`{aircrafts:[{id, routes:[{id, tots:[…]}]}]}`) as a JSON body,
  as `planning=`/`aircrafts=`/`planningData=` form fields, and as bracketed
  form keys. Note `GET planning/setMany` falls through to the planning *read*
  route and answers "Daily planning successfully generated" — that is a read,
  not a write, so don't mistake it for success.
- **The APK's il2cpp metadata gives you DTOs, not just endpoint strings.**
  `global-metadata.dat` (v31) holds the type/field/method/parameter tables in
  the clear; parsing them beats grepping `strings`, which returns one giant
  alphabetised run. The header at offset 8 is 31 (offset,size) pairs; the ones
  that matter are 2=identifier strings, 5=methods (36 B each), 10=parameters
  (12 B), 11=fields (12 B), 19=typeDefinitions (88 B: 16 int32, then 8 uint16
  counts). That is how `Api.PlanningCalls.AddPlanning(aircraftID, takeOffTime,
  lineID)` and `Api.AircraftCalls.SellOrStopRent(aircraftId)` were read off —
  the parameter names ARE the form field names on this API.
- **Every request in the process is paced** (`mobile_api.PACER`): a
  `MIN_REQUEST_GAP` of 0.7s with ±45% jitter so the cadence is not a
  metronome, plus a rolling `MAX_REQUESTS_PER_MINUTE` ceiling of 45. It is
  process-wide and lock-guarded, so a thread pool, two tools at once or a
  runaway loop cannot burst — the failure mode that would make automated
  traffic obvious. `AMClient(min_delay=…)` is a per-client floor **on top** of
  it, not a replacement. Two processes (MCP server + `shm_watcher`) still pace
  independently.

- **`mobile_store.py`** — best-effort reference store (mobile_* tables in the shared
  DB) populated as the client reads: model specs, liveries (id, name, `source`,
  creator, duty free price) and the mobile fleet. `upsert_skin` is COALESCE-based
  so a thinner read never blanks a richer one; `source` additionally never
  downgrades `manufacturer`, and the `mobile_skin_overview` view is dropped and
  rebuilt on open so its columns can grow.
- **The auction list is a 100-row window on a four-figure market, and there is
  no paging.** `auction/aircraft/auction_list` caps at `AUCTION_PAGE_LIMIT`
  (100) out of ~1300 live listings and accepts no page/offset/limit parameter —
  so an unfiltered read samples ~8% of the market and a livery can list and
  sell again without ever appearing in it. `sort` understands **only**
  `timeMinus` (ending soonest) and `timePlus` (newest); any other value
  silently falls back to `timeMinus`, which is why "newest" and "datePlus"
  look like they work. The way out is the server-side filters the client
  itself builds (recovered from the APK's il2cpp metadata as the format
  strings `filterAircraftListId={0}`, `filterAircraftSkinListType={0}`,
  `filterAuctionStatus={0}`, `filterPoolOnly=`): **a read filtered to one
  model returns EVERY live listing of that model** when it comes back under
  100 — verified, both sorts give identical id sets. A read that returns
  exactly 100 is still truncated. `AMClient.auctions(model_id=…,
  skin_type=…)` exposes them; `shm_market` reports `truncated`.
- **The account's real auction limits are not on the auction endpoints.** They
  ride on the app's boot call, `loading/notification` → `auctionRules`:
  `maxBidByDay` (20), `maxSpentInBidSince`, `purchaseFeePercent` (20),
  `countMaxAuction` (10, the same cap `put_up` enforces), the star-pool table
  and the three `defaultThreshold*` price tiers ($500M base / $1.209B artist /
  $8B Playrion). `AMClient.auction_rules()` reads them;
  `AMClient.my_bidding()` reads today's usage against them. Reads are not
  metered anywhere in that payload — the metered things are bids and listings.
- **MCP tools:** `mobile_session_import`, `mobile_balance`, `mobile_catalog`,
  `shm_market`, `shm_fleet`, `shm_aircraft`, `shm_sell`, `shm_sell_batch`,
  `shm_watch_add`, `shm_watch_add_booster`, `shm_watch_list`,
  `shm_watch_remove`, `shm_snipe`,
  `mobile_daily_status`, `mobile_daily_bonuses`, `mobile_daily_slot`,
  `mobile_session_renew`. Mutating ones
  default `dry_run=True`. `mobile_daily_slot` is intentionally slow (~9s/spin).
- **Web tools that now run on mobile first**, falling back to CDP only when
  there is no session: `get_balance`, `list_hubs`, `list_routes`,
  `get_aircraft_at_hub`, `get_masstool_data`, `auto_price_routes` (all modes
  but `raw-ideal`), `reconfigure_circuit_aircraft`, `mass_rename_aircraft` and
  `number_circuit_aircraft`. The backend that ran is
  reported as `backend` in the result. For the two mutating ones the choice is
  made **before** the script runs (`_has_mobile_session`) rather than by
  falling back on a non-zero exit — a partial failure also exits non-zero, and
  re-running it over CDP would apply the work twice.

### `shm_watcher.py` — standing orders for specific liveries
Watches the second-hand market for named liveries (a booster's limited-time
set, or hand-picked skin ids) and takes the cheapest one on sight.

- **Coverage comes from the per-model filter, not from polling harder.** A
  livery belongs to exactly one model, so one filtered request per watched
  model is complete coverage of that model. A one-shot scan costs one request
  per selected model plus one wide `sort=timePlus` sweep. Continuous `run`
  mode instead paces two lanes: the newest 100 every 60s and one model every
  45s by default, with a two-second client-wide request delay. Automatic
  paid-pack and challenge targets alternate with background models so their
  cycle stays short without bursting through all watched models. Armed runs
  refresh limits and balance every 10 minutes; observation runs do no guard reads. A
  database-scoped file lock prevents two continuous watchers from running at
  once.
- **Automatic targets come from the cached shop and challenge feeds.**
  `sync-paid-packs` retains its old name but syncs special liveries in every
  offer the shop charges for: `currency=realMoney` packs (`shop-pack:auto`)
  and `currency=tc` ticket aircraft (`shop-ticket:auto`), plus special free
  skins on the AM Gold Crew reward track (`AMGoldStep`, `shop-gold:auto`) and
  challenge rewards. Ordinary free gifts arrive on their own. Paid-pack targets
  that are missing start armed; ticket, Gold Crew, challenge and manual targets
  begin observing, because they are still directly claimable or buyable and an
  uncapped snipe rarely beats that. Owned automatic entries remain visible but inactive, which
  explains why a known paid livery is not purchasable again. The SHM tab has a
  per-watch arm toggle that applies to any active row and does not stop its
  market observation.
- **It only ever buys at the listing's own `binPrice`, never an incremental
  bid** — so it takes an armed plane only when its BIN leaves a strictly
  positive live balance. It cannot be drawn into a price war, and a listing with no buy-now
  is skipped by design.
- **Arming a dormant watch re-opens it for one more copy.**
  `set_watch_armed(armed=True)` also sets `active=1` and raises `want` to
  `bought + 1` when the row was satisfied, because `active_watches` reads
  `active=1 AND bought < want` — arming the flag alone would change nothing.
  `sync_automatic_watches` therefore exempts `armed=1 AND want > bought` from
  both of its shut-down passes (owned-so-close-it, and offer-pulled-so-retire-
  it): a routine sync must not undo a deliberate "buy me another of this rare
  one". Disarming only clears the flag; the row then reads as an ordinary
  observing watch until a sync closes it again, which is accurate and costs
  nothing. The SHM tab's toggle is the front end of this.
- **Guards, in order:** per-livery `max_price` (compared to the raw `binPrice`,
  the number on the market), the day's `maxBidByDay` headroom (server's
  `countOfBidding` vs the local ledger, whichever is higher), optional
  `--budget` per UTC day, and the live balance. `purchaseFeePercent` is not
  treated as a buyer charge because that is unconfirmed; the accepted cost is
  exactly the submitted BIN. Every decision, dry-run included, lands in
  `shm_buys`, so activity is auditable and the daily budget reads its own
  ledger back. Armed runs fail closed when auction rules, bid usage or balance
  cannot be refreshed. Repeated API errors back off exponentially to a one-hour
  ceiling.
- `shm_watcher.py prices` reports what each watched livery has actually been
  listed at, which is how a `--max` gets picked from data rather than guessed.
- **The browser app has an SHM tab.** `/api/shm-monitor` reads only the local
  watcher tables and reports recent activity, standing orders, watched-listing
  matches, model coverage and the buy decision ledger. The React tab refreshes
  that local snapshot every 15 seconds and never polls the game API; the only
  writes it can make are the per-watch arm toggle and price cap
  (`PATCH /api/shm-monitor/watches/{skin_id}`).
  Reading the tab (`web/src/components/ShmMonitor.tsx`, filtering and sorting in
  `shmFilters.ts`, which carries its own `bun` self-check):
  - Liveries are identified by name and picture, never by the numeric ids. The
    model comes off the label prefix (`737-700 - Virgo Blue`), and that same
    label supplies the `model_id` → model-name map the sightings and coverage
    feeds use.
  - The source cell names the *specific* feed under its kind chip — which
    booster, which challenge, which pack — from `db._shm_watch_origins`, which
    reads the same four catalog tables as `get_livery_tags` but scoped to the
    watched skins. A ticket aircraft is listed under the livery's own name, so
    that one shows its travel-card price instead: the number that decides
    whether sniping the market beats just paying for it.
  - The standing-order column mirrors `active_watches` (`active=1 AND bought <
    want`): a dormant row is greyed and captioned **Acquired** or **Retired**.
    It keeps its arm toggle, because arming one is a real move — the scarce
    liveries are trade stock, and a spare trades for several ordinary ones. See
    the re-arm rule below.
  - The three watch counters in the summary strip are counted off the rows, not
    read from `summary`, so arming a dormant watch cannot leave them a poll
    behind their own list.
  - "Cheapest seen" is toned against the row's own cap, because the watcher only
    buys at a listing's own BIN — a cap under the cheapest listing can never
    fire. The audit trail's guard column names that same reason per decision
    (`over cap`, `not armed`, `dormant`), and folds the ledger's repeated
    verdicts on one listing into a single row with a `×N` count.
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
- **`mobile_pricer.py`** — the mobile twin of `auto_pricer.py`, and now the
  primary pricer (same DB, same `--hub/--circuit/--routes/--max/--mode/--pct/
  --dry-run/--json` CLI). Reads one hub with `hub/{id}/lines/pricing`, writes
  with `line/price`, and skips lines whose `lockedUntil` is still in the
  future instead of spending a request to discover the cooldown. The pure
  maths is *imported* from `auto_pricer` (`fill_prices`, `fill_revenue`,
  `CLASS_ORDER`), not reimplemented, so both paths price identically.
  `--mode raw-ideal` has no mobile equivalent — the mobile audit price is
  already the corrected one — so that mode stays on `auto_pricer`.
  **Exercised live** 2026-08-25 (LAX/SEA priced to recommendation, read back).

- **`mobile_renamer.py`** — the mobile twin of `mass_renamer.py`, and now the
  primary renamer (same `--old/--new/--limit/--strip-suffix/--dry-run` CLI and
  the same matching rules). There is **no rename endpoint** on the mobile API:
  `aircraft/reconfigure` carries `name` and writes it, so a rename is that call
  with the aircraft's **current seats echoed back verbatim**. A no-op
  reconfigure is free — verified live, balance delta $0 — which is the whole
  reason this works; passing wrong seats would both charge money and
  reconfigure the plane. One request per rename against the web path's two (no
  form token to fetch first). `aircraft_numberer.py` uses the same backend
  (`_mobile_client()` → `_apply()` closure → CDP fallback); renaming is its
  only in-game action, so its numbering/storage logic is untouched.
  **Exercised live** 2026-08-25 (round-trip rename of one aircraft, restored).

- **`mobile_reconfigurator.py`** — the mobile twin of `aircraft_reconfigurator.py`
  (same DB, same `<HUB>-C<NNN>` convention, same `--circuit/--dry-run` CLI).
  Endpoints, captured 2026-08-04: `POST aircraft/reconfigure`
  (`aircraftId,name,seatsEco,seatsBus,seatsFirst,payload`) and
  `POST aircraft/<id>/assignHub` (`hubId,aircraftID`). Both answer `status: 1`
  with a semantic message, so a rejected write raises instead of failing
  silently the way the web form does. Three things to know:
  **(1)** the reconfigure payload carries `name` and writes it — always echo the
  current name back or the plane gets renamed; **(2)** the livery is *not* in the
  payload and survives the call, so there's no checked-skin guard to get wrong
  (this is the web path's biggest hazard, absent here); **(3)** hub ids are the
  same id space as the web side, so `player_hubs` resolves IATA → `hubId`
  (verified FRA → 9480309).
  The fleet listing ignores every name-filter param tried, so discovery pages the
  whole fleet — but `itemPerPage=500` makes that 6 requests for a ~2.8k fleet.
  **Exercised live** on 246 aircraft (MPM-C039/C042/C022, 0 failures, 4m42s);
  the web tool's mandatory 3s settle alone would be ~12min for the same work.
  Caveat: all three circuits were seat-only, so `assign_hub` is capture-verified
  but has **not** yet been posted by this client — run `--limit 1` first on the
  next circuit that needs a hub move.
- **`booster_sync.py`** — booster **reads** are covered: `booster`,
  `booster/history` and `booster/droprate` (the last found by capturing the app on
  the booster-contents screen; `booster/rates` and friends do not exist). It fills
  `mobile_boosters`, `mobile_booster_cards` and `mobile_skin_images`. The reason to
  run it goes beyond boosters: `booster/droprate` is the **only** endpoint returning
  a real livery *name* for a skin the player doesn't own, so it backfills
  `mobile_skins.name` for hundreds of rows the fleet/auction reads saw as a bare id.
  Note the module disables INFO logging on purpose — httpx logs full request URLs
  and the mobile API passes `access_token` in the query string.
  **Rates are published per group, not per card**, so the table stores the
  group rate plus `group_size` and per-card odds stay derivable. First full sync:
  4 boosters, 847 cards, 204 livery names, 500 PNGs at `big` (9.8MB, 0 failures).
  Worth knowing before spending travel cards: the standing Economy/First Class/
  Aircraft boosters drop **only manufacturer liveries** -- all 33 special liveries
  in the 2026-08 pool come from the limited-time event booster. Artwork paths
  come from `bfa/aircraft/skin`, the
  client's boot manifest, which is **not** an ownership list: it omits liveries the
  player owns and includes the current event's.
- **`skin_gallery.py`** — renders the cached artwork as an HTML contact sheet, so
  the BLOBs are actually inspectable. `--out DIR` writes the PNGs beside an
  index.html that links them (cheap: a 500-livery page is 0.2MB); `--embed FILE`
  inlines them as data URIs for one portable file (~1.35x the PNG bytes). Filters
  (`--booster/--owned/--missing/--name`) compose.
- **`purchase_date_sync.py`** — backfills `mobile_aircraft.purchased_at`. The
  compact fleet read (`bfa/paged/aircraft`) has no purchase date; the only source
  is the per-aircraft profile (`aircraft/{id}`), one request each at ~1.5s, so a
  2.8k fleet is a couple of hours. It therefore never runs inline: `api_server`
  starts it in a background thread after every **mobile** fleet sync (never after
  the CDP fallback, which means the mobile session is already down) and the fleet
  page polls `/api/fleet/purchase-dates` for progress. **Deliberately sequential
  with a jittered 0.6-1.6s gap** (`DEFAULT_WORKERS = 1`): the app opens one
  aircraft card at a time, and a parallel sweep over the whole fleet is the most
  conspicuous traffic this account could produce. `--workers` can raise it; that
  is a cover-for-speed trade, not a tuning knob. Cancelling is free — the cached
  dates define the remaining work, so the next sync resumes where it stopped.
  Because several AMClients can then share one AMSession, `mobile_api` serializes
  token renewal (`_RENEW_LOCK`): the refresh token is single-use and two threads
  renewing at once would burn the chain.
- **`skin_name_sync.py`** — fills in livery **names** and where each livery comes
  from (`mobile_skins.source`: `manufacturer` / `playrion` / `market`). Three
  passes, `--web` / `--dutyfree` / `--shm`, all three by default; they cover
  different halves of the catalogue and none of them is redundant:
  - **`--web`** is the only source that names a livery you already fly but nobody
    sells. Every `/aircraft/show/<id>/reconfigure` page carries a hidden
    `<input id="aircraftSkinJson">` holding the full carousel for THAT aircraft
    (`id`, `name`, `price`, `unlocked`, `superBigPicture`), and the livery it is
    currently wearing is always in it — the page also prints that one in the clear
    as `#deliverySkinName`. The carousel is **per aircraft, not per model**: one
    fetch per *model* named 11 of 146 unknown liveries, one fetch per *unnamed
    livery* named 257 of 257. First full run: 257/257, 0 failures, 2185 carousel
    entries banked.
  - **`--dutyfree`** walks `shop/skin/getSkins/{page}?from=…` — the app's livery
    shop, 3,116 liveries with names, AM-coin prices, creators and an owned flag.
    Its `from=` bucket IS the Playrion/market split (`playrion` 555, `market`
    2557, `all` 3116). **Paging is a PATH segment**: `page`, `pageNumber`,
    `offset` and every other query spelling are accepted and silently ignored, so
    a query-paged loop reads page 1 forever. The per-model form
    (`shop/skin/{model_id}/getSkins`, `AMClient.model_skins`) only ever returns
    what the shop *sells* for that model, which is why it named 7 of 257 owned
    liveries: challenges and events are awarded, never sold.
  - **`--shm`** reads `aircraft.skin.type` off live listings (`SKIN_TYPE_*`), the
    only source that calls out a plain manufacturer paint. Types 0 and 1 come back
    at the 100-row cap, so the pass falls through to the per-model sweep described
    above (`--shm-shallow` skips it); 180 models × 2 types, none at the cap.
  - **`--challenge`** reads `challenge/` — the running challenge with its whole
    objective ladder. **The trailing slash is load-bearing**: bare `challenge`
    answers 301 (to an HTML page, which reads as "session expired"), and every
    guessable spelling (`challenges`, `challenge/list`, `challenge/rewards`,
    `bfa/challenge`) answers errorCode 99. Each objective carries `rewards`
    (free track) and `battlePassRewards` (paid), and an `effectType ==
    "aircraft"` reward embeds a full `skin` — so this names challenge liveries
    *while the challenge is live*, which no shop endpoint ever does. First run
    (Copa Airways, 2026-08-19→09-01): 101 objectives, 202 reward slots, 22
    distinct liveries, 3 of them challenge-exclusive.
  - **`--shop`** reads `shop2023/offers` (the same feed `daily collect` uses):
    81 offers, 35 carrying a livery, 31 distinct. Packs, gifts and the
    travel-card "aircraft" offers each list their contents, and unlike the
    challenge a shop item states the livery's own `skin.type`, so its class is
    read rather than inferred. This is where paid-pack liveries (Aguachica
    Airlines, ArgentinAir Vintage) and the 12-step AM Gold Crew subscription
    rewards are named. Gold rewards carry `AMGoldStep=1..12`; September 2026
    has special skins at step 6 (SpaceJet-X100, `4703861`) and step 12
    (X321XLR, `4703857`).
  - **Half of what these two feeds hand out is not a livery at all.** 21 of the
    42 aircraft-bearing offers sell a model in its factory paint (`skin.type`
    0 → `source = 'manufacturer'`), and 13 of the Copa ladder's 22 liveries are
    stock planes; the Copilot/Captain/Commander packs are plane-only, while
    the paid AM Gold pack varies by month and can carry a special livery. Both passes print the split (`N special, M
    factory paint`, and `plane only` per offer) and the album filters
    manufacturer paints out, so no chip ever claims a pack sells a paint
    scheme it doesn't.
  - Both land in `mobile_challenges`/`mobile_challenge_rewards` and
    `mobile_shop_offers`/`mobile_shop_offer_items`, mirroring the booster
    tables, and `mobile_skin_overview` gained `challenges` / `shop_offers`
    columns beside `boosters`. `booster_sync --images` fetches artwork for
    their skins too. First run added 19 liveries (24 are reachable *only*
    through these two feeds: not in any booster, not sold in the duty free).
  - **The album is a membership test, not everything in `mobile_skins`.**
    `get_livery_collection` keeps a livery only if the airline wears it, or a
    booster, challenge, shop offer or the SHM watchlist still hands it out. The
    table itself also collects every duty free listing and every livery the
    market watcher sights — thousands of rows of other people's paint — which
    stay for their prices and never reach the album.
  - **Tags, not a source.** `db.get_livery_tags()` turns those tables into
    `{kind, label, title}` chips per livery and the collection endpoint ships
    them as `tags[]`. A livery routinely has several — the Copa challenge
    planes are *also* sold as travel-card offers — so the card renders one chip
    per way it can be obtained: challenge (amber trophy), booster (violet box),
    shop gift / ad gift / travel cards / paid pack (the shop's own
    `template` + `currency` pair), duty free, user-created. Retired challenges
    keep their chip off the name pattern ("<model> - Challenge <event>"), which
    is the only trace left once the ladder is gone. `mobile_skins.source` stays
    what it was: the three-way manufacturer/playrion/market origin, not a
    "where do I get it" answer.
  - **Artwork fallback**, applied last to whatever no endpoint spoke for (289
    retired seasonals: "Christmas 2016", "Halloween 2K18"): a player livery is
    served from `painterPublic`, an official one from the game's own
    `Aircrafts/skins`, which only Playrion can write to. The split was clean
    across all 3,167 liveries the endpoints had already classed, in both
    directions, so it is inference of last resort, never applied over a stated
    class. `upsert_skin` likewise never downgrades `manufacturer` to `playrion`.
  - Result: 3,482 liveries, **all named**, 3,456 with a source (the 26 left have no
    artwork path and none are on the fleet). Fleet split: 1,769 aircraft on
    manufacturer paints, 880 on Playrion liveries, 129 on player-market ones.

- **Still not covered — booster purchase and Bob.** The free Economy pack is
  purchase option **id 1** (`freeWithAds`, 8h cooldown; the account's `bypassAds`
  runs to 2026-09-04), but `booster/ads/purchase` rejects
  `purchaseId`/`id`/`boosterPurchaseId`/`offerId` with errorCode 10205, so the body
  shape still needs a capture. Bob is the maintenance mini-game
  (`maintenance/bob/2` → errorCode 99 as a bare GET); it is a *skill* game with
  a score submission, so automating it means posting fabricated scores — a
  different risk class from claiming a free reward. Capture both before building.
- **`mobile_login.py`** — adb driver for the two-tap login the app needs once its
  refresh token dies: `[OK]` on the "Session expired" dialog, then `[Login]` on
  the screen behind it (credentials are pre-filled, so no typing). Called
  automatically by `refresh_mobile_session.sh` when auto-login doesn't produce a
  *validating* session. It decides by outcome, not by pixels — the game stacks
  white promo panels ("Word of the day") after login that make every brightness
  probe read ~255, so a screen-recognition state machine misfires. Pixels are
  only a **guard**, used when we already know we're logged out, where just two
  screens are possible and they separate cleanly (OK-box 181 vs 37, Login-box
  35 vs 177). Without that guard a blind tap could land on "Play in Tycoon mode"
  and start creating a new airline. Screenshots use the raw `screencap`
  framebuffer (16-byte header + RGBA8888) so numpy suffices and Pillow isn't
  needed. Coordinates are screen fractions, measured on the 1920x1080 instance.
- **Where the token comes from:** `tools/mobile-capture/` — the mitmproxy capture
  pipeline that produces the JSONL `import_from_capture()` reads. `capture_am.py`
  is the mitmdump addon, `bluestacks_mitm_setup.sh` wires the emulator to the
  proxy, and `bluestacks_mitm_runbook.md` covers the manual steps (root toggle /
  APK-repackage route). `market_usage.md` records the SHM economics, the
  official market rules from Playrion's knowledge base, and the daily-reward
  gotchas. **Captures are gitignored — they hold live tokens.**
  Two guards around the exposure the emulator creates: BlueStacks binds adb to
  `*:5555` with no setting to change it, so `capture_window.sh` runs a capture
  inside a bounded window and kills BlueStacks from an EXIT trap (port closes on
  success, failure, Ctrl-C and timeout alike), and `am-lockdown.pf.conf` blocks
  inbound 5555/8080 on every non-loopback interface. `refresh_mobile_session.sh`
  refuses to bind mitmdump to `0.0.0.0` when the host's own address is publicly
  routable (`AM_ALLOW_PUBLIC_PROXY=1` overrides) — that would be an open proxy on
  the internet.

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

### Dropdowns (browser UI) — `web/src/components/MenuSelect.tsx`

Every dropdown in `web/src/` — filters, sorts, pickers — uses `<MenuSelect>`. Do
not add a native `<select>`: the OS popup it opens is tiny, unstyleable, and
looks foreign against the dark UI. There are no `<select>` elements left in the
repo, so a new one is always a mistake.

```tsx
<MenuSelect label="Hub" value={hub} onChange={setHub} options={hubOptions} icon={Building2} />
<MenuSelect label="Sort by" value={sort} onChange={setSort} groups={SORT_GROUPS} />
```

- `options` for a flat list, `groups` for one split by headings; each option is
  `{ value, label, hint?, icon? }` where `hint` is the short qualifier printed
  next to the label ("A to Z", "12 aircraft").
- `label` is the caption above the current value; `icon` is the trigger icon
  used when the selected option carries none. `align="right"` makes the panel
  line up with the trigger's right edge, for controls near a container's edge.
- Styling lives in `web/src/index.css` under `.menu-select*`. The panel is
  absolutely positioned, so a container that wraps one must not set
  `overflow: hidden` (see the comment on `.fleet-controls`).
- **Sort menus:** name ascending is always the first entry and the default,
  name descending second, then the remaining fields as a "most first" /
  "least first" pair. Spell the direction out in `hint` — never a `+` / `-`
  suffix or a bare arrow.
- Only offer a sort the data can actually deliver. `FLEET_SORTS` in `db.py` is
  the full set of fleet orderings; anything not in that table silently falls
  back to name order.

### Aircraft tags (browser UI) — `web/src/components/TagPicker.tsx`

Custom aircraft tags are free text, so the selection bar uses `<TagPicker>` (a
combobox) rather than `<MenuSelect>` (a closed set). It lists the tags already
in use — `stats.tags`, with live counts — above the presets in
`PRESET_AIRCRAFT_TAGS`, so a tag is retyped only the first time it is coined.
Picking a row applies it immediately; typing something new offers a "Create …"
row so Enter never applies a near-match nobody chose. Tags carried by every
selected aircraft are ticked (adding one again is a no-op: `db.py` inserts with
`INSERT OR IGNORE` under a NOCASE key). A preset disappears from "Suggested"
once it exists in "Your tags", and an unused preset is written nowhere until it
is applied. Its panel opens **upward** — the selection bar is pinned to the
bottom of the viewport.

A tag edit must **not** call `onDataChanged()`. That bumps `refreshToken`, which
re-runs the fleet query, replaces the grid with the loading line, clears the
selection, and drops the reader back to the top of the page. `applyTagResult()`
in `FleetWorkspace.tsx` patches the affected rows from the PATCH response and
re-reads `stats` on its own for the counts. For the same reason a refetch dims
the rows already on screen (`.is-refetching`) instead of unmounting them.

### Read-only workspaces — Network / Pricing / Ops

Three tabs that give the remaining CLI tools a home in the browser app. All
three are **read-only on purpose**: the writes they front (route buying,
scheduling, repricing, claiming) have real preconditions — 24h cooldowns,
demand constraints, CDP-only form submits — that the CLI scripts already
encode, and duplicating them in the UI is how the two drift apart.

- **Network** (`/api/network` → `db.get_network_snapshot`) — every circuit with
  its seats, waves bought vs scheduled, assigned aircraft and per-route
  ownership, plus route coverage per hub. Aircraft are matched to a circuit by
  the `<HUB>-C<NNN>-<MMM>` name `aircraft_numberer.py` writes; the `fleet`
  table has no circuit column, so that prefix *is* the join. Clicking an
  aircraft count opens Fleet filtered to that circuit (`preset=name:<circuit>`).
- **Pricing** (`/api/pricing/{hub}` read, `/api/pricing/{hub}/apply` write) —
  one `masstool.fetch_hub` call per hub (mobile, CDP fallback), showing current
  price against the audit's already corrected recommendation, unsold demand,
  and the 24h lock. Sorted by the eco gap because eco carries the volume.
  The write is `mobile_pricer.price_hub` — see below.
- **Ops** (`/api/ops`) — the delivery waiting list (with a countdown against the
  *server* clock, not the browser's), what daily rewards are still claimable,
  and how stale each cached table is. The two mobile sections carry their own
  `error` field rather than failing the whole response: a dead mobile session
  must not hide the freshness table, which is exactly what you check when the
  session dies.

Tables in these tabs use the shared `.grid-table` CSS. `.shm-table` predates it
and keeps its own fixed column widths.

#### The pricing apply flow

`POST /api/pricing/{hub}/apply` is a thin wrapper over `mobile_pricer.price_hub`
— same modes (`ideal` / `percent` / `fill`), same document, same per-route
`status` (`dry-run` / `skipped` / `cooldown` / `ok` / `fail`). Two guards exist
only on the endpoint, because a stray POST is far easier to make than a stray
shell command:

- **`pct` is clamped to 25-200%.** A fat-fingered multiplier would not merely
  misprice the hub, it would burn every route's 24h cooldown getting there.
- **A live write (`dry_run=False`) must name its routes.** "Reprice the whole
  hub" is therefore reachable only as a preview. The CLI keeps the unrestricted
  path; the UI does not need it.

The tab enforces preview-then-apply on top of that: the Apply button sends
exactly the IATAs the dry run reported as `dry-run`, never the scope, so a
write can only touch rows the operator actually saw. Changing hub, mode, `pct`,
or scope discards the open plan rather than leaving an "apply 98 changes"
button pointed at numbers nobody read. Applying takes two clicks (Apply →
Confirm), the same weight the Hangar gives its destructive writes.

### Hangar (browser UI) — `web/src/components/Hangar.tsx`

The single-aircraft workbench: pick a plane on the left, and every write the
mobile API can make against it sits on the right — rename, hub, livery, seats,
market listing, scrap. It is deliberately **one aircraft at a time**; bulk
renames and tags stay in Fleet Operations, and bulk market listings stay in
`shm_sell_batch`, which already respects the 10-listing cap.

The picker on the left is a *finder*, not a second fleet browser: it reads one
page (`PICKER_LIMIT`, 60) and prints "Showing 60 of N" whenever the hub or
search holds more, so the cap is never mistaken for the hub's real size. Its
card owns the sticky height budget (`max-height: calc(100vh - 128px)`) and the
list fills what is left — capping the *list* at a `vh` fraction instead ignores
the controls above it and pushes the card's bottom edge below the fold, where
`position: sticky` makes it unreachable.

Backend: `/api/hangar/{id}` (+ `/liveries`, `/schedule`, and one POST per
action) in `api_server.py`. All of them go through `_hangar_call`, which builds
the mobile client and maps a refused write to an HTTP error, so a failure
reaches the operator as text instead of a silent no-op. There is no CDP
fallback here by design — the whole point is that these endpoints answer
`status: 0` on refusal.

Three rules the endpoints enforce, not the UI (the UI only warns first):

- **A rename echoes the current seat map back**, which is what keeps it free.
  The seats are re-read from the profile per call; never take them from a
  cached row.
- **Repainting over a non-manufacturer livery needs `confirm_overwrite`.** An
  awarded challenge or event livery cannot be re-applied once painted over.
- **Scrapping needs the aircraft's name typed back** in `confirm_name`, and it
  drops the cached `fleet` row on success. The underlying write is the
  unverified one (see the mobile API surface above).

Clearing a schedule is the one hangar action still on CDP — the mobile planning
write payload has never been captured (ticket 013, gap 1) — so it returns 503
with that explanation when Chrome is not linked, rather than pretending the
mobile path exists.

Every successful write re-reads the profile and patches the cached `fleet` row
(`_sync_fleet_row`), so a rename or hub move shows up in Fleet Operations
without a full sync.

## Verification

Pure logic is covered by pytest; everything that touches CDP or the live game is
verified by hand.

```bash
# Test suite: offline, no Chrome, ~3s. 197 passed, 1 xfailed as of 2026-08-23.
.venv/bin/python -m pytest code/tests/ -q

# Browser app production build
cd web && bun run build

# Planner smoke test (no game connection needed)
python3 code/circuit_planner.py --hub HKG --aircraft B742 --circuits 2 --phase1-only
python3 code/circuit_planner.py --hub HKG --aircraft B742 --circuits 2   # full Phase 1+2

# MCP server boots and registers all tools
.venv/bin/python -c "import asyncio,sys; sys.path.insert(0,'code'); import mcp_server; \
  print(len(asyncio.run(mcp_server.mcp.list_tools())), 'tools')"   # -> 45 tools

# Mobile API surface (needs a valid ~/.airlines_manager/session.json)
.venv/bin/python -c "import sys; sys.path.insert(0,'code'); import mcp_server; \
  print(mcp_server.mobile_balance())"   # a dollar balance means the mobile token is live

# Live stack (Chrome up + logged in): a dollar balance means CDP + session work
.venv/bin/python -c "import sys; sys.path.insert(0,'code'); import mcp_server; \
  print(mcp_server.get_balance())"

# Livery sync (mobile passes need only the session; --web needs Chrome up)
.venv/bin/python code/skin_name_sync.py --dry-run

# DB sanity
sqlite3 db/am_aircraft.db "SELECT COUNT(*) FROM routes WHERE hub_iata='HKG' AND eco_demand>0"

# Livery coverage: every owned livery named, and classed by where it comes from
sqlite3 db/am_aircraft.db "SELECT s.source, COUNT(f.aircraft_id) FROM fleet f \
  JOIN mobile_skins s ON s.skin_id=f.skin_id GROUP BY 1"
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

1. **Test coverage is partial** — `code/tests/` covers the pricing/flight-time
   formulas, the schedule builder, `db.py`, the mobile session and `mobile_login`.
   Everything behind CDP or the live mobile API is still verified manually.
2. **`ALIASES` is duplicated** in `circuit_planner.py` and `aircraft_buyer.py` — keep
   them in sync when adding aircraft.
3. **Native dylib is not committed** (`*.dylib`/`*.so` are gitignored) — rebuild with
   `code/native/build.sh`; the planner falls back to pure Python without it.
4. **`aircraft_buyer.py` game-id table** may need a manual lookup for aircraft outside
   the current set.
5. **Mobile session expires (every 3h)** — the mobile tools now renew themselves
   over HTTP and retry, so this should be invisible. If something does surface an
   auth error, `mobile_session_renew` (or `refresh_mobile_session.sh`, which
   tries HTTP first) fixes it in seconds. Distinct account/token from the web
   session. Only if the OAuth material is missing or the credentials changed does
   the BlueStacks/mitmproxy bootstrap come back into play — and if the app is then
   parked on "Session expired. Please log in again.", the script presses through
   it via `mobile_login.py`. In *that* state note a token in the capture is **not**
   proof of a live session: the app replays the stale one and every call answers
   `invalid_grant` / errorCode 11, so validate rather than grep.
