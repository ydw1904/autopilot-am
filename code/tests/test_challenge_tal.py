import challenge_tal as ct


def test_takeoff_times_exact_fit_24h_and_wrap():
    # Tuesday 03:25:19 -> first slot Tuesday 04:00 (86400 + 14400)
    slots = ct.takeoff_times("2026-09-15 03:25:19.000000", 24.0)
    assert slots[0] == 100800 and len(slots) == 7
    assert slots[-1] == (100800 + 6 * 86400) % ct.WEEK
    a320 = ct.takeoff_times("2026-09-15 03:25:19.000000", 13.75)
    assert len(a320) == 12 and all(s % 900 == 0 for s in a320)
    assert a320[-1] == (100800 + 11 * 49500) % ct.WEEK


def test_pick_offers_respects_cap_and_cards():
    free = {"template": "aircraft", "title": "A320neo - Challenge TAL Journey",
            "isAvailable": True, "remaining": 1, "purchaseCost": 0, "purchaseCurrency": "adv"}
    paid = {"template": "aircraft", "title": "A330-300 - Challenge TAL Journey",
            "isAvailable": True, "remaining": 1, "purchaseCost": 25000, "purchaseCurrency": "tc"}
    other = dict(paid, title="A330-300 - Copa")
    gone = dict(paid, remaining=0)
    picks = ct.pick_offers([free, paid, other, gone], a330_bought=1, max_a330=2, travel_cards=30000)
    assert [(o["title"][:4], buy) for o, buy in picks] == [("A320", False), ("A330", True)]
    assert ct.pick_offers([paid], 2, 2, 10**6) == []
    assert ct.pick_offers([paid], 0, 2, 24999) == []
