# SHM watcher, dailies, and the mobile sync scripts

## `shm_watcher.py` — standing orders for specific liveries
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
  offer the shop charges for: `currency=realMoney` packs (`shop-pack:auto`),
  `currency=tc` ticket aircraft (`shop-ticket:auto`) and `currency=amc` AM-coin
  aircraft (`shop-amc:auto`), plus special free skins on the AM Gold Crew
  reward track (`AMGoldStep`, `shop-gold:auto`) and challenge rewards. Ordinary
  free gifts arrive on their own. Paid-pack targets that are missing start
  armed; ticket, AM-coin, Gold Crew, challenge and manual targets begin
  observing, because they are still directly claimable or buyable and an
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
  - Every list on the tab -- the watch table and the three activity feeds --
    carries the same toolbar the fleet page uses: search, the dropdowns that
    list cares about, its sort, then the "N active filters / Clear all /
    showing X of Y" strip. Feed state is one `FeedFilters` (`query` plus the
    feed's own `select`), and a sort is one `"key:dir"` string so the pair is a
    single menu option -- there is no direction button. The feeds open on their
    newest rows rather than on name order, which is the one place the sort-menu
    rule below bends: an activity log read alphabetically is not a log.
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
    shop gift / ad gift / AM coins / travel cards / paid pack (the shop's own
    `template` + `currency` pair — `aircraft`/`amc` is the AM-coin aircraft, a
    purchase and never a gift), duty free, user-created. Retired challenges
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

