"""Mobile pricing path: request pacing, cooldown parsing, target selection.

Offline — no session, no network. The two things worth pinning are the pacer
(a regression there is a burst of requests at the game server) and the
mobile-row → price-target mapping the pricer writes from.
"""

import datetime as dt
import time

import mobile_api
import mobile_pricer
from masstool import parse_mobile_lines

ROW = {
    "id": 63557170,
    "aOneName": "LAX",
    "aTwoName": "PVG",
    "price": {"eco": 2764, "bus": 3586, "first": 5970, "cargo": 6468},
    "demand": {"eco": 5171, "bus": 1198, "first": 419, "cargo": 1144},
    "carriedPax": {"eco": 4336, "bus": 1128, "first": 400, "cargo": 392},
    "remainingDemand": {"eco": 835, "bus": 70, "first": 19, "cargo": 752},
    "lockedUntil": "2025-07-02 00:07:24",
    "audit": {"reliability": 100,
              "price": {"eco": 3371, "bus": 4483, "first": 7753, "cargo": 7888},
              "demand": {"eco": 3775, "bus": 863, "first": 293, "cargo": 150}},
}


def test_pacer_spaces_requests():
    """Three calls cannot land inside one MIN_REQUEST_GAP."""
    pacer = mobile_api._Pacer()
    start = time.monotonic()
    for _ in range(3):
        pacer.wait()
    elapsed = time.monotonic() - start
    # Two gaps between three calls, each jittered down by at most REQUEST_JITTER.
    floor = 2 * mobile_api.MIN_REQUEST_GAP * (1 - mobile_api.REQUEST_JITTER)
    assert elapsed >= floor


def test_pacer_honours_client_floor():
    """A per-client min_delay above the global gap wins."""
    pacer = mobile_api._Pacer()
    start = time.monotonic()
    pacer.wait()
    pacer.wait(2.0)
    assert time.monotonic() - start >= 2.0 * (1 - mobile_api.REQUEST_JITTER)


def test_parse_mobile_lines_keeps_masstool_shape():
    data = parse_mobile_lines([ROW])
    assert set(data) == {"PVG"}
    row = data["PVG"]
    assert row["line_id"] == 63557170
    assert row["price"]["eco"] == 2764
    assert row["carried"]["bus"] == 1128
    assert row["remaining"]["cargo"] == 752
    # Extras the scraped HTML never carried.
    assert row["audit_price"]["first"] == 7753
    assert row["audit_demand"]["eco"] == 3775
    assert row["locked_until"] == "2025-07-02 00:07:24"


def test_audit_price_is_already_the_corrected_ideal():
    """bus == floor(eco*1.33) and first == floor(eco*2.3).

    This is why the mobile pricer targets audit.price directly instead of
    re-deriving bus/first the way auto_pricer must from the web page.
    """
    p = ROW["audit"]["price"]
    assert p["bus"] == int(p["eco"] * 1.33)
    assert p["first"] == int(p["eco"] * 2.3)


def test_is_locked_reads_the_cooldown():
    now = dt.datetime(2026, 8, 25, 12, 0, 0)
    assert mobile_pricer.is_locked("2026-08-26 03:10:06", now)
    assert not mobile_pricer.is_locked("2025-07-02 00:07:24", now)
    assert not mobile_pricer.is_locked(None, now)
    # Unparseable → treated as unlocked, so the server gets to refuse instead
    # of the tool silently pricing nothing.
    assert not mobile_pricer.is_locked("whenever", now)


def test_target_prices_by_mode():
    route = parse_mobile_lines([ROW])["PVG"]
    assert mobile_pricer.target_prices(route, "ideal", 100.0) == ROW["audit"]["price"]
    half = mobile_pricer.target_prices(route, "percent", 50.0)
    assert half["eco"] == round(3371 * 0.5)
    fill = mobile_pricer.target_prices(route, "fill", 100.0)
    # Fill never prices below the recommended price, nor past the zero-demand
    # point at 4/3 × ideal.
    for cls in ("eco", "bus", "first", "cargo"):
        assert route["audit_price"][cls] <= fill[cls] <= int(route["audit_price"][cls] * 4 / 3)


def test_target_prices_none_without_audit():
    row = dict(ROW, audit={"price": {}, "demand": {}})
    assert mobile_pricer.target_prices(parse_mobile_lines([row])["PVG"],
                                       "ideal", 100.0) is None
