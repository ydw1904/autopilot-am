"""Multi-configuration aircraft purchase: row building and the 99 ceiling.

Offline. The ceiling is what matters: the game bills every configuration row
in ONE POST, so 98 + 55 is not "two purchases of under 99" — it is one
oversized purchase the form would clamp silently.
"""

import pytest

import aircraft_buyer as ab
from mobile_api import AMClient, BUY_LIMIT


BASE = dict(hub="LAX", name="X", eco=104, bus=32, first=23, cargo=19, qty=98)


def test_rows_inherit_unset_fields():
    rows = ab.build_configs(["bus=32,qty=98", "bus=55,qty=1"],
                            "LAX", "X", 104, 0, 23, 19, 99)
    assert [r["bus"] for r in rows] == [32, 55]
    assert [r["qty"] for r in rows] == [98, 1]
    # everything not named in the spec comes from the purchase-wide values
    assert all(r["eco"] == 104 and r["first"] == 23 and r["cargo"] == 19
               and r["hub"] == "LAX" for r in rows)


def test_no_config_strings_gives_one_row():
    assert ab.build_configs(None, "LAX", "X", 104, 32, 23, 19, 7) == [
        dict(BASE, bus=32, qty=7)]


def test_aliases_and_bad_keys():
    assert ab.parse_config("fir=5,crg=2,quantity=3") == {
        "first": 5, "cargo": 2, "qty": 3}
    with pytest.raises(ValueError):
        ab.parse_config("seats=5")
    with pytest.raises(ValueError):
        ab.parse_config("eco")


def test_web_purchase_refuses_over_limit_before_touching_the_page():
    class Boom:
        def __getattr__(self, name):
            raise AssertionError("must not touch the page")

    ok, msg = ab.configure_and_purchase(
        Boom(), [dict(BASE, qty=98), dict(BASE, bus=55, qty=2)])
    assert ok is False and "quantity_over_limit" in msg


def test_mobile_buy_refuses_over_limit():
    client = AMClient.__new__(AMClient)  # no session needed: it never posts
    cfg = dict(model_id=146, hub_id=1, quantity=50, name="X", skin_id=1,
               eco=104, bus=32, first=23, payload=19)
    with pytest.raises(ValueError, match=str(BUY_LIMIT)):
        client.buy_multiple(configs=[cfg, dict(cfg, quantity=50)])
