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


def watch(skin_id=113648, model_id=134, max_price=None, want=1, bought=0,
          source="manual"):
    return sw.Watch(skin_id, model_id, "Event Livery", max_price, want, bought,
                    source)


def add_catalog_tables(conn):
    conn.executescript("""
        CREATE TABLE mobile_skins (
            skin_id INTEGER PRIMARY KEY,
            model_id INTEGER,
            name TEXT,
            source TEXT
        );
        CREATE TABLE mobile_shop_offers (
            offer_id INTEGER PRIMARY KEY,
            template TEXT,
            currency TEXT
        );
        CREATE TABLE mobile_shop_offer_items (
            offer_id INTEGER,
            skin_id INTEGER
        );
        CREATE TABLE fleet (skin_id INTEGER);
        CREATE TABLE mobile_aircraft (skin_id INTEGER);
    """)


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


def test_balance_cost_is_the_submitted_buy_now_price():
    # purchaseFeePercent is not confirmed as a buyer charge, so the amount the
    # client posts is the only amount reserved from the balance.
    got, _ = sw.choose([listing(1, 113648, 1_000_000_000)],
                       [watch(max_price=1_000_000_000)], fee_pct=20)
    assert got[0].bin_price == 1_000_000_000
    assert got[0].est_cost == 1_000_000_000


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


def test_plan_next_model_alternates_paid_pack_and_background_lanes(conn):
    watches = [watch(skin_id=1, model_id=10, source=sw.PAID_PACK_SOURCE),
               watch(skin_id=2, model_id=20),
               watch(skin_id=3, model_id=30)]
    assert sw.plan_next_model(conn, watches, priority_turn=True) == 10
    assert sw.plan_next_model(conn, watches, priority_turn=False) == 20


def test_plan_next_model_uses_nonempty_lane_as_fallback(conn):
    watches = [watch(skin_id=1, model_id=10)]
    assert sw.plan_next_model(conn, watches, priority_turn=True) == 10


# Paid-pack target discovery
def test_sync_automatic_watches_lists_owned_paid_liveries_but_only_arms_missing_ones(conn):
    add_catalog_tables(conn)
    conn.executemany(
        "INSERT INTO mobile_skins VALUES (?,?,?,?)",
        [(1, 10, "Pack Special", "playrion"),
         (2, 20, "Factory Paint", "manufacturer"),
         (3, 30, "Owned Special", "playrion"),
         (4, 40, "Ad Special", "playrion"),
         (5, 50, "Manual Special", "playrion"),
         (6, 60, "Ticket Special", "playrion")],
    )
    conn.executemany(
        "INSERT INTO mobile_shop_offers VALUES (?,?,?)",
        # 'aircraft'/'tc' is the ticket aircraft, livery-exclusive like a pack.
        [(100, "pack", "realMoney"), (200, "gift", "adv"),
         (300, "aircraft", "tc")],
    )
    conn.executemany(
        "INSERT INTO mobile_shop_offer_items VALUES (?,?)",
        [(100, 1), (100, 2), (100, 3), (100, 5), (200, 4), (300, 6)],
    )
    conn.execute("INSERT INTO mobile_aircraft VALUES (3)")
    conn.execute(
        "INSERT INTO shm_watch (skin_id, model_id, label, max_price, source) "
        "VALUES (5, 50, 'My manual watch', 123, 'manual')")

    result = sw.sync_paid_pack_watches(conn)

    assert result["targets"] == 4
    assert result["models"] == 4
    assert result["added"] == 3
    rows = {r["skin_id"]: r for r in conn.execute(
        "SELECT skin_id, source, max_price, active, armed FROM shm_watch").fetchall()}
    assert rows[1]["source"] == sw.PAID_PACK_SOURCE
    assert rows[1]["max_price"] is None
    assert rows[1]["armed"] == 1
    assert rows[3]["active"] == 0
    assert rows[3]["armed"] == 0
    assert rows[5]["source"] == "manual"
    assert rows[5]["max_price"] == 123
    # A ticket aircraft is watched but never auto-armed: it is still on sale
    # for travel cards, so an uncapped market buy is rarely the better deal.
    assert rows[6]["source"] == sw.TICKET_SOURCE
    assert rows[6]["armed"] == 0
    assert 2 not in rows and 4 not in rows

    updated = sw.sync_paid_pack_watches(conn, max_price=456)
    assert updated["added"] == 0
    assert updated["updated"] == 3
    caps = dict(conn.execute(
        "SELECT skin_id, max_price FROM shm_watch").fetchall())
    assert caps == {1: 456, 3: 456, 5: 123, 6: 456}


def test_sync_automatic_watches_observes_special_am_gold_rewards_only(conn):
    add_catalog_tables(conn)
    conn.execute("ALTER TABLE mobile_shop_offers ADD COLUMN am_gold_step INTEGER")
    conn.executemany(
        "INSERT INTO mobile_skins VALUES (?,?,?,?)",
        [(7, 70, "SpaceJet-X100 - AM Gold Crew", "playrion"),
         (8, 80, "X380Plus - (Manufacturer livery)", "manufacturer")],
    )
    conn.executemany(
        "INSERT INTO mobile_shop_offers "
        "(offer_id, template, currency, am_gold_step) VALUES (?,?,?,?)",
        [(700, "gift", "gift or free", 6),
         (800, "gift", "gift or free", 8)],
    )
    conn.executemany("INSERT INTO mobile_shop_offer_items VALUES (?,?)",
                     [(700, 7), (800, 8)])

    result = sw.sync_automatic_watches(conn)

    assert result["targets"] == 1
    row = dict(conn.execute(
        "SELECT skin_id, source, active, armed FROM shm_watch").fetchone())
    assert row == {"skin_id": 7, "source": sw.GOLD_SOURCE,
                   "active": 1, "armed": 0}


def test_sync_automatic_watches_marks_acquired_managed_rows_inactive(conn):
    add_catalog_tables(conn)
    conn.executemany(
        "INSERT INTO mobile_skins VALUES (?,?,?,?)",
        [(1, 10, "Managed", "playrion"), (2, 20, "Manual", "playrion")],
    )
    conn.execute("INSERT INTO mobile_shop_offers VALUES (100, 'pack', 'realMoney')")
    conn.executemany("INSERT INTO mobile_shop_offer_items VALUES (100, ?)",
                     [(1,), (2,)])
    conn.execute(
        "INSERT INTO shm_watch (skin_id, model_id, label, source) "
        "VALUES (1, 10, 'Managed', ?), (2, 20, 'Manual', 'manual')",
        (sw.PAID_PACK_SOURCE,),
    )
    conn.executemany("INSERT INTO fleet VALUES (?)", [(1,), (2,)])

    result = sw.sync_paid_pack_watches(conn)

    assert result["disabled"] == 0
    states = {r["skin_id"]: (r["active"], r["bought"])
              for r in conn.execute("SELECT skin_id, active, bought FROM shm_watch")}
    assert states == {1: (0, 1), 2: (1, 0)}


def test_arming_an_acquired_watch_reopens_it_for_one_more_copy(conn):
    conn.execute(
        "INSERT INTO shm_watch (skin_id, model_id, label, source, want, bought, active, armed) "
        "VALUES (1, 10, 'Rare', ?, 1, 1, 0, 0)", (sw.PAID_PACK_SOURCE,))

    assert sw.set_watch_armed(conn, 1, True)

    row = dict(conn.execute(
        "SELECT active, armed, want, bought FROM shm_watch WHERE skin_id=1").fetchone())
    assert row == {"active": 1, "armed": 1, "want": 2, "bought": 1}
    # And the watcher's own selection now sees it again.
    assert [w.skin_id for w in sw.active_watches(conn)] == [1]


def test_arming_a_live_watch_leaves_its_target_count_alone(conn):
    conn.execute(
        "INSERT INTO shm_watch (skin_id, model_id, label, source, want, bought, active, armed) "
        "VALUES (2, 20, 'Wanted', 'manual', 3, 1, 1, 0)")

    sw.set_watch_armed(conn, 2, True)

    row = dict(conn.execute(
        "SELECT active, armed, want FROM shm_watch WHERE skin_id=2").fetchone())
    assert row == {"active": 1, "armed": 1, "want": 3}


def test_a_sync_does_not_undo_a_watch_armed_for_another_copy(conn):
    add_catalog_tables(conn)
    conn.executemany(
        "INSERT INTO mobile_skins VALUES (?,?,?,?)",
        [(1, 10, "Rearmed", "playrion"), (2, 20, "Settled", "playrion")],
    )
    conn.execute("INSERT INTO mobile_shop_offers VALUES (100, 'pack', 'realMoney')")
    conn.executemany("INSERT INTO mobile_shop_offer_items VALUES (100, ?)", [(1,), (2,)])
    conn.execute(
        "INSERT INTO shm_watch (skin_id, model_id, label, source, want, bought, active, armed) "
        "VALUES (1, 10, 'Rearmed', ?, 2, 1, 1, 1), (2, 20, 'Settled', ?, 1, 1, 0, 0)",
        (sw.PAID_PACK_SOURCE, sw.PAID_PACK_SOURCE),
    )
    conn.executemany("INSERT INTO fleet VALUES (?)", [(1,), (2,)])

    sw.sync_paid_pack_watches(conn)

    states = {r["skin_id"]: (r["active"], r["armed"], r["want"], r["bought"])
              for r in conn.execute(
                  "SELECT skin_id, active, armed, want, bought FROM shm_watch")}
    assert states == {1: (1, 1, 2, 1), 2: (0, 0, 1, 1)}


def test_a_pulled_offer_still_retires_a_watch_that_is_only_observing(conn):
    add_catalog_tables(conn)
    conn.execute("INSERT INTO mobile_skins VALUES (9, 90, 'Gone', 'playrion')")
    conn.execute(
        "INSERT INTO shm_watch (skin_id, model_id, label, source, want, bought, active, armed) "
        "VALUES (9, 90, 'Gone', ?, 1, 0, 1, 0)", (sw.PAID_PACK_SOURCE,))

    result = sw.sync_paid_pack_watches(conn)

    assert result["disabled"] == 1
    row = dict(conn.execute(
        "SELECT active, armed FROM shm_watch WHERE skin_id=9").fetchone())
    assert row == {"active": 0, "armed": 0}


def test_sync_automatic_watches_adds_challenge_rows_as_observe_only(conn):
    add_catalog_tables(conn)
    conn.execute("CREATE TABLE mobile_challenge_rewards (skin_id INTEGER)")
    conn.execute("INSERT INTO mobile_skins VALUES (7, 70, 'Challenge Special', 'playrion')")
    conn.execute("INSERT INTO mobile_challenge_rewards VALUES (7)")

    sw.sync_automatic_watches(conn)

    row = conn.execute("SELECT source, armed, active FROM shm_watch WHERE skin_id=7").fetchone()
    assert dict(row) == {"source": sw.CHALLENGE_SOURCE, "armed": 0, "active": 1}


def test_challenge_beats_a_pack_bundle_and_corrects_a_drifted_source(conn):
    """The Copa X777-9 case: awarded by a challenge, also inside a paid pack."""
    add_catalog_tables(conn)
    conn.execute("CREATE TABLE mobile_challenge_rewards (skin_id INTEGER)")
    conn.execute("INSERT INTO mobile_skins VALUES (7, 70, 'X777-9 - Challenge Copa', 'playrion')")
    conn.execute("INSERT INTO mobile_shop_offers VALUES (100, 'pack', 'realMoney')")
    conn.execute("INSERT INTO mobile_shop_offer_items VALUES (100, 7)")

    # Synced before the challenge started: the pack is the only feed that has it.
    sw.sync_automatic_watches(conn)
    assert conn.execute("SELECT source FROM shm_watch WHERE skin_id=7"
                        ).fetchone()["source"] == sw.PAID_PACK_SOURCE

    conn.execute("INSERT INTO mobile_challenge_rewards VALUES (7)")
    sw.sync_automatic_watches(conn)
    assert conn.execute("SELECT source FROM shm_watch WHERE skin_id=7"
                        ).fetchone()["source"] == sw.CHALLENGE_SOURCE


# ── limits ──────────────────────────────────────────────────────────────────
def test_bid_headroom_accounts_for_the_reserve():
    lim = sw.Limits(max_bids_per_day=20, reserve_bids=5, bids_used=12)
    assert lim.bids_left == 3


def test_spent_today_counts_only_real_buys(conn):
    conn.execute("INSERT INTO shm_buys (auction_id, est_cost, dry_run) "
                 "VALUES (1, 500, 0), (2, 700, 0), (3, 999, 1)")
    assert sw.spent_today(conn) == (2, 1200)


def test_limit_cache_obeys_ttl():
    cache = sw.LimitCache(ttl=600, limits=sw.Limits(), refreshed_at=100)
    assert not cache.due(now=699)
    assert cache.due(now=700)


def test_armed_limit_read_fails_closed_when_guards_are_unavailable(conn):
    class Client:
        def auction_rules(self):
            raise sw.AMError("offline")

        def my_bidding(self):
            raise sw.AMError("offline")

        def resources(self):
            raise sw.AMError("offline")

    with pytest.raises(sw.AMError, match="purchase guards unavailable"):
        sw.read_limits(Client(), conn, strict=True)


def test_empty_pass_does_not_read_account_limits(conn):
    conn.execute(
        "INSERT INTO shm_watch (skin_id, model_id, label, source) "
        "VALUES (1, 10, 'Wanted', 'manual')")

    class Client:
        def auctions(self, **kwargs):
            return []

        def auction_rules(self):
            raise AssertionError("limit read should not happen")

        def my_bidding(self):
            raise AssertionError("bidding read should not happen")

        def resources(self):
            raise AssertionError("balance read should not happen")

    result = sw.one_pass(
        Client(), conn, arm=False, per_pass=1, sweep=True, pool_only=False,
        reserve_bids=0, daily_budget=None, verbose=False)
    assert result["requests"] == 2
    assert result["bought"] == []


def test_dry_run_candidate_does_not_read_account_limits(conn):
    conn.execute(
        "INSERT INTO shm_watch (skin_id, model_id, label, max_price, source) "
        "VALUES (1, 10, 'Wanted', 1000, 'manual')")

    class Client:
        def auction_rules(self):
            raise AssertionError("limit read should not happen")

        def my_bidding(self):
            raise AssertionError("bidding read should not happen")

        def resources(self):
            raise AssertionError("balance read should not happen")

    result = sw.act_on_listings(
        Client(), conn, [listing(1, 1, 500, model_id=10)], arm=False,
        reserve_bids=0, daily_budget=None)
    assert result["candidates"] == 1
    assert result["bought"][0]["dry_run"] is True


def test_paid_pack_only_mode_buys_pack_and_observes_other_sources(conn):
    conn.executemany(
        "INSERT INTO shm_watch "
        "(skin_id, model_id, label, max_price, source) VALUES (?,?,?,?,?)",
        [(1, 10, "Paid Pack", 1000, sw.PAID_PACK_SOURCE),
         (2, 20, "Manual", 1000, "manual")],
    )

    class Session:
        player_id = 42

    class Client:
        s = Session()

        def __init__(self):
            self.bids = []

        def bid(self, auction_id, amount):
            self.bids.append((auction_id, amount))

        def my_bidding(self):
            return {"auctions": [{"id": 101, "isPurchased": True,
                                  "winner": {"id": 42}}]}

    client = Client()
    limits = sw.Limits(
        max_bids_per_day=20, fee_pct=0, balance=10_000,
        daily_budget=10_000,
    )
    result = sw.act_on_listings(
        client, conn,
        [listing(101, 1, 500, model_id=10, name="Paid Pack"),
         listing(202, 2, 600, model_id=20, name="Manual")],
        arm=True, arm_sources={sw.PAID_PACK_SOURCE}, reserve_bids=0,
        daily_budget=10_000, limits=limits,
    )

    assert client.bids == [(101, 500)]
    assert [(row["auction_id"], row["dry_run"]) for row in result["bought"]] == [
        (101, False), (202, True),
    ]
    states = dict(conn.execute(
        "SELECT skin_id, active FROM shm_watch").fetchall())
    assert states == {1: 0, 2: 1}


def test_armed_watch_can_buy_without_a_cap_when_balance_stays_positive(conn):
    conn.execute(
        "INSERT INTO shm_watch (skin_id, model_id, label, source, armed) "
        "VALUES (1, 10, 'Paid Pack', ?, 1)",
        (sw.PAID_PACK_SOURCE,),
    )

    class Client:
        class Session:
            player_id = 42
        s = Session()

        def bid(self, auction_id, amount):
            assert (auction_id, amount) == (101, 500)

        def my_bidding(self):
            return {"auctions": [{"id": 101, "isPurchased": True,
                                  "winner": {"id": 42}}]}

    result = sw.act_on_listings(
        Client(), conn, [listing(101, 1, 500, model_id=10)],
        arm=True, arm_sources={sw.PAID_PACK_SOURCE}, reserve_bids=0,
        daily_budget=None, limits=sw.Limits(balance=10_000, fee_pct=0),
    )

    assert result["bought"][0]["dry_run"] is False


def test_armed_watch_keeps_balance_strictly_positive(conn):
    conn.execute("INSERT INTO shm_watch (skin_id, model_id, label, armed) "
                 "VALUES (1, 10, 'Paid Pack', 1)")
    result = sw.act_on_listings(
        object(), conn, [listing(101, 1, 10_000, model_id=10)],
        arm=True, reserve_bids=0, daily_budget=None,
        limits=sw.Limits(balance=10_000, fee_pct=0), require_armed=True,
    )
    assert result["bought"] == []
    assert any("keeping a positive balance" in line for line in result["log"])


def test_watcher_lock_rejects_second_owner(tmp_path):
    lock_path = tmp_path / "watcher.lock"
    with sw.watcher_lock(str(lock_path)):
        with pytest.raises(sw.WatcherAlreadyRunning):
            with sw.watcher_lock(str(lock_path)):
                pass
