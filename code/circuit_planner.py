#!/usr/bin/env python3
"""
Circuit Planner — Revenue-Optimized Circuit Design

Phase 1: Beam search for route combinations, scored by quick revenue estimate.
         Considers demand matching, time constraint, and multi-aircraft comparison.
Phase 2: Full grid search over seat config + waves + SuperSim pricing.

Wave model:
  - 1 wave = 7 aircraft, staggered Mon→Sun
  - Each day, one aircraft visits each route in the circuit
  - Daily flights per route = waves
  - Capacity = 2 × seats × waves (round trip factor)
  - Circuit time ≤ 168h (one rotation per week)

Usage:
    python3 circuit_planner.py --hub HKG --aircraft B742 B743 B744 B748 --circuits 10
    python3 circuit_planner.py --hub HKG --aircraft B742 B743 --circuits 5 --owned-hubs FRA CGK PEK JNB
"""

import sqlite3, math, argparse, heapq, time as _time, os
import numpy as np

try:
    from numba import njit
except ImportError:
    njit = None

try:
    from circuit_planner_native import search_circuits_native
    _HAS_NATIVE = True
except ImportError:
    search_circuits_native = None
    _HAS_NATIVE = False

from db import DB
from aircraft_aliases import resolve as resolve_aircraft


def flight_time_rt(distance_km, speed_kmh):
    return math.ceil((((distance_km / speed_kmh) + 1) * 2) * 4) / 4


def load_aircraft(db, name):
    r = resolve_aircraft(name)
    resolved = r.model if r.status == "ok" else name  # keep LIKE fallback for partials
    row = db.execute(
        "SELECT model, category, speed_kmh, range_km, max_pax, max_tonnage, gross_price "
        "FROM aircraft WHERE model=? OR model LIKE ?",
        (resolved, f"%{resolved}%")
    ).fetchone()
    if not row:
        return None
    return {
        "alias": name.upper(), "model": row[0], "cat": row[1],
        "speed": row[2], "range": row[3], "pax": row[4], "tonnage": row[5],
        "price": row[6] or 0,
    }


def load_routes(db, hub, ac, exclude_iatas=None, min_dist=None, max_dist=None):
    exclude_iatas = set((x or "").upper() for x in (exclude_iatas or []))
    effective_max = min(ac["range"], max_dist) if max_dist else ac["range"]
    rows = db.execute(
        "SELECT dest_iata, dest_name, dest_country, distance_km, dest_category, "
        "eco_demand, bus_demand, fir_demand, cargo_demand, gross_price "
        "FROM routes WHERE hub_iata=? AND distance_km<=? AND dest_category>=? "
        "AND eco_demand IS NOT NULL AND eco_demand > 0 "
        "ORDER BY distance_km DESC",
        (hub, effective_max, ac["cat"])
    ).fetchall()
    routes = []
    for r in rows:
        iata = r[0].upper()
        if iata in exclude_iatas:
            continue
        if min_dist and r[3] < min_dist:
            continue
        ft = flight_time_rt(r[3], ac["speed"])
        if ft > 168:
            continue
        routes.append({
            "iata": iata, "name": r[1], "country": r[2],
            "dist": r[3], "cat": r[4],
            "eco_d": r[5] or 0, "bus_d": r[6] or 0,
            "fir_d": r[7] or 0, "cargo_d": r[8] or 0,
            "ft": ft, "price": r[9] or 0,
        })
    return routes


# ═══════════════════════════════════════════════════════════════════
#  Pricing Formulas
# ═══════════════════════════════════════════════════════════════════

def ideal_eco(dist, comfort):
    return math.floor(100 + dist * 0.3 * (1 + comfort / 3000))

def ideal_bus(dist, comfort):
    return math.floor(ideal_eco(dist, comfort) * 1.33)

def ideal_fir(dist, comfort):
    return math.floor(ideal_eco(dist, comfort) * 2.3)

def ideal_cargo(dist, speed):
    p = 0.56 if dist <= 1999 else 0.52 if dist <= 5000 else 0.47
    return math.ceil(200 + p * dist * (1 + speed / 500))

def supersim_price(audit_price, capacity, demand):
    if audit_price == 0 or demand == 0:
        return 0
    if capacity <= 0:
        return audit_price
    if capacity < demand:
        return math.floor(audit_price * (1 - (capacity - demand) / (3 * demand)))
    return audit_price

def daily_turnover(audit_price, capacity, demand):
    if capacity <= 0 or demand <= 0 or audit_price == 0:
        return 0
    return min(demand, capacity) * supersim_price(audit_price, capacity, demand)


# ═══════════════════════════════════════════════════════════════════
#  PHASE 1 — Revenue-Estimated Beam Search
# ═══════════════════════════════════════════════════════════════════

def quick_revenue_estimate(routes_list, ac, comfort, speed, max_waves=20,
                           overshoot_pct=0.0):
    """Phase 1 scoring: best shared-config revenue for a set of routes.

    Key constraint: ALL routes share the same (eco, bus, fir, cargo) seats.
    Max waves = floor(min(demand / (2 × seats))) across ALL route/class pairs.
    The bottleneck route/class determines everything.

    Uses demand-derived configs: seats proportional to min_demand across routes,
    tried at multiple target wave counts. This naturally penalizes mismatched
    routes (e.g. FIH fir=18 caps the whole circuit's fir allocation).
    """
    max_pax = ac["pax"]
    max_ton = ac["tonnage"]

    if not routes_list:
        return 0

    # Pre-compute prices per route
    prices = []
    for r in routes_list:
        prices.append({
            "eco": ideal_eco(r["dist"], comfort),
            "bus": ideal_bus(r["dist"], comfort),
            "fir": ideal_fir(r["dist"], comfort),
            "cargo": ideal_cargo(r["dist"], speed),
        })

    # Min demands across routes — bottleneck for shared config
    min_eco = min(r["eco_d"] for r in routes_list)
    min_bus = min(r["bus_d"] for r in routes_list)
    min_fir = min(r["fir_d"] for r in routes_list)
    min_cargo = min(r["cargo_d"] for r in routes_list)

    best_rev = 0

    def eval_cfg(eco, bus, fir, cargo):
        """Evaluate a shared config: check constraints, find max waves, compute rev."""
        if eco + bus + fir + cargo == 0:
            return 0
        if eco * 0.1 + bus * 0.125 + fir * 0.15 + cargo * 1.0 > max_ton:
            return 0
        if eco * 1.0 + bus * 1.8 + fir * 4.2 > max_pax:
            return 0
        # Size waves to fill ECONOMY demand only. Eco is the highest-volume
        # class with best revenue/seat-cost in this game; bus/fir/cargo are
        # by-products of the same aircraft and their leftover demand is
        # acceptable. Optimising wave count for all classes pushes wave counts
        # very high to chase a tiny FIR/BUS demand for marginal gain — bad ROI.
        wl = []
        tol = 1.0 + max(0.0, overshoot_pct)
        if eco > 0:
            for r in routes_list:
                d = r["eco_d"]
                if d <= 0:
                    return 0
                wl.append(d / (2 * eco))
        else:
            # No eco seats: fall back to bottleneck across whichever classes exist.
            for r in routes_list:
                for s, dk in [(bus, "bus_d"), (fir, "fir_d"), (cargo, "cargo_d")]:
                    if s <= 0: continue
                    d = r[dk]
                    if d <= 0: return 0
                    wl.append(d * tol / (2 * s))
        if not wl:
            return 0
        mw = min(int(math.ceil(max(wl))), max_waves)
        if mw < 1:
            return 0
        rev = 0
        for i, r in enumerate(routes_list):
            rev += daily_turnover(prices[i]["eco"], 2 * eco * mw, r["eco_d"])
            rev += daily_turnover(prices[i]["bus"], 2 * bus * mw, r["bus_d"])
            rev += daily_turnover(prices[i]["fir"], 2 * fir * mw, r["fir_d"])
            rev += daily_turnover(prices[i]["cargo"], 2 * cargo * mw, r["cargo_d"])
        return rev

    # Demand-derived configs: seats = min_demand / (2 × target_waves)
    # This ensures max_waves ≈ target_waves when min demands are consistent
    for tw in [3, 5, 8, 10, 15, 20]:
        for fir_on, bus_on in [(True, True), (True, False),
                               (False, True), (False, False)]:
            fir_seats = max(0, int(min_fir / (2 * tw))) if fir_on else 0
            bus_seats = max(0, int(min_bus / (2 * tw))) if bus_on else 0
            cargo_seats = max(0, int(min_cargo / (2 * tw)))

            # Eco: capped by demand/target AND payload AND seat constraints
            eco_dem = max(0, int(min_eco / (2 * tw)))
            eco_pay = max(0, int((max_ton - bus_seats * 0.125
                                  - fir_seats * 0.15 - cargo_seats * 1.0) / 0.1))
            eco_seat = max(0, int((max_pax - bus_seats * 1.8 - fir_seats * 4.2) / 1.0))
            eco_seats = min(eco_dem, eco_pay, eco_seat)

            best_rev = max(best_rev, eval_cfg(eco_seats, bus_seats, fir_seats, cargo_seats))

    return best_rev


if njit is not None:
    @njit(cache=True)
    def _eval_cfg_nb(eco, bus, fir, cargo, demands, prices,
                     max_pax, max_ton, max_waves, overshoot_pct):
        if eco + bus + fir + cargo == 0:
            return 0.0
        if eco * 0.1 + bus * 0.125 + fir * 0.15 + cargo * 1.0 > max_ton:
            return 0.0
        if eco * 1.0 + bus * 1.8 + fir * 4.2 > max_pax:
            return 0.0
        R = demands.shape[0]
        seats0, seats1, seats2, seats3 = eco, bus, fir, cargo
        mw_f = float(max_waves)
        tol = 1.0 + overshoot_pct if overshoot_pct > 0.0 else 1.0
        for r in range(R):
            for c in range(4):
                if c == 0: s = seats0
                elif c == 1: s = seats1
                elif c == 2: s = seats2
                else: s = seats3
                if s <= 0:
                    continue
                d = demands[r, c]
                if d <= 0:
                    return 0.0
                # Eco (c=0) strict; bus/fir/cargo allow overshoot
                allowed = d if c == 0 else d * tol
                wl = allowed / (2.0 * s)
                if wl < mw_f:
                    mw_f = wl
        mw = int(math.floor(mw_f))
        if mw < 1:
            return 0.0
        if mw < 1:
            return 0.0
        rev = 0.0
        for r in range(R):
            for c in range(4):
                if c == 0: s = seats0
                elif c == 1: s = seats1
                elif c == 2: s = seats2
                else: s = seats3
                if s <= 0:
                    continue
                d = demands[r, c]
                p = prices[r, c]
                cap = 2.0 * s * mw
                if p == 0.0 or d == 0.0 or cap <= 0.0:
                    continue
                if cap < d:
                    ss = math.floor(p * (1.0 - (cap - d) / (3.0 * d)))
                else:
                    ss = p
                filled = d if d < cap else cap
                rev += filled * ss
        return rev


    @njit(cache=True)
    def _quick_revenue_estimate_nb(demands, prices, max_pax, max_ton,
                                   max_waves, overshoot_pct):
        R = demands.shape[0]
        if R == 0:
            return 0.0
        min_eco = demands[0, 0]
        min_bus = demands[0, 1]
        min_fir = demands[0, 2]
        min_cargo = demands[0, 3]
        for r in range(1, R):
            if demands[r, 0] < min_eco: min_eco = demands[r, 0]
            if demands[r, 1] < min_bus: min_bus = demands[r, 1]
            if demands[r, 2] < min_fir: min_fir = demands[r, 2]
            if demands[r, 3] < min_cargo: min_cargo = demands[r, 3]

        best = 0.0
        tw_arr = (3, 5, 8, 10, 15, 20)
        for ti in range(6):
            tw = tw_arr[ti]
            for fb in range(4):
                fir_on = (fb >> 1) & 1
                bus_on = fb & 1
                fir_seats = int(min_fir / (2 * tw)) if fir_on else 0
                if fir_seats < 0: fir_seats = 0
                bus_seats = int(min_bus / (2 * tw)) if bus_on else 0
                if bus_seats < 0: bus_seats = 0
                cargo_seats = int(min_cargo / (2 * tw))
                if cargo_seats < 0: cargo_seats = 0

                eco_dem = int(min_eco / (2 * tw))
                if eco_dem < 0: eco_dem = 0
                eco_pay = int((max_ton - bus_seats * 0.125 - fir_seats * 0.15 - cargo_seats * 1.0) / 0.1)
                if eco_pay < 0: eco_pay = 0
                eco_seat = int((max_pax - bus_seats * 1.8 - fir_seats * 4.2) / 1.0)
                if eco_seat < 0: eco_seat = 0
                eco_seats = eco_dem
                if eco_pay < eco_seats: eco_seats = eco_pay
                if eco_seat < eco_seats: eco_seats = eco_seat

                r = _eval_cfg_nb(eco_seats, bus_seats, fir_seats, cargo_seats,
                                 demands, prices, max_pax, max_ton, max_waves,
                                 overshoot_pct)
                if r > best:
                    best = r
        return best
else:
    _quick_revenue_estimate_nb = None


def time_efficiency(circuit_time):
    if circuit_time <= 0 or circuit_time > 168:
        return 0.0
    utilization = circuit_time / 168.0
    rotations = 168.0 / circuit_time
    frac = rotations - math.floor(rotations)
    rotation_fit = 1.0 - 2 * min(frac, 1.0 - frac)
    return utilization * 0.6 + rotation_fit * 0.4


def _search_circuits_native(routes, ac, comfort, speed, top_n, beam_width,
                             max_steps, max_routes, max_waves, match):
    routes_ranked = sorted(enumerate(routes), key=lambda x: -x[1]["eco_d"])
    top = routes_ranked[:max_routes]
    M = len(top)

    demands = np.empty((M, 4), dtype=np.float64)
    prices = np.empty((M, 4), dtype=np.float64)
    flight_times = np.empty(M, dtype=np.float64)
    eco_demands_arr = np.empty(M, dtype=np.float64)
    cargo_demands_arr = np.empty(M, dtype=np.float64)

    for new_i, (orig_i, r) in enumerate(top):
        demands[new_i, 0] = r["eco_d"]
        demands[new_i, 1] = r["bus_d"]
        demands[new_i, 2] = r["fir_d"]
        demands[new_i, 3] = r["cargo_d"]
        prices[new_i, 0] = ideal_eco(r["dist"], comfort)
        prices[new_i, 1] = ideal_bus(r["dist"], comfort)
        prices[new_i, 2] = ideal_fir(r["dist"], comfort)
        prices[new_i, 3] = ideal_cargo(r["dist"], speed)
        flight_times[new_i] = r["ft"]
        eco_demands_arr[new_i] = r["eco_d"]
        cargo_demands_arr[new_i] = r["cargo_d"]

    top_idx = np.arange(M, dtype=np.int64)

    raw = search_circuits_native(
        demands, prices, flight_times, eco_demands_arr, cargo_demands_arr,
        top_idx, float(ac["pax"]), float(ac["tonnage"]),
        max_waves, top_n, beam_width, max_steps, match,
    )

    orig_map = [orig_i for orig_i, _ in top]
    return [(score, total_time, frozenset(orig_map[k] for k in indices))
            for score, total_time, indices in raw]


def _search_circuits_python(routes, ac, comfort, speed, top_n, beam_width,
                             max_steps, max_routes, max_waves, match,
                             score_mode='revenue', overshoot_pct=0.0):
    routes_ranked = sorted(enumerate(routes), key=lambda x: -x[1]["eco_d"])
    indexed = routes_ranked[:max_routes]

    N = len(routes)
    demands_full = np.empty((N, 4), dtype=np.float64)
    prices_full = np.empty((N, 4), dtype=np.float64)
    route_prices = np.array([r["price"] for r in routes], dtype=np.float64)
    for i, r in enumerate(routes):
        demands_full[i, 0] = r["eco_d"]
        demands_full[i, 1] = r["bus_d"]
        demands_full[i, 2] = r["fir_d"]
        demands_full[i, 3] = r["cargo_d"]
        prices_full[i, 0] = ideal_eco(r["dist"], comfort)
        prices_full[i, 1] = ideal_bus(r["dist"], comfort)
        prices_full[i, 2] = ideal_fir(r["dist"], comfort)
        prices_full[i, 3] = ideal_cargo(r["dist"], speed)
    max_pax_f = float(ac["pax"])
    max_ton_f = float(ac["tonnage"])
    ac_price_f = float(ac["price"])

    INF = float('inf')
    beam = [(0.0, frozenset(), INF, 0, INF, 0)]
    best = []
    seen = set()
    score_cache = {}
    roi_cache = {}

    def cached_score(indices_fs):
        key = tuple(sorted(indices_fs))
        if key not in score_cache:
            if len(key) >= 2:
                idx = np.asarray(key, dtype=np.int64)
                score_cache[key] = _quick_revenue_estimate_nb(
                    demands_full[idx], prices_full[idx],
                    max_pax_f, max_ton_f, max_waves, overshoot_pct)
            else:
                r = routes[key[0]]
                score_cache[key] = r["eco_d"] * ideal_eco(r["dist"], comfort)
        return score_cache[key]

    def cached_roi_score(indices_fs):
        key = tuple(sorted(indices_fs))
        if key not in roi_cache:
            rev = cached_score(indices_fs)
            if rev <= 0:
                roi_cache[key] = 0.0
            else:
                route_inv = float(sum(route_prices[i] for i in key))
                # Estimate waves: eco demand bottleneck with ~60% eco seats
                min_eco = min(demands_full[i, 0] for i in key)
                eco_seat_est = max(1.0, max_pax_f * 0.6)
                est_waves = max(1.0, min(float(max_waves), min_eco / (2.0 * eco_seat_est)))
                total_inv = route_inv + est_waves * 7.0 * ac_price_f
                roi_cache[key] = rev / total_inv if total_inv > 0 else 0.0
        return roi_cache[key]

    score_fn = cached_roi_score if score_mode == 'roi' else cached_score

    for step in range(max_steps):
        next_beam = []
        for time_used, indices, mn_eco, mx_eco, mn_cgo, mx_cgo in beam:
            for i, rd in indexed:
                if i in indices:
                    continue
                new_time = time_used + rd["ft"]
                if new_time > 168:
                    continue

                new_mn_eco = min(mn_eco, rd["eco_d"])
                new_mx_eco = max(mx_eco, rd["eco_d"])
                new_mn_cgo = min(mn_cgo, rd["cargo_d"])
                new_mx_cgo = max(mx_cgo, rd["cargo_d"])

                if new_mx_eco > 0 and new_mn_eco / new_mx_eco < match:
                    continue
                if new_mx_cgo > 0 and new_mn_cgo / new_mx_cgo < match:
                    continue

                new_indices = frozenset(indices | {i})
                key = tuple(sorted(new_indices))
                if key in seen:
                    continue
                seen.add(key)

                score = score_fn(new_indices)

                entry = (score, new_time, new_indices)
                if len(best) < top_n or score > best[0][0]:
                    heapq.heappush(best, entry)
                    if len(best) > top_n:
                        heapq.heappop(best)

                next_beam.append((new_time, new_indices,
                                  new_mn_eco, new_mx_eco,
                                  new_mn_cgo, new_mx_cgo))

        if len(next_beam) > beam_width:
            next_beam.sort(key=lambda s: -score_fn(s[1]))
            next_beam = next_beam[:beam_width]

        beam = next_beam
        if not beam:
            break

    best.sort(key=lambda x: -x[0])
    return best


def search_circuits(routes, ac, comfort, speed, top_n=3, beam_width=1200,
                    max_steps=12, max_routes=150, max_waves=20, match=0.9,
                    score_mode='revenue', overshoot_pct=0.0):
    """Beam search with demand-ratio filtering on eco + cargo.

    Only expands states where eco and cargo min/max demand ratios stay
    above the match threshold. Fir and bus are not filtered — they're
    minor revenue contributors and the optimizer can drop them if needed.

    score_mode='revenue': maximise daily revenue (default)
    score_mode='roi':     maximise daily_rev / estimated_investment (best payback)
    """
    # Native C path doesn't know about overshoot — fall back to Python when set.
    if _HAS_NATIVE and score_mode == 'revenue' and overshoot_pct == 0.0:
        return _search_circuits_native(
            routes, ac, comfort, speed, top_n, beam_width,
            max_steps, max_routes, max_waves, match)
    return _search_circuits_python(
        routes, ac, comfort, speed, top_n, beam_width,
        max_steps, max_routes, max_waves, match, score_mode, overshoot_pct)


# ═══════════════════════════════════════════════════════════════════
#  PHASE 2 — Full Seat Config + Revenue Optimization
# ═══════════════════════════════════════════════════════════════════

def optimize_circuit(routes_list, ac, comfort=500, speed=700, max_waves=20,
                     overshoot_pct=0.0):
    """Phase 2 optimizer.

    ``overshoot_pct`` controls how much over-supply is permitted on bus/fir/
    cargo when sizing waves. 0.0 (default) is strict — capacity ≤ demand on
    every class. 0.10 lets bus/fir/cargo cap exceed demand by up to 10% so
    that eco (the meta) can run more waves. Eco is always strict.
    """
    max_pax = ac["pax"]
    max_ton = ac["tonnage"]

    for rd in routes_list:
        rd["p_eco"] = ideal_eco(rd["dist"], comfort)
        rd["p_bus"] = ideal_bus(rd["dist"], comfort)
        rd["p_fir"] = ideal_fir(rd["dist"], comfort)
        rd["p_cargo"] = ideal_cargo(rd["dist"], speed)

    best_rev = -1
    best_cfg = None
    best_waves = 1

    max_fir = min(int(max_ton / 0.15), int(max_pax / 4.2))
    max_bus = min(int(max_ton / 0.125), int(max_pax / 1.8))
    max_cargo = int(max_ton)

    fir_step = max(1, max_fir // 12)
    bus_step = max(1, max_bus // 20)
    cargo_step = max(1, max_cargo // 12)

    tol = 1.0 + max(0.0, overshoot_pct)

    def eval_config(eco, bus, fir, cargo):
        if eco == 0 and bus == 0 and fir == 0 and cargo == 0:
            return 0, 0
        mw_f = float(max_waves)
        for rd in routes_list:
            # Eco always strict; bus/fir/cargo allowed to oversupply by `tol`.
            for seats, dem_key, strict in [
                (eco,   "eco_d",   True),
                (bus,   "bus_d",   False),
                (fir,   "fir_d",   False),
                (cargo, "cargo_d", False),
            ]:
                if seats <= 0:
                    continue
                dem = rd[dem_key]
                if dem <= 0:
                    return 0, 0
                allowed = dem if strict else dem * tol
                wl = allowed / (2 * seats)
                if wl < mw_f:
                    mw_f = wl
        mw = int(math.floor(mw_f))
        if mw < 1:
            return 0, 0
        rev = 0
        for rd in routes_list:
            rev += daily_turnover(rd["p_eco"], 2 * eco * mw, rd["eco_d"])
            rev += daily_turnover(rd["p_bus"], 2 * bus * mw, rd["bus_d"])
            rev += daily_turnover(rd["p_fir"], 2 * fir * mw, rd["fir_d"])
            rev += daily_turnover(rd["p_cargo"], 2 * cargo * mw, rd["cargo_d"])
        return rev, mw

    # Coarse grid
    for fir in range(0, max_fir + 1, fir_step):
        for bus in range(0, max_bus + 1, bus_step):
            for cargo in range(0, max_cargo + 1, cargo_step):
                payload = fir * 0.15 + bus * 0.125 + cargo * 1.0
                if payload > max_ton:
                    break
                seat_space = fir * 4.2 + bus * 1.8
                if seat_space > max_pax:
                    break
                eco = max(0, min(int((max_ton - payload) / 0.1),
                                 int((max_pax - seat_space) / 1.0)))
                rev, waves = eval_config(eco, bus, fir, cargo)
                if rev > best_rev:
                    best_rev = rev
                    best_cfg = {"eco": eco, "bus": bus, "fir": fir, "cargo": cargo}
                    best_waves = waves

    # Fine-tune
    if best_cfg:
        fc, bc, cc = best_cfg["fir"], best_cfg["bus"], best_cfg["cargo"]
        for fir in range(max(0, fc - fir_step), min(max_fir, fc + fir_step) + 1):
            for bus in range(max(0, bc - bus_step), min(max_bus, bc + bus_step) + 1):
                for cargo in range(max(0, cc - cargo_step), min(max_cargo, cc + cargo_step) + 1):
                    payload = fir * 0.15 + bus * 0.125 + cargo * 1.0
                    if payload > max_ton:
                        break
                    seat_space = fir * 4.2 + bus * 1.8
                    if seat_space > max_pax:
                        break
                    eco = max(0, min(int((max_ton - payload) / 0.1),
                                     int((max_pax - seat_space) / 1.0)))
                    rev, waves = eval_config(eco, bus, fir, cargo)
                    if rev > best_rev:
                        best_rev = rev
                        best_cfg = {"eco": eco, "bus": bus, "fir": fir, "cargo": cargo}
                        best_waves = waves

    # Build breakdown
    breakdown = []
    if best_cfg and best_waves > 0:
        for rd in routes_list:
            route_info = {"iata": rd["iata"], "dist": rd["dist"], "classes": []}
            route_rev = 0
            for cls_name, seats, price_key, dem_key in [
                ("eco", best_cfg["eco"], "p_eco", "eco_d"),
                ("bus", best_cfg["bus"], "p_bus", "bus_d"),
                ("fir", best_cfg["fir"], "p_fir", "fir_d"),
                ("cargo", best_cfg["cargo"], "p_cargo", "cargo_d"),
            ]:
                cap = 2 * seats * best_waves
                dem = rd[dem_key]
                price = rd[price_key]
                ss = supersim_price(price, cap, dem)
                filled = min(cap, dem)
                cls_rev = filled * ss
                route_rev += cls_rev
                rem = dem - cap
                route_info["classes"].append({
                    "name": cls_name, "seats": seats, "cap": cap,
                    "dem": dem, "price": price, "ss_price": ss,
                    "filled": filled, "rev": cls_rev, "remaining": rem,
                })
            route_info["rev"] = route_rev
            breakdown.append(route_info)

    return best_cfg, best_waves, best_rev, breakdown


# ═══════════════════════════════════════════════════════════════════
#  Output
# ═══════════════════════════════════════════════════════════════════

def print_circuit(rank, ac, routes_list, total_time, cfg=None, waves=0,
                  daily_rev=0, breakdown=None, p1_score=0, bulk_discount=0.13,
                  bulk_threshold=63):
    rotations = 168.0 / total_time if total_time > 0 else 0
    gap = 168 - total_time
    tot = {k: sum(r[k] for r in routes_list) for k in ["eco_d", "bus_d", "fir_d", "cargo_d"]}
    mn = {k: min(r[k] for r in routes_list) for k in ["eco_d", "bus_d", "fir_d", "cargo_d"]}
    mx = {k: max(r[k] for r in routes_list) for k in ["eco_d", "bus_d", "fir_d", "cargo_d"]}

    print(f"\n{'─' * 80}")
    print(f"  Circuit #{rank}  |  {ac['alias']} ({ac['model']})")
    print(f"  Time: {total_time:.2f}h / 168h ({gap:.2f}h gap)  |  Rotations: {rotations:.2f}")
    print(f"  Routes: {len(routes_list)}  |  Phase1 est: ${p1_score:,.0f}/day")

    if cfg:
        payload = cfg["eco"] * 0.1 + cfg["bus"] * 0.125 + cfg["fir"] * 0.15 + cfg["cargo"] * 1.0
        seats = cfg["eco"] * 1.0 + cfg["bus"] * 1.8 + cfg["fir"] * 4.2
        weekly = daily_rev * 7
        planes = waves * 7

        # Investment calculation
        ac_unit = ac["price"]
        discount = bulk_discount if planes >= bulk_threshold else 0
        ac_cost = planes * ac_unit * (1 - discount)
        route_cost = sum(r["price"] for r in routes_list)
        total_inv = ac_cost + route_cost
        payback_days = total_inv / daily_rev if daily_rev > 0 else float('inf')
        roi = daily_rev * 365 / total_inv * 100 if total_inv > 0 else 0

        print(f"  Config: eco={cfg['eco']}  bus={cfg['bus']}  fir={cfg['fir']}  cargo={cfg['cargo']}")
        print(f"  Constraints: seats={seats:.0f}/{ac['pax']}  payload={payload:.1f}/{ac['tonnage']}T")
        print(f"  Waves: {waves} ({planes} aircraft)  |  Daily: ${daily_rev:,.0f}  |  Weekly: ${weekly:,.0f}")
        print(f"  ── Investment ──")
        ac_line = f"  Aircraft: {planes} × ${ac_unit:,.0f} = ${planes * ac_unit:,.0f}"
        if discount:
            ac_line += f" → ${ac_cost:,.0f} ({discount:.0%} bulk discount)"
        print(ac_line)
        print(f"  Routes:   {len(routes_list)} routes = ${route_cost:,.0f}")
        print(f"  Total:    ${total_inv:,.0f}")
        pb_line = f"  Payback:  {payback_days:.1f} days"
        if payback_days < 9999:
            pb_line += f"  ({payback_days/30.44:.1f} months)"
        print(pb_line)
        print(f"  ROI:      {roi:.1f}%/year")

    print(f"\n  Demand min — eco:{mn['eco_d']:>7,}  bus:{mn['bus_d']:>5,}  fir:{mn['fir_d']:>4,}  cargo:{mn['cargo_d']:>5,}")
    print(f"  Demand max — eco:{mx['eco_d']:>7,}  bus:{mx['bus_d']:>5,}  fir:{mx['fir_d']:>4,}  cargo:{mx['cargo_d']:>5,}")
    print(f"  Demand ratio (min/max) — eco:{mn['eco_d']/mx['eco_d']:.2f}  bus:{mn['bus_d']/max(1,mx['bus_d']):.2f}  "
          f"fir:{mn['fir_d']/max(1,mx['fir_d']):.2f}  cargo:{mn['cargo_d']/max(1,mx['cargo_d']):.2f}")

    print()
    print(f"  {'IATA':<5} {'Destination':<28} {'Dist':>6} {'FT':>5}  {'Eco':>6} {'Bus':>4} {'Fir':>3} {'Cgo':>4}")
    print(f"  {'─' * 68}")
    for rd in sorted(routes_list, key=lambda r: -r["dist"]):
        print(f"  {rd['iata']:<5} {rd['name'][:27]:<28} {rd['dist']:>5}km {rd['ft']:>4.2f}h "
              f" {rd['eco_d']:>6,} {rd['bus_d']:>4,} {rd['fir_d']:>3,} {rd['cargo_d']:>4,}")
    print(f"  {'─' * 68}")
    print(f"  {'TOTAL':<5} {'':28} {'':>6} {total_time:>4.2f}h "
          f" {tot['eco_d']:>6,} {tot['bus_d']:>4,} {tot['fir_d']:>3,} {tot['cargo_d']:>4,}")

    # Phase 2 breakdown
    if cfg and breakdown:
        print()
        print(f"  {'IATA':<5} {'Class':<6} {'Seats':>5} {'Cap':>6} {'Dem':>6} {'Rem':>6} "
              f"{'Price':>7} {'SSPrc':>7} {'Fill%':>6} {'Revenue':>12}")
        print(f"  {'─' * 78}")
        for ri in breakdown:
            route_total = ri["rev"]
            first = True
            for ci in ri["classes"]:
                if ci["seats"] == 0:
                    continue
                fill_pct = (ci["filled"] / ci["dem"] * 100) if ci["dem"] > 0 else 0
                label = ri["iata"] if first else ""
                print(f"  {label:<5} {ci['name']:<6} {ci['seats']:>5} {ci['cap']:>6} {ci['dem']:>6} "
                      f"{ci['remaining']:>6} ${ci['price']:>6,.0f} ${ci['ss_price']:>6,.0f} "
                      f"{fill_pct:>5.1f}% ${ci['rev']:>11,.0f}")
                first = False
            print(f"  {'':5} {'TOTAL':<6} {'':>5} {'':>6} {'':>6} {'':>6} {'':>7} {'':>7} "
                  f"{'':>6} ${route_total:>11,.0f}")


# ═══════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(description="Circuit Planner — Revenue Optimized")
    p.add_argument("--hub", default="HKG")
    p.add_argument("--aircraft", nargs="+", required=True,
                   help="Aircraft aliases to compare (e.g. B742 B743 B744 B748)")
    p.add_argument("--circuits", type=int, default=10)
    p.add_argument("--owned-hubs", nargs="*", default=[])
    p.add_argument("--beam", type=int, default=1200)
    p.add_argument("--max-routes", type=int, default=150,
                   help="Pre-filter to top N routes by eco demand before beam search")
    p.add_argument("--candidates-per-ac", type=int, default=3,
                   help="Top N circuit candidates per aircraft before cross-comparison")
    p.add_argument("--comfort", type=float, default=500)
    p.add_argument("--speed", type=float, default=700)
    p.add_argument("--max-waves", type=int, default=30)
    p.add_argument("--overshoot", type=float, default=0.0,
                   help="Allow bus/fir/cargo capacity to exceed demand by this "
                        "fraction (e.g. 0.10 = 10%%). Eco stays strict.")
    p.add_argument("--match", type=float, default=0.9,
                   help="Min eco/cargo demand ratio (min/max) within a circuit (default: 0.9)")
    p.add_argument("--bulk-discount", type=float, default=0.13,
                   help="Bulk purchase discount rate (default: 0.13 = 13%%)")
    p.add_argument("--bulk-threshold", type=int, default=63,
                   help="Min aircraft count for bulk discount (default: 63)")
    p.add_argument("--min-dist", type=int, default=None,
                   help="Minimum route distance in km (e.g. 8000 to skip short routes)")
    p.add_argument("--max-dist", type=int, default=None,
                   help="Maximum route distance in km (e.g. 13800 to cap below ULR range)")
    p.add_argument("--exclude", nargs="*", default=[])
    p.add_argument("--exclude-routes", default=None,
                   help="File to persist excluded route IATAs across runs")
    p.add_argument("--phase1-only", action="store_true")
    p.add_argument("--save", action="store_true",
                   help="Persist resulting circuits to DB via save_circuit_full")
    args = p.parse_args()

    db = sqlite3.connect(DB)

    # Load aircraft
    aircraft_list = []
    for name in args.aircraft:
        ac = load_aircraft(db, name)
        if ac:
            aircraft_list.append(ac)
        else:
            print(f"Aircraft '{name}' not found, skipping")

    if not aircraft_list:
        print("No valid aircraft specified")
        return

    # Build exclusion set
    exclude_base = set(x.upper() for x in (args.exclude or []))

    # Hub exclusions: check DB if owned hub actually has a route TO this hub
    hub = args.hub.upper()
    owned = [h.upper() for h in (args.owned_hubs or [])]
    for oh in owned:
        has_route = db.execute(
            "SELECT 1 FROM routes WHERE hub_iata=? AND dest_iata=? LIMIT 1",
            (oh, hub)
        ).fetchone()
        if has_route:
            exclude_base.add(oh)
    exclude_base.add(hub)  # exclude self

    # Route exclusions from file (previous circuits)
    prev_locked = set()
    if args.exclude_routes and os.path.exists(args.exclude_routes):
        with open(args.exclude_routes) as f:
            prev_locked = set(l.strip().upper() for l in f
                              if l.strip() and not l.startswith("#"))
            exclude_base.update(prev_locked)
            if prev_locked:
                print(f"  Loaded {len(prev_locked)} locked routes from {args.exclude_routes}")

    print(f"{'=' * 80}")
    print(f"CIRCUIT PLANNER — Revenue Optimized")
    print(f"{'=' * 80}")
    print(f"Hub: {args.hub}  |  Comfort: {args.comfort}  |  Speed: {args.speed}")
    print(f"Circuits: {args.circuits}  |  Max waves: {args.max_waves}  |  Match: {args.match:.0%}")
    dist_filter = []
    if args.min_dist:
        dist_filter.append(f">={args.min_dist:,}km")
    if args.max_dist:
        dist_filter.append(f"<={args.max_dist:,}km")
    if dist_filter:
        print(f"Distance filter: {' and '.join(dist_filter)}")
    for ac in aircraft_list:
        print(f"  {ac['alias']:>4} — {ac['model']:<12} cat>={ac['cat']}  {ac['speed']}km/h  "
              f"range={ac['range']:,}km  pax={ac['pax']}  ton={ac['tonnage']}T")
    hub_excluded = exclude_base & set(owned)
    file_excluded = exclude_base - hub_excluded - {hub}
    if hub_excluded:
        print(f"Hub conflicts: {', '.join(sorted(hub_excluded))}")
    if file_excluded:
        print(f"Locked routes: {len(file_excluded)} from file")
    for ac in aircraft_list:
        routes = load_routes(db, args.hub, ac, exclude_iatas=exclude_base,
                             min_dist=args.min_dist, max_dist=args.max_dist)
        print(f"  {ac['alias']}: {len(routes)} eligible routes")
    print(f"{'=' * 80}")

    used_routes = set()
    all_circuits = []
    t0 = _time.time()

    for circuit_num in range(1, args.circuits + 1):
        current_exclude = exclude_base | used_routes

        # Phase 1: search per aircraft
        candidates = []
        for ac in aircraft_list:
            routes = load_routes(db, args.hub, ac, exclude_iatas=current_exclude,
                                 min_dist=args.min_dist, max_dist=args.max_dist)
            if len(routes) < 2:
                continue

            print(f"  {ac['alias']} ({len(routes)}r, top {args.max_routes})...",
                  end="", flush=True)
            results = search_circuits(
                routes, ac, args.comfort, args.speed,
                top_n=args.candidates_per_ac, beam_width=args.beam,
                max_routes=args.max_routes, max_waves=args.max_waves,
                match=args.match, overshoot_pct=args.overshoot,
            )
            if results:
                print(f" best=${results[0][0]:,.0f}/day", flush=True)
            else:
                print(" no results", flush=True)

            for score, total_time, indices in results:
                route_list = [routes[i] for i in indices]
                candidates.append((score, total_time, route_list, ac))

        if not candidates:
            print(f"\nCircuit #{circuit_num}: no viable circuits remaining")
            break

        # Pick best across aircraft
        candidates.sort(key=lambda x: -x[0])
        p1_score, total_time, route_list, best_ac = candidates[0]

        # Lock routes
        circuit_iatas = set(r["iata"] for r in route_list)
        used_routes |= circuit_iatas

        # Phase 2
        if not args.phase1_only:
            cfg, waves, daily_rev, breakdown = optimize_circuit(
                route_list, best_ac, comfort=args.comfort, speed=args.speed,
                max_waves=args.max_waves, overshoot_pct=args.overshoot,
            )
            print_circuit(circuit_num, best_ac, route_list, total_time,
                          cfg, waves, daily_rev, breakdown, p1_score,
                          bulk_discount=args.bulk_discount,
                          bulk_threshold=args.bulk_threshold)
            all_circuits.append((circuit_num, best_ac, route_list, total_time,
                                 p1_score, cfg, waves, daily_rev))
        else:
            print_circuit(circuit_num, best_ac, route_list, total_time, p1_score=p1_score)
            all_circuits.append((circuit_num, best_ac, route_list, total_time,
                                 p1_score, None, 0, 0))

    elapsed = _time.time() - t0

    # Summary
    print(f"\n{'=' * 80}")
    print(f"SUMMARY — {len(all_circuits)} circuits, {elapsed:.1f}s")
    print(f"{'=' * 80}")
    print()

    total_routes_used = 0
    total_weekly = 0
    total_investment = 0
    total_planes = 0
    ac_usage = {}

    if not args.phase1_only:
        print(f"  {'#':>2}  {'AC':<4}  {'Time':>7}  {'Rt':>2}  {'Waves':>5}  {'Planes':>6}  "
              f"{'Config':<22}  {'Weekly T/O':>14}  {'Investment':>14}  {'Payback':>9}  {'ROI/yr':>7}")
        print(f"  {'─' * 115}")

    for entry in all_circuits:
        num, ac = entry[0], entry[1]
        route_list, total_time = entry[2], entry[3]
        p1_score = entry[4]
        cfg, waves, daily_rev = entry[5], entry[6], entry[7]

        iatas = " ".join(r["iata"] for r in sorted(route_list, key=lambda r: -r["dist"]))
        total_routes_used += len(route_list)
        ac_usage[ac["alias"]] = ac_usage.get(ac["alias"], 0) + 1

        if cfg and not args.phase1_only:
            weekly = daily_rev * 7
            planes = waves * 7
            total_weekly += weekly
            total_planes += planes
            # Investment
            discount = args.bulk_discount if planes >= args.bulk_threshold else 0
            ac_cost = planes * ac["price"] * (1 - discount)
            route_cost = sum(r["price"] for r in route_list)
            total_inv = ac_cost + route_cost
            payback_days = total_inv / daily_rev if daily_rev > 0 else 0
            roi = daily_rev * 365 / total_inv * 100 if total_inv > 0 else 0
            total_investment += total_inv
            cfg_str = f"e{cfg['eco']} b{cfg['bus']} f{cfg['fir']} c{cfg['cargo']}"
            print(f"  #{num:>2}  {ac['alias']:<4}  {total_time:>6.2f}h  {len(route_list):>2}r  "
                  f"{waves:>5}w  {planes:>5}ac  {cfg_str:<22}  ${weekly:>13,.0f}  "
                  f"${total_inv:>13,.0f}  {payback_days:>7.1f}d  {roi:>6.1f}%")
        else:
            print(f"  #{num:>2}  {ac['alias']}  {total_time:>6.2f}h  {len(route_list):>2}r  "
                  f"est=${p1_score:>13,.0f}/day  {iatas}")

    print(f"\n  Routes used: {total_routes_used}")
    print(f"  Aircraft mix: {', '.join(f'{k}×{v}' for k, v in sorted(ac_usage.items()))}")
    if not args.phase1_only:
        print(f"  Total aircraft: {total_planes}")
        print(f"  Total Weekly Turnover: ${total_weekly:>14,.0f}")
        print(f"  Total Investment:      ${total_investment:>14,.0f}")
        overall_daily = total_weekly / 7 if total_weekly > 0 else 0
        overall_payback_days = total_investment / overall_daily if overall_daily > 0 else 0
        overall_roi = overall_daily * 365 / total_investment * 100 if total_investment > 0 else 0
        print(f"  Overall Payback:       {overall_payback_days:>10.1f} days ({overall_payback_days/30.44:.1f} months)")
        print(f"  Overall ROI:           {overall_roi:>10.1f}%/year")
    print(f"  Locked IATAs: {' '.join(sorted(used_routes))}")

    if args.exclude_routes and used_routes:
        all_locked = prev_locked | used_routes
        with open(args.exclude_routes, 'w') as f:
            for iata in sorted(all_locked):
                f.write(f"{iata}\n")
        print(f"  Exclusions file updated: {args.exclude_routes} ({len(all_locked)} total routes)")

    if args.save and not args.phase1_only:
        from db import save_circuit_full
        saved = []
        for entry in all_circuits:
            _num, ac, route_list, total_time = entry[0], entry[1], entry[2], entry[3]
            cfg, waves, daily_rev = entry[5], entry[6], entry[7]
            if not cfg or not waves:
                continue
            cdict = {
                "hub": args.hub, "ac": ac, "routes": route_list,
                "total_time": total_time, "cfg": cfg, "waves": waves,
                "daily_rev": daily_rev, "weekly_rev": daily_rev * 7,
            }
            saved.append(save_circuit_full(cdict))
        print(f"  Saved circuits to DB: {len(saved)} ({', '.join(saved)})")

    db.close()


if __name__ == "__main__":
    main()
