#!/usr/bin/env python3
"""TAL Journey challenge autopilot (2026-09-15 .. 2026-09-28, 72h reward grace).

One idempotent pass, meant to run hourly (launchd/com.lobster.am-challenge-tal
or any cron). Per run, over the mobile API only:

  1. deliver anything finished in the delivery queue (harmless when empty);
  2. claim every free-track challenge reward whose goal is already reached;
  3. take the store's challenge planes: the ad-priced A320neo is always free,
     the travel-card A330-300 is bought until --max-a330 of them were bought;
  4. move every TAL-livery plane to --hub;
  5. schedule every unscheduled TAL plane back-to-back on its route
     (A320neo -> GIG-SID 13.75h round trip x12/week, A330/X350 -> GIG-FRA
     24h x7/week), opening the route first if the hub lacks it.

Default is a dry run. State (how many A330 were bought) lives in
data/challenge_tal_state.json so the cap survives restarts and machine moves.
See docs/challenge-tal.md.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from circuit_planner import flight_time_rt  # noqa: E402
from mobile_api import AMClient, AMError, AMSession  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "data", "challenge_tal_state.json")
WEEK = 7 * 86400
CHALLENGE_TITLE = "TAL Journey"

# skin id -> (label, speed km/h, destination IATA, route distance km)
# Distances pin the exact-fit round trips: 9570 km -> 24.00h, 4922 km -> 13.75h.
PLANES = {
    4713510: ("A320neo", 839, "SID", 4922),
    4713511: ("A330-300", 871, "FRA", 9570),
    4713512: ("X350-1000ULR", 950, "FRA", 9570),
}


def takeoff_times(server_now, ft_hours, lead_min=20):
    """Weekly takeoff slots (s since Monday 00:00 UTC, 15-min grid), back to back.

    `server_now` is the `event` clock string "YYYY-MM-DD HH:MM:SS.ffffff".
    """
    t = datetime.strptime(server_now[:19], "%Y-%m-%d %H:%M:%S")
    now_s = t.weekday() * 86400 + t.hour * 3600 + t.minute * 60 + t.second
    start = -(-(now_s + lead_min * 60) // 900) * 900
    step = int(ft_hours * 3600)
    return [(start + k * step) % WEEK for k in range(WEEK // step)]


def pick_offers(offers, a330_bought, max_a330, travel_cards):
    """Store offers to claim now: (offer, is_purchase)."""
    out = []
    for o in offers:
        if o.get("template") != "aircraft" or CHALLENGE_TITLE not in (o.get("title") or ""):
            continue
        if not o.get("isAvailable") or not (o.get("remaining") or 0):
            continue
        cost, cur = o.get("purchaseCost") or 0, o.get("purchaseCurrency")
        if cur in ("adv", "free") and cost == 0:
            out.append((o, False))
        elif cur == "tc" and a330_bought < max_a330 and travel_cards >= cost:
            out.append((o, True))
            a330_bought += 1
    return out


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"a330_bought": 0}


def save_state(state):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(state, f, indent=1)


def log(*a):
    print(datetime.now(timezone.utc).strftime("%H:%M:%S"), *a, flush=True)


def run(apply, hub_iata, max_a330):
    c = AMClient(AMSession.load())
    state = load_state()
    # bfa/hub names airports by internal id; the masstool's hubList has IATA
    # but omits the hub being queried, so union two hubs' views to cover all.
    hubs = {}
    for h in c.hubs()[:2]:
        hubs.update({x["iata"]: x["id"] for x in c.hub_masstool(h["id"]).get("hubList", [])})
    if hub_iata not in hubs:
        sys.exit(f"hub {hub_iata} not owned; have {sorted(hubs)}")
    hub_id = hubs[hub_iata]

    # 1. deliveries (answers status 0 when the queue is empty: not an error)
    if apply:
        try:
            c.deliver_finished()
        except AMError:
            pass

    # 2. challenge rewards
    chs = [ch for ch in c.challenges() if CHALLENGE_TITLE in (ch.get("title") or "")]
    if not chs:
        log("no active", CHALLENGE_TITLE, "challenge; nothing to do")
        return
    ch = chs[0]
    progress = (ch.get("airlineProgress") or {}).get("progress") or 0
    due = [o for o in ch["objectives"]
           if (o.get("goal") or 0) <= progress and not o.get("claimedDate")]
    log(f"progress {progress:,} pts, {len(due)} reward(s) claimable")
    for o in due:
        labels = [r.get("labelShort") or r.get("effectType") for r in o.get("rewards", [])]
        log("claim objective", o["id"], "goal", o.get("goal"), labels)
        if apply:
            try:
                c.claim_objective(o["id"])
            except AMError as e:
                log("  claim failed:", e)

    # 3. store
    res = c.resources()
    picks = pick_offers(c.shop_offers(), state["a330_bought"], max_a330,
                        res.get("travelCards", 0))
    for o, is_purchase in picks:
        log("store:", o["title"], o.get("purchaseCost"), o.get("purchaseCurrency"))
        if apply:
            try:
                c.claim_offer(o["id"])
                if is_purchase:
                    state["a330_bought"] += 1
                    save_state(state)
            except AMError as e:
                log("  store claim failed:", e)
    log(f"A330 bought from store: {state['a330_bought']}/{max_a330}")

    # 4. + 5. fleet: relocate and schedule
    planes = [a for a in c.fleet() if a.get("as_id") in PLANES]
    log(f"{len(planes)} TAL plane(s) in fleet")
    for a in planes:
        if a["h_id"] != hub_id:
            log("move", a["id"], a["n"], "->", hub_iata)
            if apply:
                try:
                    c.assign_hub(a["id"], hub_id)
                    a["h_id"] = hub_id
                except AMError as e:
                    log("  move failed (airborne?):", e)

    view = c.hub_masstool(hub_id)
    scheduled = {ac["id"] for line in view.get("activeLines", [])
                 for ac in line.get("aircraftList", [])}
    lines = {(r["h"], r["dis"]): r["id"] for r in c.routes()}
    server_now, _ = c._events()
    for a in planes:
        if a["h_id"] != hub_id or a["id"] in scheduled:
            continue
        label, speed, dest, dist = PLANES[a["as_id"]]
        line_id = lines.get((hub_id, dist))
        if line_id is None:
            log("open route", hub_iata, "-", dest)
            if not apply:
                continue
            line_id = int(c.open_line(hub_id, dest)["line"]["id"])
            lines[(hub_id, dist)] = line_id
        ft = flight_time_rt(dist, speed)
        slots = takeoff_times(server_now, ft)
        log(f"schedule {a['id']} {a['n']} ({label}) on line {line_id}: "
            f"{len(slots)} x {ft}h from slot {slots[0]}")
        if apply:
            for s in slots:
                try:
                    c.add_flight(a["id"], line_id, s)
                except AMError as e:
                    log("  add_flight failed:", e)
                    break


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--apply", action="store_true", help="really claim, buy, move, schedule")
    p.add_argument("--hub", default="GIG", help="hub IATA to base the planes at")
    p.add_argument("--max-a330", type=int, default=2,
                   help="A330-300 to buy from the store in total, 25k travel cards each")
    a = p.parse_args()
    run(a.apply, a.hub.upper(), a.max_a330)


if __name__ == "__main__":
    main()
