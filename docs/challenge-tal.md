# TAL Journey challenge (2026-09-15 00:00 to 2026-09-28 23:59 UTC, rewards claimable until 2026-10-01 23:59)

Km-flown challenge. Only planes wearing the "Challenge TAL Journey" livery count,
and only between airports in: DE AO AT BE BR CV DK ES FR GR IE IT MA MZ NO NL PL
PT GB SE CH. A320neo counts 1x km, A330-300 2x, X350-1000ULR 3x. Both legs count.

## The plan (F2P: no real money)

Everything is based at **GIG** (owned hub in Brazil). Round trips are chosen so
they divide the 168h weekly grid exactly (`flight_time_rt` = 2 x (km/speed + 1h),
rounded up to 15 min):

| Plane | Route | km | Round trip | Flights/week | Points/day |
|---|---|---|---|---|---|
| A330-300 | GIG-FRA (line 70619141) | 9,570 | 24.00h | 7 | ~38,300 |
| A320neo | GIG-SID (line 70619147) | 4,922 | 13.75h | 12 | ~17,200 |

Planes come from three places, all over the mobile API:

- **Challenge ladder** (free track): A320neo at 0 / 23,520 / 110,230 / 275,450 pts,
  A330 at 522,100 / 2,035,000 / 4,493,000 / 5,288,200, X350 at 6,745,000.
  Claim: `POST challenge/objective/{objectiveId}/claim` (`AMClient.claim_objective`).
- **Store**, two rotations a day (00:00 and 12:00 UTC, each item lives 24h):
  one A320neo for an ad (`purchaseCurrency: adv`, cost 0, claimable through
  `shop2023/in-game/purchase/item` without watching anything) and one A330-300
  for 25,000 travel cards. Budget decision 2026-09-15: buy **2** A330 in total.
- With 28 free A320neos and 2 bought A330s the simulator lands around
  5.7M pts (tier 95: 4 reward A330s, A380-800, X2707). The X350 at 6.745M needs
  5 bought A330s (`scratchpad` sim, session 2026-09-15).

## The autopilot: `code/challenge_tal.py`

Idempotent hourly pass, dry run by default, `--apply` to act:

1. `event/validateended` (deliveries),
2. claim every ladder reward whose goal is reached,
3. claim the store A320neo, buy the store A330 while `a330_bought < --max-a330`
   (counter in `data/challenge_tal_state.json`, seeded to 1 for the plane bought
   by hand on 2026-09-15),
4. `assignHub` every TAL plane to `--hub` (default GIG; free; fails harmlessly
   while the plane is airborne and retries next hour),
5. schedule every TAL plane at the hub that is on no active line (per
   `hub/masstool/{hubId}.activeLines[].aircraftList`), back to back from the next
   quarter hour, opening the route with `line/open` if the hub lacks it.

```bash
.venv/bin/python code/challenge_tal.py              # dry run, prints what it would do
.venv/bin/python code/challenge_tal.py --apply      # one real pass
.venv/bin/python -m pytest code/tests/test_challenge_tal.py -q
```

### Running it hourly

macOS: `launchd/com.lobster.am-challenge-tal.plist.in` (minute 10 of every hour,
logs to `~/.airlines_manager/challenge-tal.log`). `launchd/install.sh` installs it
together with the other agents; to install only this one:

```bash
T=~/Library/LaunchAgents/com.lobster.am-challenge-tal.plist
sed -e "s|@AM_ROOT@|$PWD|g" -e "s|@AM_USER_HOME@|$HOME|g" launchd/com.lobster.am-challenge-tal.plist.in > "$T"
launchctl bootstrap gui/$(id -u) "$T" && launchctl enable gui/$(id -u)/com.lobster.am-challenge-tal
```

Any other scheduler (cron, Hermes) works the same: run
`<repo>/.venv/bin/python <repo>/code/challenge_tal.py --apply` once an hour from
the repo root. Nothing else is needed; the script has no CDP/Chrome dependency.

### Moving it to the Mac mini

The repo syncs via Syncthing, `~/.airlines_manager/session.json` does not. Copy it
once (`scp ~/.airlines_manager/session.json macmini:~/.airlines_manager/`); the
client renews the token itself from then on. Run the agent on **one** machine only:
two machines racing the same store offer is harmless (second claim fails) but
wastes calls. Stop it here with
`launchctl bootout gui/$(id -u)/com.lobster.am-challenge-tal`.

After 2026-10-01 the challenge disappears from `challenge/` and the script exits
with "nothing to do"; remove the agent then. The TAL planes can be sold on the SHM
from 2026-09-29 (price caps: A320neo $500M, A330 $1B, X350 $3B).

## Done on 2026-09-15 (03:16 to 03:30 UTC)

- Store A320neo claimed (free) and A330 bought (25k TC): ids 191162367, 191162368.
- Ladder tier 0 A320neo claimed: id 191162565.
- Routes bought over `line/open`: GIG-FRA $40.4M, GIG-SID $37.9M.
- All three moved LAX -> GIG and scheduled from Tuesday 04:00 UTC.
- Rules window confirms `isProgressing: true`.
