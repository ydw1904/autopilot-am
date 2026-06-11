# Airlines Manager — Agent Control Plane

An **MCP server that lets an AI agent play a real browser game end-to-end** — plus the
optimization engine and browser-automation layer it drives.

[Airlines Manager](https://www.airlines-manager.com) is a long-horizon economic
strategy game: you buy routes, configure aircraft, schedule flights, and price
seats to maximize weekly revenue across multiple hubs. This repo turns that whole
loop into a set of agent tools. An agent (Claude Code, or anything that speaks MCP)
can reason in natural language — *"find the best 5 circuits out of HKG, buy the
routes, schedule the planes, price every seat"* — and the server executes it against
a live, logged-in game session.

The interesting engineering is not the game. It's the **harness**: 24 typed tools
over a real, hostile web app (CSRF tokens, jQuery handlers, silent server-side
rejections), with safety boundaries baked in so an autonomous agent can run the
loop without breaking things.

```
   Agent (natural language)
        │  MCP / JSON-RPC over stdio
        ▼
   mcp_server.py ──┬── live game I/O ──► cdp.py ──► Chrome (CDP WebSocket) ──► airlines-manager.com
                   └── planning/math ──► circuit_planner.py ──► beam_search (native C++) + db.py (SQLite)
```

---

## Why this exists / design highlights

- **Tool use as the interface.** Every game capability is a single MCP tool with a
  typed signature and a structured `dict` return. The agent never sees CDP, the DOM,
  or a CSRF token — each tool encapsulates the game's quirks (`mcp_server.py:11`).
- **Safety boundaries for autonomy.** Every *mutating* tool defaults to
  `dry_run=True`. Purchases honor a `--min-balance` floor. The agent has to opt into
  side effects, so a planning conversation can't accidentally spend in-game money.
- **Reliability over a hostile target.** The game silently rejects `fetch()`-based
  purchase POSTs — they return `200 OK` without buying. The buy path was rewritten to
  drive the real country-listing form with native `form.submit()` so success means
  success. That kind of "make the tool *actually* reliable, not just look successful"
  work is the point (see `MCP_SETUP.md`).
- **Session reuse, not re-auth.** The server attaches to an already-logged-in Chrome
  tab over the DevTools Protocol and reuses its cookies — no credential handling, no
  headless login flow.
- **A real optimizer underneath.** Route/seat/wave selection is a genuine constrained
  optimization problem (mixed-integer, coupled seat configs across a circuit). The
  planner uses beam search with a hot path implemented in **native C++** behind a
  ctypes wrapper. See [Optimization engine](#optimization-engine).

This is a personal agent I actually use to run the game as a production task. It started as a CLI optimizer and grew a GUI and then an MCP server as
the workflow got more autonomous (see `CHANGELOG.md` / git history).

---

## Quickstart (agent / MCP)

Full setup — venv, Chrome debug flags, agent registration, smoke test — is in
**[`MCP_SETUP.md`](MCP_SETUP.md)**. The short version:

```bash
# 1. Install deps into the project venv
python3 -m venv .venv
.venv/bin/pip install -r code/requirements.txt

# 2. Launch Chrome with remote debugging and log into the game (once)
code/launch_chrome.sh

# 3. Verify the server boots and registers its tools
.venv/bin/python -c "import asyncio,sys; sys.path.insert(0,'code'); import mcp_server; \
  print(len(asyncio.run(mcp_server.mcp.list_tools())), 'tools')"
# -> 24 tools
```

`.mcp.json` at the repo root already declares the server for Claude Code. Once Chrome
is up and logged in, an agent can call the tools directly:

> "What's my balance? Plan 3 circuits out of HKG with B742, then dry-run buying the routes."

### The 24 tools

Mutating tools default to `dry_run=True`.

| Group | Tools |
|---|---|
| **Read / live state** | `get_balance`, `list_hubs`, `list_routes`, `get_aircraft_at_hub`, `list_aircraft_for_sale`, `get_page_text`, `navigate_to` |
| **Direct game actions (CDP)** | `buy_route`, `schedule_flight` |
| **Planning & bulk ops** | `plan_circuits`, `buy_circuit_routes`, `buy_aircraft`, `schedule_circuits`, `auto_price_routes`, `number_circuit_aircraft`, `reconfigure_circuit_aircraft`, `rename_circuit`, `mass_rename_aircraft`, `mass_unschedule_aircraft` |
| **Data sync / scraping** | `refresh_internal_audits`, `sync_warehouse`, `get_masstool_data`, `scrape_line_ids`, `scrape_audit_line_ids` |

Each of these is also a standalone CLI script under `code/` — the MCP server is a thin,
typed layer over the same code paths, so everything is runnable and testable without an
agent in the loop.

---

## Repository layout

```
airlines-manager/
├── README.md            ← this file
├── MCP_SETUP.md         ← MCP server setup + smoke test
├── AGENTS.md            ← guide for AI agents working ON this codebase
├── CHANGELOG.md         ← project evolution
├── .mcp.json            ← Claude Code MCP registration
└── code/
    ├── mcp_server.py            ← MCP server: 24 tools (the control plane)
    ├── cdp.py                   ← shared Chrome DevTools Protocol layer
    ├── db.py                    ← shared SQLite access layer
    ├── circuit_planner.py       ← primary optimizer (Phase 1 + Phase 2)
    ├── circuit_planner_native.py← ctypes wrapper for the C++ beam search
    ├── native/beam_search.cpp   ← native beam search (build.sh → .dylib/.so)
    ├── circuit_route_buyer.py   ← route purchaser (CDP, country-listing flow)
    ├── aircraft_buyer.py        ← aircraft purchaser (CDP)
    ├── circuit_scheduler.py     ← flight scheduler
    ├── auto_pricer.py           ← per-route seat pricing
    ├── aircraft_numberer.py / aircraft_reconfigurator.py / mass_*.py  ← fleet ops
    ├── warehouse_sync.py / masstool.py / scrape_*.py  ← data sync
    └── gui_app.py + gui/        ← NiceGUI desktop control panel
```

The scraped game database (`db/*.db`) and generated `data/` are **not** committed —
they're local state. The repo ships the code that produces and consumes them.

---

## Optimization engine

The agent-facing layer is the headline, but the planner underneath is a real
constrained-optimization problem. Two phases:

- **Phase 1 — circuit selection.** Beam search over route combinations to find sets of
  routes ("circuits") that maximize weekly demand captured within a 168-hour game week,
  subject to aircraft range/category and a demand-balance filter. Hot path is native C++.
- **Phase 2 — seat config + revenue.** Grid search over the seat split
  (eco/bus/first/cargo) and wave count to maximize weekly revenue, accounting for the
  game's undersupply pricing bonus and the fact that **seat config is shared across all
  routes in a circuit** (one low-demand class on one route caps that class everywhere).

```bash
python3 code/circuit_planner.py --hub HKG --aircraft B742 B743 --circuits 5 \
  --owned-hubs FRA CGK PEK JNB --comfort 500 --speed 700
```

| Arg | Default | Description |
|-----|---------|-------------|
| `--hub` | HKG | Departure hub IATA |
| `--aircraft` | *required* | Aircraft aliases to compare (space-separated) |
| `--circuits` | 10 | Number of sequential circuits to find |
| `--owned-hubs` | — | Destination hubs to exclude |
| `--beam` | 1200 | Beam width (higher = more thorough, slower) |
| `--max-routes` | 150 | Pre-filter to top-N routes by demand |
| `--comfort` | 500 | Pax pricing comfort factor |
| `--speed` | 700 | Cargo speed factor |
| `--max-waves` | 30 | Cap on waves per circuit |
| `--match` | 0.9 | Min demand-balance ratio within a circuit |
| `--phase1-only` | — | Demand summary only, skip seat optimization |
| `--save` | — | Persist circuits to the local SQLite DB |

<details>
<summary><strong>Mathematical model</strong> (capacity, pricing, constraints, MIP formulation)</summary>

### Core capacity model

Weekly capacity per route per class:
```
capacity(class) = 2 × seats(class) × waves × 7
```
`2` = round-trip/day, `waves` = number of 7-plane waves (one flight/day/route).

### Pricing

Ideal price (used when audit data unavailable):
```
eco_price   = FLOOR(100 + distance × 0.3 × (1 + comfort/3000))
bus_price   = FLOOR(eco_price × 1.33)
fir_price   = FLOOR(eco_price × 2.3)
cargo_price = CEILING(200 + P × distance × (1 + speed/500))   # P by distance band
```

Undersupply bonus (capacity < demand pushes price up ~33% as capacity → 0):
```
supersim_price = audit_price                              if capacity ≥ demand
               = FLOOR(audit_price × (1 − (cap−dem)/(3·dem)))  otherwise
```

Daily turnover per route/class:
```
daily_turnover = MIN(capacity, demand) × supersim_price(...)
weekly = daily × 7
```

### Constraints

```
Payload:   0.1·e + 0.125·b + 0.15·f + 1.0·c ≤ max_tonnage
Seats:     1.0·e + 1.8·b + 4.2·f           ≤ max_pax
Demand:    2·seats(class)·waves            ≤ demand(route, class)   ∀ route, class
Time:      Σ flight_time(distance, speed)  ≤ 168 hours
Hub-excl:  x_r = 0 for routes destined to an owned hub
```
where `flight_time = CEILING((distance/speed + 1)·2, 0.25)` hours (round-trip +
turnaround, rounded to 15 min).

### MIP formulation

```
variables:  x_r ∈ {0,1} (route in circuit),  w ≥ 1 (waves),  e,b,f,c ≥ 0 (seats)
maximize:   Σ_{r∈circuit} Σ_class  MIN(2·seats·w, demand) × supersim_price
subject to: payload, seat, demand (⇒ w ≤ MIN_{r,class} FLOOR(demand/(2·seats))),
            circuit-time, and hub-exclusion constraints above
```

</details>

---

## Data model

A local SQLite DB (scraped from the game, not committed) holds:

- `aircraft(model, category, speed_kmh, range_km, max_pax, max_tonnage, gross_price, …)`
- `routes(hub_iata, dest_iata, distance_km, category, {eco,bus,fir,cargo}_demand, audit_price_*, …)`
- `hubs(hub_id, iata, name, country_code, category, …)`

Populated by the `scrape_*` / `warehouse_sync` / audit tools against a live session.

---

## Dependencies

Python 3.10+. Pinned in [`code/requirements.txt`](code/requirements.txt):
`mcp`, `httpx`, `websocket-client`, `numpy`, `colorama`, and `nicegui` (GUI only).
The native beam search needs a C++ compiler (`code/native/build.sh`).

---

## Notes

- **Game time** is the game-week (168 h), not real time.
- **Audit prices** (scraped from the game) give accurate revenue; the ideal-price
  formulas are ~±10% estimates used as a fallback.
- **Hub IDs** (numeric, e.g. `10087991`) differ from IATA codes (`HKG`); find the
  numeric ID in the game URL.
- See [`AGENTS.md`](AGENTS.md) for how AI agents should work *on* this codebase.

---

## License

[MIT](LICENSE).
