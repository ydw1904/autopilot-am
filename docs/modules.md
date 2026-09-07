# Module Reference (high-traffic modules)

## `circuit_planner.py` — primary optimizer
Two phases. **Phase 1** (`search_circuits`): beam search over route combinations,
each scored by `quick_revenue_estimate()`; filtered by a demand-balance ratio
(`--match`). The hot loop is delegated to
`circuit_planner_native.search_circuits_native` when the Rust dylib is built. **Phase 2**
(`optimize_circuit`): native coarse + fine grid search over `(eco,bus,fir,cargo)`
seats and wave count, using SuperSim pricing. Pricing helpers (`ideal_eco/bus/fir/cargo`,
`supersim_price`, `daily_turnover`) and the `ALIASES` map live here.

## `circuit_planner_native.py` + `native/beam_search.rs`
Dependency-free Rust `cdylib` behind a plain C ABI. Phase 1 keeps the
`search_circuits_native` signature and return shape
`[(score, total_time, [route_idx, …]), …]`; Phase 2 returns the best seat config,
wave count and revenue while Python builds the public per-route breakdown. Build
with `code/native/build.sh`. The exact-output tests compare serialized bytes against
the legacy Phase 1 and Python Phase 2 oracles.

## `circuit_route_buyer.py` — route purchaser (CDP)
Default flow drives the game's country-listing page and submits the real form with
native `form.submit()`. The game silently rejects `fetch()`-based purchase POSTs
(returns `200 OK` without buying), so do **not** "simplify" this back to `fetch()`.
Can take IATAs directly or `--circuit NAME` (loads routes from DB).

## `aircraft_buyer.py` — aircraft purchaser (CDP)
Find aircraft on the list page → click Buy (AJAX configure form) → set hub / seat
config / quantity → submit via jQuery trigger. Reads circuit config from DB; has a
game-id lookup for aircraft models. Also exposes `get_balance()`, reused by the MCP
server.

## `route_details.py` — one route's details page (CDP-only)
`/network/showline/{line_id}`, parsed. This exists because the mobile API has
**no** per-line financials: `line/{id}/statistics|stats|finance|accounting|
turnover|aircraft|planning` all answer "error 99" (probed 2026-09-04), so
airport taxes, accepted categories, flights per week, the Today/Yesterday
statistics, the six-day history, the D+5 forecast, the 7-day financial summary
and the aircraft actually scheduled on the line have exactly one source.

`parse_showline(html)` is pure and covered offline by
`code/tests/fixtures/showline_fra_pvg.html`. Two traps that the tests pin, both
the same shape — **one table, two datasets**:
- Today's and yesterday's per-class rows are two `<tbody>`s of the *same*
  `<table id="resultTable">`, so a parser that stops at `</table>` reads
  yesterday's numbers over today's.
- The six-day history and the D+5 forecast share one table and both have a
  `Turnover :` row, so the rows have to be split at the `Forecast` header or
  the forecast silently replaces the history.

Also note `alt="In flight"` lives inside an `<img>` tag, so it is gone by the
time the box is turned into text — check the raw HTML for it, not the text.

## `auto_pricer.py` — corrected ideal-price setter
The game's displayed "Ideal price" is correct for **eco** and **cargo** but **wrong**
for business/first. Always derive: `bus = floor(eco × 1.33)`, `fir = floor(eco × 2.3)`.
Modes: `ideal` (corrected, default), `percent --pct N`, `raw-ideal` (uncorrected).

## `circuit_scheduler.py` — flight scheduler
Reads circuit config from DB, resolves game ids for aircraft + routes, submits
schedules. Mobile-primary: `_mobile_hub_view()` reads the hub's aircraft and
lines in two requests (`hub/masstool/{id}` + the fleet listing) and
`clear_schedule` / `submit_flights` take either an `AMClient` or a CDP handle —
so with a mobile session on disk the whole run needs no browser. Without one it
falls back to the planning page and the `/network/planning/0/ajax` POST. On the
mobile path `util` is only 0 or 100 (idle vs flying), which is all `--only-new`
needs. Exposes `get_lines_at_hub()` (reused by MCP).

## Fleet / data-sync scripts
`aircraft_numberer`, `aircraft_reconfigurator`, `circuit_renamer`, `mass_renamer`,
`mass_unscheduler`, `warehouse_sync`, `masstool`, `scrape_line_ids`,
`scrape_audit_line_ids`, `scrape_internal_audits` — each is a focused CDP/DB CLI; see
its module docstring. Most are also wrapped as MCP tools.

## `alliance_donator.py` — daily treasury donation
Maxes the donate box at the bottom of `/alliance/profile`. It does **not** drag the
jQuery-UI slider; it reads the same state the page's own handler reads
(`#alliance-slider`'s `data-airline-money` + `data-donation-profile`,
`#donation-validation`'s `data-url`) and POSTs `donation=<amount>` to
`/alliance/donate` from the page's origin. Amount = `donationMax - airlineDonations`,
capped by cash minus `--reserve`. `donationMax` is a per-day ceiling, so the script
is idempotent — a second run donates 0. The airline pays the full amount; the
treasury gets it less `dollarTax` (10%). Cap observed 2026-08-04: **$200M/day**.
