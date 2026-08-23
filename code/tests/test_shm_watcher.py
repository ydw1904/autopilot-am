"""Decision logic for the SHM livery watcher.

Everything here is the part that runs before any money moves: price parsing,
which listing gets picked, and how a watchlist wider than one pass rotates.
No network, no shared DB — the schema is applied to a throwaway in-memory one.
"""

import sqlite3

import pytest

import shm_watcher as sw


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(sw.SCHEMA)
    return c


def listing(auction_id, skin_id, bin_price, *, model_id=134, name="Event Livery",
            own=False, concluded=False, time_left=30000, current=1_000_000):
    return {"id": auction_id, "currentPrice": current, "binPrice": bin_price,
            "isConcluded": concluded, "isPurchased": False,
            "timeLeft": time_left, "isAirlineSeller": own,
            "sellerPool": {"key": 3}, "countParticipant": 0,
            "aircraft": {"aircraftListId": model_id,
                         "skin": {"id": skin_id, "name": name, "type": 1}}}


def watch(skin_id=113648, model_id=134, max_price=None, want=1, bought=0):
    return sw.Watch(skin_id, model_id, "Event Livery", max_price, want, bought)


# ── prices ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,expected", [
    ("1.2b", 1_200_000_000), ("850m", 850_000_000), ("$1.209b", 1_209_000_000),
    ("1_209_000_000", 1_209_000_000), ("500", 500), ("2K", 2_000),
])
def test_parse_money(text, expected):
    assert sw.parse_money(text) == expected


def test_parse_money_rejects_junk():
    with pytest.raises(Exception):
        sw.parse_money("cheap")


# ── choosing ────────────────────────────────────────────────────────────────
def test_picks_the_cheapest_buy_now_for_a_watched_livery():
    got, _ = sw.choose([listing(1, 113648, 900_000_000),
                        listing(2, 113648, 400_000_000),
                        listing(3, 113648, 600_000_000)], [watch()], fee_pct=0)
    assert [c.auction_id for c in got] == [2]


def test_ignores_liveries_that_are_not_watched():
    got, skipped = sw.choose([listing(1, 999999, 100)], [watch()], fee_pct=0)
    assert got == [] and skipped == []


def test_respects_the_price_cap():
    got, skipped = sw.choose([listing(1, 113648, 2_000_000_000)],
                             [watch(max_price=1_000_000_000)], fee_pct=0)
    assert got == []
    assert "over cap" in skipped[0].reason


def test_never_buys_our_own_listing():
    got, skipped = sw.choose([listing(1, 113648, 500, own=True)], [watch()],
                             fee_pct=0)
    assert got == [] and skipped[0].reason == "own listing"


def test_skips_listings_with_no_buy_now_price():
    # binPrice 0 means the only route in is an open bid war, which the watcher
    # deliberately will not enter.
    got, skipped = sw.choose([listing(1, 113648, 0)], [watch()], fee_pct=0)
    assert got == [] and skipped[0].reason == "no buy-now price"


@pytest.mark.parametrize("kwargs", [{"concluded": True}, {"time_left": 0}])
def test_skips_dead_listings(kwargs):
    got, skipped = sw.choose([listing(1, 113648, 500, **kwargs)], [watch()],
                             fee_pct=0)
    assert got == [] and skipped[0].reason == "already gone"


def test_fee_inflates_the_cost_but_not_the_cap_comparison():
    # The cap is read against the price on the market; the fee only shows up in
    # what the budget and balance guards are charged.
    got, _ = sw.choose([listing(1, 113648, 1_000_000_000)],
                       [watch(max_price=1_000_000_000)], fee_pct=20)
    assert got[0].bin_price == 1_000_000_000
    assert got[0].est_cost == 1_200_000_000


def test_a_satisfied_watch_stops_matching():
    got, _ = sw.choose([listing(1, 113648, 500)], [watch(want=1, bought=1)],
                       fee_pct=0)
    assert got == []


def test_one_candidate_per_livery_even_across_models():
    watches = [watch(skin_id=1, model_id=10), watch(skin_id=2, model_id=20)]
    got, _ = sw.choose([listing(1, 1, 900, model_id=10),
                        listing(2, 1, 300, model_id=10),
                        listing(3, 2, 700, model_id=20)], watches, fee_pct=0)
    assert sorted((c.watch.skin_id, c.bin_price) for c in got) == [(1, 300), (2, 700)]


# ── rotation ────────────────────────────────────────────────────────────────
def test_plan_models_is_least_recently_checked_first(conn):
    watches = [watch(skin_id=i, model_id=m) for i, m in enumerate([10, 20, 30])]
    conn.execute("INSERT INTO shm_model_checks (model_id, last_checked) "
                 "VALUES (30, '2020-01-01 00:00:00'), (10, '2030-01-01 00:00:00')")
    # 20 has never been checked at all, so it goes first, then the stalest.
    assert sw.plan_models(conn, watches, per_pass=2) == [20, 30]


def test_plan_models_caps_at_per_pass_and_zero_means_all(conn):
    watches = [watch(skin_id=i, model_id=i * 10) for i in range(1, 6)]
    assert len(sw.plan_models(conn, watches, per_pass=2)) == 2
    assert len(sw.plan_models(conn, watches, per_pass=0)) == 5


def test_plan_models_ignores_watches_with_no_model(conn):
    assert sw.plan_models(conn, [watch(model_id=None)], per_pass=5) == []


# ── limits ──────────────────────────────────────────────────────────────────
def test_bid_headroom_accounts_for_the_reserve():
    lim = sw.Limits(max_bids_per_day=20, reserve_bids=5, bids_used=12)
    assert lim.bids_left == 3


def test_spent_today_counts_only_real_buys(conn):
    conn.execute("INSERT INTO shm_buys (auction_id, est_cost, dry_run) "
                 "VALUES (1, 500, 0), (2, 700, 0), (3, 999, 1)")
    assert sw.spent_today(conn) == (2, 1200)
