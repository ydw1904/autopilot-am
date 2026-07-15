"""Pricing / flight-time formulas from circuit_planner.

These encode the game's economics, so every expected value here was derived by
hand from the formula (and cross-checked with exact rational arithmetic), not
captured from a run of the code.
"""

import math

import pytest

from circuit_planner import (
    daily_turnover,
    flight_time_rt,
    ideal_bus,
    ideal_cargo,
    ideal_eco,
    ideal_fir,
    supersim_price,
)


# ── flight_time_rt: ceil((d/s + 1) * 2 * 4) / 4 — round trip, quarter-hour grid ──

@pytest.mark.parametrize("dist, speed, expected", [
    # (1000/500 + 1) * 2 = 6.0 — already on the grid
    (1000, 500, 6.0),
    # (11840/800 + 1) * 2 = 31.6 -> 126.4 quarters -> ceil 127 -> 31.75
    (11840, 800, 31.75),
    # (100/700 + 1) * 2 = 2.2857 -> 9.1428 quarters -> ceil 10 -> 2.5
    (100, 700, 2.5),
    # (3304/700 + 1) * 2 = 11.4400 -> 45.76 quarters -> ceil 46 -> 11.5
    (3304, 700, 11.5),
    # zero distance still pays the +1h turnaround on each leg
    (0, 700, 2.0),
])
def test_flight_time_rt(dist, speed, expected):
    assert flight_time_rt(dist, speed) == expected


def test_flight_time_rt_always_on_quarter_hour_grid():
    for dist in range(0, 12000, 137):
        ft = flight_time_rt(dist, 700)
        assert (ft * 4) % 1 == 0, f"{dist}km -> {ft}h is not a multiple of 0.25"


# ── ideal_eco: floor(100 + dist * 0.3 * (1 + comfort/3000)) ──────────────────

@pytest.mark.parametrize("dist, comfort, expected", [
    (0, 500, 100),        # floor(100 + 0) — base fare only
    (1000, 0, 400),       # floor(100 + 300 * 1)
    (1000, 500, 450),     # floor(100 + 300 * 1.16667) = floor(450.0)
    (11840, 500, 4244),   # floor(100 + 3552 * 1.16667) = floor(4244.0)
    (3304, 500, 1256),    # floor(100 + 991.2 * 1.16667) = floor(1256.4)
])
def test_ideal_eco(dist, comfort, expected):
    assert ideal_eco(dist, comfort) == expected


# ── ideal_bus = floor(eco * 1.33), ideal_fir = floor(eco * 2.3) ──────────────

@pytest.mark.parametrize("dist, comfort, expected", [
    (1000, 500, 598),     # floor(450 * 1.33) = floor(598.5)
    (0, 500, 133),        # floor(100 * 1.33) = floor(133.0)
    (11840, 500, 5644),   # floor(4244 * 1.33) = floor(5644.52)
    (3304, 500, 1670),    # floor(1256 * 1.33) = floor(1670.48)
])
def test_ideal_bus(dist, comfort, expected):
    assert ideal_bus(dist, comfort) == expected


@pytest.mark.parametrize("dist, comfort, expected", [
    (1000, 500, 1035),    # floor(450 * 2.3) = floor(1035.0)
    (11840, 500, 9761),   # floor(4244 * 2.3) = floor(9761.2)
    (3304, 500, 2888),    # floor(1256 * 2.3) = floor(2888.8)
])
def test_ideal_fir(dist, comfort, expected):
    assert ideal_fir(dist, comfort) == expected


@pytest.mark.xfail(
    strict=True,
    reason="Known float artifact: eco*2.3 lands just under an exact integer "
           "(100*2.3 == 229.99999999999997), so ideal_fir underprices by $1 "
           "whenever eco is a multiple of 10. Affects 797/20001 eco values in "
           "0..20000. Fixing it changes live prices, so it is tracked "
           "separately rather than patched under the test-suite ticket.",
)
def test_ideal_fir_exact_integer_boundary_underprices_by_one():
    # dist=0, comfort=500 -> eco = 100 -> exact math: 100 * 2.3 = 230
    assert ideal_fir(0, 500) == 230


def test_ideal_fir_and_bus_scale_off_eco():
    for dist, comfort in [(1000, 500), (5000, 250), (11840, 500)]:
        eco = ideal_eco(dist, comfort)
        assert ideal_bus(dist, comfort) == math.floor(eco * 1.33)
        assert ideal_fir(dist, comfort) == math.floor(eco * 2.3)


# ── ideal_cargo: ceil(200 + p*dist*(1 + speed/500)), p steps at 1999 / 5000 ──

@pytest.mark.parametrize("dist, speed, expected", [
    # p = 0.56 up to 1999km: ceil(200 + 0.56*1999*2.4) = ceil(2886.6) = 2887
    (1999, 700, 2887),
    # p drops to 0.52 at 2000km: ceil(200 + 0.52*2000*2.4) = ceil(2696.0) = 2696
    (2000, 700, 2696),
    # p = 0.52 through 5000km: ceil(200 + 0.52*5000*2.4) = ceil(6440.0) = 6440
    (5000, 700, 6440),
    # p drops to 0.47 at 5001km: ceil(200 + 0.47*5001*2.4) = ceil(5841.1) = 5842
    (5001, 700, 5842),
])
def test_ideal_cargo_distance_band_boundaries(dist, speed, expected):
    assert ideal_cargo(dist, speed) == expected


def test_ideal_cargo_price_drops_across_each_band_edge():
    """Crossing a band edge cuts the rate, so price falls despite +1km."""
    assert ideal_cargo(2000, 700) < ideal_cargo(1999, 700)
    assert ideal_cargo(5001, 700) < ideal_cargo(5000, 700)


# ── supersim_price: capacity under demand raises price on a 3x-demand slope ──

def test_supersim_price_capacity_below_demand_marks_up():
    # 1 - (50 - 100)/(3*100) = 1 + 50/300 = 1.16667 -> floor(1000 * 1.16667)
    assert supersim_price(1000, 50, 100) == 1166


def test_supersim_price_capacity_equal_demand_is_audit_price():
    assert supersim_price(1000, 100, 100) == 1000


def test_supersim_price_capacity_above_demand_is_audit_price():
    """Excess capacity never discounts below the audit price."""
    assert supersim_price(1000, 500, 100) == 1000


def test_supersim_price_markup_capped_at_one_third():
    """capacity -> 0 approaches audit * 4/3; it never exceeds it."""
    assert supersim_price(3000, 1, 100) == 3990   # floor(3000 * 1.33)
    assert supersim_price(3000, 1, 100) < 3000 * 4 / 3


@pytest.mark.parametrize("audit, capacity, demand", [
    (0, 100, 100),    # zero audit price -> no data, no price
    (1000, 100, 0),   # zero demand -> no price
    (0, 0, 0),
])
def test_supersim_price_zero_guards(audit, capacity, demand):
    assert supersim_price(audit, capacity, demand) == 0


def test_supersim_price_nonpositive_capacity_returns_audit_price():
    assert supersim_price(1000, 0, 100) == 1000
    assert supersim_price(1000, -5, 100) == 1000


# ── daily_turnover = min(demand, capacity) * supersim_price ──────────────────

def test_daily_turnover_capacity_below_demand():
    # seats sold = 50, price = 1166 (see supersim test above)
    assert daily_turnover(1000, 50, 100) == 50 * 1166


def test_daily_turnover_capacity_above_demand_sells_only_demand():
    # only 100 pax exist, price stays at audit
    assert daily_turnover(1000, 500, 100) == 100 * 1000


@pytest.mark.parametrize("audit, capacity, demand", [
    (1000, 0, 100),    # no seats
    (1000, 100, 0),    # no pax
    (0, 100, 100),     # no audit price
    (1000, -1, 100),   # negative seats
])
def test_daily_turnover_zero_guards(audit, capacity, demand):
    assert daily_turnover(audit, capacity, demand) == 0
