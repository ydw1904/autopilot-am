# Mobile API surface (`mobile_api.py`, `mobile_store.py`)

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

