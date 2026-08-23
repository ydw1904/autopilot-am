# SHM market tooling (`market` commands)

> **Ported note.** This document predates the MCP port and describes the
> original standalone `main.py market …` / `daily …` CLI from the old
> `airlines-manager-tools` tree. That CLI is **not** part of this repo — the
> same operations are MCP tools now. The economics, endpoint shapes and
> hard-won gotchas below are all still accurate; only the invocation changed.
>
> | Old CLI | MCP tool |
> |---|---|
> | `market auth-import` | `mobile_session_import` |
> | `market balance` | `mobile_balance` |
> | `market models` / `prices` | `shm_market`, `mobile_catalog` |
> | `market fleet` | `shm_fleet` |
> | `market show <id>` | `shm_aircraft` |
> | `market sell` | `shm_sell` |
> | `market sell-batch` | `shm_sell_batch` |
> | `daily status` | `mobile_daily_status` |
> | `daily collect` / `wheel` | `mobile_daily_bonuses` |
> | `daily slot` | `mobile_daily_slot` |
>
> Mutating MCP tools default to `dry_run=True`, replacing the old
> `--dry-run` / `--yes` pairing.

Standalone automation against the live Tycoon auction API — **no BlueStacks or
proxy needed to run these**; the emulator + mitmproxy are only used to grab a
fresh session. Reverse-engineered from captured traffic (`capture_am.py`).

Auth = an `access_token` query param + the `PHPSESSID` cookie. No request
signing, so calls are replayable from Python. The session is stored (0600) at
`~/.airlines_manager/session.json`.

## Refresh the session (when it expires)

1. Ensure the patched app + proxy capture are running (see
   `bluestacks_mitm_runbook.md`), open the app so it makes an API call.
2. Import the newest token from the capture log:
   ```bash
   python3 main.py market auth-import            # reads tools/captures/am_api.jsonl
   ```
   It validates immediately and prints your balance. When commands start
   failing with "session likely expired", repeat this.

## Commands

| Command | What it does |
|---|---|
| `market balance` | Current dollars / AM coins / research$ / travel cards |
| `market models -c 747` | Distinct models on the market now → **skin id ↔ name** (find the 747SP's id) |
| `market prices -m <skinId>` | Price stats + live listings for a model (the arbitrage lens) |
| `market fleet -n 747 [--all-pages]` | Owned aircraft filtered by nickname/skin id |
| `market show <aircraftId>` | One aircraft's model, raw price, hub, wear |
| `market sell <aircraftId> --bin <price>` | List one aircraft (buy-now = `--bin`) |
| `market sell-batch -n 747 --bin <price>` | Bulk-list matching fleet planes, human-jittered |

### Selling / 747SP flip

Freshly-bought planes are auto-named `SHOP-<model>`, so after minting 747SPs:

```bash
# see what 747SPs currently fetch in your star pool
python3 main.py market prices -c 747SP --pool-only

# list all your minted 747SPs at the max buy-now price (dry-run first).
# --bin = the aircraft's binThreshold; see "Pricing rule" below — do not
# invent a lower number.
python3 main.py market sell-batch -n 747 --bin 1209000000 --price 177000000 --dry-run
python3 main.py market sell-batch -n 747 --bin 1209000000 --price 177000000 --yes
```

`put_up` params: `price` (starting bid, defaults to `--bin`), `binPrice`
(buy-it-now), `duration` (hours, default 11), `aircraftId`. Response returns the
new auction id and `alertThreshold` = the game's fair-value line.

### Pricing rule: arbitrage always lists at the max price

Never hand-pick a buy-now number for an arbitrage plane. `shm_aircraft` returns
three distinct caps, and the flip uses two of them:

| Field | Meaning | Use |
|---|---|---|
| `binThreshold` | **max buy-it-now the game accepts.** Per *livery*, not per model | → `bin_price` |
| `maxAuctionSellPrice` | max **starting bid** = the model's raw value | → `price` |
| `minAuctionSellPrice` | min starting bid — often **below** what you paid | ⚠️ never use |
| `sellPrice` | instant scrap-to-the-game price | not a market price |

Starting at `maxAuctionSellPrice` matters: a listing can be won at its opening
bid, and `minAuctionSellPrice` on a 747SP is $88M against a $160M mint cost — a
$72M loss per plane if a single bidder takes it. Opening at the $177M raw value
makes the downside a small profit and the upside the full BIN.

`binThreshold` being per-livery is what the market's price tiers actually are:
the Spirit 747SP caps at **$1.209B**, while an Il-96-300 "Tokyo Sports Event
2021" caps at **$8B** — that's why those sit on the market at exactly
8,000,000,000. Read the threshold off the aircraft; don't assume a tier.

Note `alertThreshold` in the put_up response is a *different, higher* number
(3.717B on a Spirit 747SP listed at its 1.209B cap) — it's the fair-value line,
not a ceiling you can price to. Confirmed 2026-08-06: a BIN exactly at
`binThreshold` is accepted.

```bash
# the whole rule, in MCP terms
shm_aircraft(<id>)                      # read binThreshold + maxAuctionSellPrice
shm_sell_batch(ids=[…], bin_price=<binThreshold>, price=<maxAuctionSellPrice>)
```

## The 747SP arbitrage (confirmed economics)

Minting a 747SP (model **151**, Manufacturer skin **2801396**, `isClassic`):

- With the 747SP **license**, the 20 AM-coins/plane cost is **waived** — you pay
  **money only, ~$160M each** (raw value $177M). Verified: a batch of 20 dropped
  dollars by $3.19B and left AM-coins **unchanged**.
- New planes have a **30-min delivery** and are **NOT sellable until delivered**:
  `put_up` during delivery is rejected with `status=0 message=0` (observed
  2026-08-12; wait ~35 min after minting, then list). Also, fresh planes do
  **not** all appear in the paged fleet right away — so sell freshly-minted
  planes by explicit id from the buy response, not a name scan.
- Mint via mobile: `AMClient.buy_multiple(model_id=151, hub_id=…, quantity=20,
  name=…, skin_id=…, eco=136, bus=74, first=31, payload=12)` (added 2026-08-12;
  wraps the captured `POST aircraft/buymultiple`). New ids come back in
  `events[].objectid` — see `AMClient.bought_aircraft_ids()`.

```bash
# sell a just-minted batch by the ids from the buy response.
# --price = maxAuctionSellPrice (raw value, so a one-bid close still profits),
# --bin   = binThreshold. NOT --price 88000000: that's minAuctionSellPrice,
#           which is below the ~$160M mint cost.
python3 main.py market sell-batch --ids "188272456,188272457,…" \
        --price 177000000 --bin 1209000000 --dry-run
```

Buy/mint endpoint (captured): `POST aircraft/buymultiple`, body
`purchaseAssistance=false&aircrafts=[{"aircraftId":151,"hubId":<hub>,"quantity":N,`
`"name":"…","aircraftSkinId":2801396,"seatsEco":136,"seatsBus":74,"seatsFirst":31,`
`"payload":12}]`. Response `events[].objectid` = the new aircraft ids.
`AMClient.buy_multiple()` exposes this (mobile, money-only with license).

`market bid <auctionId> <amount>` (sniping) is supported by `AMClient.bid` but
intentionally not exposed as a spending CLI command yet.

## Daily free-currency collection (`daily` commands)

The shop's free "workshop" offers give 4 currencies, each claimable **5×/day**:
money (+$10M), AM coins (+2), research$ (+$5M), travel cards (+1000).

```bash
python3 main.py daily status      # show remaining claims per currency
python3 main.py daily collect     # claim everything left today
python3 main.py daily wheel       # spin the travel-card wheel + auto-respin
python3 main.py daily all         # currency pickups + wheel, one shot
```

**Travel-card wheel** (`daily wheel`): you get one spin + one respin per day.
The server keeps the **higher** of the two scores, so the script always plays
then always respins — the respin can only match or beat spin 1. Reports both
spin scores and the travel cards won. Idempotent: if today's wheel is already
spent it says "already done".

**Slot machine** (`daily slot`): spins all FREE daily games (`nbRemainingGames`,
20 standard but read live so bonus games are included). Key gotchas learned the
hard way:
- Each spin costs one *free game*, not a ticket — the script is bounded to
  `nbRemainingGames` and **never** spins into paid (ticket/coin) territory.
- The reel has a ~10s animation; POSTing faster returns **204 (empty) but still
  burns the game**. So spins are paced ~9s (`--spin-delay`) and an empty result
  is reported as "unread", never retried (that would double-spend). A fresh day
  of 20 spins takes ~3 min.
- Reports the haul by type and flags jackpots (`[1,1,1]` = $24M for VIP).
- The "spin N times" milestone reward (`casBonusProfiles`) isn't built yet — it
  was empty (no event active); capture a claim when one runs.

`daily collect` only ever touches **free** offers (hard-guarded on
`purchaseCost == 0`), so it can never spend a balance. It's idempotent — once an
offer is drained it drops from the feed, so re-running is a no-op. Safe to put on
a daily schedule. Full haul when fresh = +$50M money, +10 coins, +$25M research,
+5000 cards.

## Ban-risk note
This drives the real game API. Keep `sell-batch --min-delay` reasonable, don't
run 24/7, and prefer human-scale volumes. Your account, your call.

What the server actually meters is worth knowing, because it isn't reads:
`loading/notification` → `auctionRules` publishes `maxBidByDay` (20),
`maxSpentInBidSince`, `countMaxAuction` (10) and `purchaseFeePercent` (20).
Bids and listings are counted and capped; nothing in that payload meters how
often the listing endpoint is read. There is also no push channel to poll
instead — the app's only realtime socket is the Paradox XMPP chat server from
`chat/credentials`, and its OneSignal pushes are per-account events, so
watching the market means reading it. `shm_watcher.py` is the low-noise way to
do that: one filtered request per watched model rather than a repeated sweep
of everything.
