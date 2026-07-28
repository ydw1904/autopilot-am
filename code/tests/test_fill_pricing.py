"""Fill pricing from auto_pricer — the price that drives remaining demand to 0.

Expected values are derived by hand from the game's demand model (flat at
maximum up to the ideal price, then linear with slope 3, zero at 4/3 x ideal),
not captured from a run of the code.
"""

import pytest

from auto_pricer import demand_factor, fill_price, fill_prices


# ── demand_factor: 1 at or below ideal, 1 - 3*(p/ideal - 1) above it ──

@pytest.mark.parametrize("ideal, price, expected", [
    (1000, 500, 1.0),           # far below ideal — demand is already maxed
    (1000, 1000, 1.0),          # exactly ideal
    (1000, 1100, 0.7),          # +10% price -> -30% demand
    (1000, 1200, 0.4),
    (1000, 4000 / 3, 0.0),      # zero-demand price
    (1000, 2000, 0.0),          # past it, clamped rather than negative
    (0, 500, 0.0),              # no ideal price -> no information
])
def test_demand_factor(ideal, price, expected):
    assert demand_factor(ideal, price) == pytest.approx(expected)


# ── fill_price: solve demand(p) == capacity ──

def test_fill_price_half_the_seats_needed_is_halfway_to_zero_demand():
    # capacity 500 of 1000 demand: markup = (1 - 1/2)/3 = 1/6 -> 1000 * 7/6
    assert fill_price(1000, 1000, 1000, 500) == 1166


def test_fill_price_capacity_equal_to_demand_is_the_ideal_price():
    assert fill_price(1000, 1000, 1000, 1000) == 1000


def test_fill_price_spare_capacity_is_the_ideal_price():
    # More seats than anyone wants: raising the price only sheds passengers.
    assert fill_price(1000, 1000, 1000, 5000) == 1000


def test_fill_price_no_seats_falls_back_to_ideal():
    # Nothing to fill, so nothing to solve; ideal is the neutral answer.
    assert fill_price(1000, 1000, 1000, 0) == 1000


def test_fill_price_capped_at_the_zero_demand_price():
    # Even one seat against huge demand cannot ask more than 4/3 x ideal.
    assert fill_price(1000, 1000, 10 ** 9, 1) == 1333


def test_fill_price_never_dips_below_ideal():
    assert fill_price(1000, 800, 1000, 999) >= 1000


def test_fill_price_unscales_demand_measured_above_ideal():
    # Observed 700 pax at 1100 (factor 0.7) means peak demand is 1000, so the
    # answer must match measuring 1000 pax at the ideal price itself.
    assert fill_price(1000, 1100, 700, 500) == fill_price(1000, 1000, 1000, 500)


def test_fill_price_rounds_down_to_keep_the_last_seats_sold():
    # Exact solution is 1000 * (1 + (1000-400)/3000) = 1200.0 - nudge capacity
    # so the true answer lands mid-integer and must floor.
    assert fill_price(1000, 1000, 1000, 401) == 1199


@pytest.mark.parametrize("ideal, cur_price, demand, capacity", [
    (0, 1000, 1000, 500),        # no ideal price known
    (1000, 1000, 0, 500),        # no demand at all
    (1000, 1400, 50, 10),        # priced past zero demand, yet demand reported
])
def test_fill_price_unsolvable_cases_return_none(ideal, cur_price, demand, capacity):
    assert fill_price(ideal, cur_price, demand, capacity) is None


# ── fill_prices: per class, falling back to ideal where unsolvable ──

def test_fill_prices_per_class():
    ideal = {"eco": 1000, "bus": 1330, "first": 2300, "cargo": 900}
    current = {"eco": 1000, "bus": 1330, "first": 2300, "cargo": 900}
    route = {
        "demand":  {"eco": 1000, "bus": 200, "first": 10, "cargo": 300},
        "carried": {"eco": 500,  "bus": 200, "first": 0,  "cargo": 150},
    }
    assert fill_prices(ideal, current, route) == {
        "eco":   1166,   # half the seats -> +1/6
        "bus":   1330,   # demand already met -> ideal
        "first": 2300,   # no seats -> ideal
        "cargo": 1050,   # half the tonnage -> +1/6
    }


def test_fill_prices_missing_masstool_classes_fall_back_to_ideal():
    ideal = {"eco": 1000, "bus": 1330, "first": 2300, "cargo": 900}
    assert fill_prices(ideal, dict(ideal), {}) == ideal
