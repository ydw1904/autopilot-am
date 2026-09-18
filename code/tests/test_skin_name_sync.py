"""skin_name_sync helpers + the `mobile_skins.source` write rules.

Offline: the parsing helpers are pure, and the store tests run against an
in-memory DB, so nothing here touches Chrome or the mobile API.
"""

import sqlite3

import pytest

import mobile_store
import skin_name_sync as sns

CDN = "https://d2kp7o52pdueav.cloudfront.net"
PAINTER = "/common/images/painterPublic/skins/big/p-9358830-1-673deb3894c28.png"
GAME_ART = "/common/images/Aircrafts/skins/big/a220-100-r-challenge-xmas-2025.png"


@pytest.mark.parametrize("url, expected", [
    # the carousel's own shape: absolute, superBig, cache-busted
    (f"{CDN}/common/images/Aircrafts/skins/superBig/CS100-R.png?v201901010000",
     "/common/images/Aircrafts/skins/big/CS100-R.png"),
    (f"{CDN}/common/images/painterPublic/skins/superBig/p-1-2.png?v2019",
     "/common/images/painterPublic/skins/big/p-1-2.png"),
    # already the stored shape — must survive untouched
    (GAME_ART, GAME_ART),
    (None, None),
    ("", None),
])
def test_picture_path_normalises_to_the_stored_shape(url, expected):
    assert sns.picture_path(url) == expected


@pytest.mark.parametrize("name, path, expected", [
    ("LH-A388", PAINTER, "market"),
    ("A220-100-R - (Manufacturer livery)", GAME_ART, "manufacturer"),
    # a challenge livery: official artwork, but the page never says by whom —
    # left for the API passes and the artwork fallback
    ("A220-100-R - Challenge Xmas 2025", GAME_ART, None),
    (None, None, None),
])
def test_web_source_only_claims_what_the_page_proves(name, path, expected):
    assert sns.web_source(name, path) == expected


@pytest.fixture
def store():
    return mobile_store.MobileStore(sqlite3.connect(":memory:"))


def _source(store, skin_id):
    return store.conn.execute(
        "SELECT source FROM mobile_skins WHERE skin_id = ?", (skin_id,)
    ).fetchone()[0]


def test_manufacturer_is_never_downgraded(store):
    """The SHM calls a model's own paint `manufacturer`; the duty free lists the
    same livery in its Playrion bucket. The finer answer has to win regardless
    of which pass runs last."""
    store.upsert_skin(1, source="manufacturer")
    store.upsert_skin(1, source="playrion")
    assert _source(store, 1) == "manufacturer"

    store.upsert_skin(2, source="playrion")
    store.upsert_skin(2, source="manufacturer")
    assert _source(store, 2) == "manufacturer"


def test_source_fills_in_but_a_thin_read_does_not_blank_it(store):
    store.upsert_skin(3, name="X380Plus - Challenge Xmas 2025")
    assert _source(store, 3) is None
    store.upsert_skin(3, source="playrion")
    assert _source(store, 3) == "playrion"
    store.upsert_skin(3, source=None)          # e.g. a later web-carousel read
    assert _source(store, 3) == "playrion"


def test_market_and_playrion_still_correct_each_other(store):
    """Neither of the two shop buckets is privileged — only manufacturer is."""
    store.upsert_skin(4, source="playrion")
    store.upsert_skin(4, source="market")
    assert _source(store, 4) == "market"


def test_shop_counters_take_the_newest_value(store):
    store.upsert_skin(5, name="A319-LR Polygonal", price_amcoins=80, sold=12,
                      owned=0)
    store.upsert_skin(5, price_amcoins=60, sold=13, owned=1)
    row = store.conn.execute(
        "SELECT name, price_amcoins, sold, owned FROM mobile_skins "
        "WHERE skin_id = 5").fetchone()
    assert row == ("A319-LR Polygonal", 60, 13, 1)


def test_overview_view_exposes_the_new_columns(store):
    store.upsert_skin(6, name="787-8 - Greek", source="playrion",
                      price_amcoins=40, sold=7, owned=1)
    row = store.conn.execute(
        "SELECT name, source, price_amcoins, sold, owned "
        "FROM mobile_skin_overview WHERE skin_id = 6").fetchone()
    assert row == ("787-8 - Greek", "playrion", 40, 7, 1)


def test_ranking_ladder_names_liveries_nothing_else_hands_out(store):
    """The top brackets pay out liveries that are in no objective, no booster
    and no shop, so `challenge/{id}/ranking-rewards` is the only naming source
    for them. Rows land on the same table as the objective tracks, with the
    bracket in objective_id/goal."""
    n = store.record_ranking_rewards(595, {"rankingRewards": [
        {"rankMin": 1, "rankMax": 1, "reward": [
            {"id": 11, "effectType": "aircraft", "label": "X380Plus - Greek",
             "skin": {"id": 4344399, "name": "X380Plus - Greek",
                      "picturePath": {"big": "/common/images/Aircrafts/skins/"
                                             "big/x380plus-greek.png?v2019"}}},
            {"id": 12, "effectType": "amCoinBonus", "label": ":amc:5,000"}]},
        {"rankMin": 501, "rankMax": -1, "reward": [
            {"id": 12, "effectType": "amCoinBonus", "label": ":amc:10"}]},
        {"reward": [{"id": 13}]},          # no rank: not a bracket, skipped
    ]})
    assert n == 3
    rows = store.conn.execute(
        "SELECT objective_id, goal, reward_id, skin_id FROM "
        "mobile_challenge_rewards WHERE track = 'ranking' ORDER BY objective_id, "
        "reward_id").fetchall()
    assert [tuple(r) for r in rows] == [(1, 1, 11, 4344399), (1, 1, 12, None),
                                        (501, -1, 12, None)]
    # and the livery itself is now named, which is the whole point
    assert store.conn.execute(
        "SELECT name FROM mobile_skins WHERE skin_id = 4344399"
    ).fetchone()[0] == "X380Plus - Greek"


@pytest.mark.parametrize("bracket, expected", [
    ({"rankMin": 1, "rankMax": 1}, "top 1"),
    ({"rankMin": 11, "rankMax": 15}, "11-15"),
    ({"rankMin": 501, "rankMax": -1}, "501+"),
])
def test_bracket_label(bracket, expected):
    assert sns._bracket(bracket) == expected
