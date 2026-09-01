"""Mobile route purchase: owned-vs-failed classification and price lookup.

Offline. The one thing worth pinning is that "you already own it" counts as a
skip and not a failure — the CDP flow reported owned routes as FAIL, which
made a real failure indistinguishable from a no-op re-run.
"""

import circuit_route_buyer as crb
from mobile_api import AMError


class FakeClient:
    """Only the four methods buy_mobile touches."""

    def __init__(self, listed, owned):
        self.listed, self.owned, self.bought = listed, owned, []

    def resources(self):
        return {"dollar": 1_000_000_000}

    def world(self):
        return {"countryList": [{"c": "ma", "id": 122}]}

    def open_line_candidates(self, hub_id, country_id):
        return [{"iata": i, "price": {"final": p}} for i, p in self.listed.items()]

    def open_line(self, hub_id, iata):
        if iata in self.owned:
            raise AMError("line/open: status=0 message=You cannot buy this route, "
                          "because you already own it")
        if iata == "BAD":
            raise AMError("line/open: status=0 message=Not enough money")
        self.bought.append(iata)
        return {"status": 1}


def _routes(*iatas):
    return [(i, "ma", None) for i in iatas]


def test_owned_is_skip_not_fail(monkeypatch):
    monkeypatch.setattr(crb, "mark_route_owned", lambda *a: None)
    c = FakeClient({"RBA": 100}, owned={"FEZ"})
    ok, skip, fail = crb.buy_mobile(_routes("RBA", "FEZ"), 1, "CGK", False, client=c)
    assert [i for i, _ in ok] == ["RBA"]
    assert skip == ["FEZ"] and fail == []
    assert c.bought == ["RBA"]


def test_real_error_is_fail(monkeypatch):
    monkeypatch.setattr(crb, "mark_route_owned", lambda *a: None)
    c = FakeClient({}, owned=set())
    ok, skip, fail = crb.buy_mobile(_routes("BAD"), 1, "CGK", False, client=c)
    assert fail == ["BAD"] and ok == [] and skip == []


def test_dry_run_prices_and_buys_nothing():
    c = FakeClient({"RBA": 257_423_130}, owned=set())
    ok, skip, fail = crb.buy_mobile(_routes("RBA", "FEZ"), 1, "CGK", True, client=c)
    assert (ok, skip, fail) == ([], [], [])
    assert c.bought == []
