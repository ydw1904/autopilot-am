"""finance.build: the Finances tab's maths, pinned to one real read (2026-09-18)."""

import json
import os

import pytest

from finance import build, income_tax

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "finance_2026-09-18.json")


@pytest.fixture(scope="module")
def raw():
    with open(FIXTURE) as handle:
        return json.load(handle)


def test_income_tax_reproduces_every_bracket_and_the_games_next_tax(raw):
    taxes = raw["taxes"]["summaryTaxes"]
    brackets = taxes["nextIncomeTaxDetails"]
    out = build(raw)["tax"]
    assert out["taxable"] == 32978946352
    assert out["gross"] == sum(b["airlineTax"] for b in brackets)
    assert abs(out["next"] - out["next_game"]) <= 1
    assert income_tax(0, brackets) == 0


def test_structural_profit_is_margin_less_the_weekly_fixed_charges(raw):
    out = build(raw)
    latest = out["days"][0]
    assert latest["date"] == "2026-09-17" and latest["structural"] == 2251918513
    # The recent days were charged at this week's rates, to the dollar.
    fixed_now = (out["week"]["income_tax_last"] + out["week"]["loans"]) / 7
    assert abs(latest["fixed"] - fixed_now) < 1


def test_ledger_net_matches_the_games_credit_and_debit_sums(raw):
    ledger = build(raw)["ledger"]
    assert ledger["dates"][0] == "2026-09-18"
    assert ledger["net"] == [c + d for c, d in zip(ledger["credit"], ledger["debit"])]


def test_statements_follows_the_pager_to_the_last_page():
    from finance import statements

    class Pager:
        def __init__(self):
            self.calls = []

        def _request(self, method, endpoint):
            self.calls.append(endpoint)
            page = int(endpoint.rsplit("/", 1)[1])
            return {"statements": [{"id": page}], "paging": {"pageCount": 3}}

    client = Pager()
    assert [r["id"] for r in statements(client, "today")] == [1, 2, 3]
    assert client.calls[0] == "finance/statements/today/true/1"


def test_fetch_reuses_the_cache_until_the_game_can_have_changed(tmp_path, monkeypatch):
    import db as dbmod
    from finance import fetch

    monkeypatch.setattr(dbmod, "DB", str(tmp_path / "finance.db"))
    monkeypatch.setattr(dbmod, "_conn", None)

    class Game:
        """Two rows a page, newest first; a new row lands before every read of
        today's first page. Everything else is a fixed payload."""

        last_resources = {"dollar": 1}

        def __init__(self):
            self.calls, self.today = [], [{"id": 0}, {"id": 12}, {"id": 11}, {"id": 10}]

        def _request(self, method, endpoint):
            self.calls.append(endpoint)
            if "/today/" in endpoint:
                page = int(endpoint.rsplit("/", 1)[1])
                if page == 1:
                    self.today.insert(1, {"id": self.today[1]["id"] + 1})
                return {"statements": self.today[2 * page - 2:2 * page],
                        "paging": {"pageCount": (len(self.today) + 1) // 2}}
            if "/yesterday/" in endpoint:
                return {"statements": [{"id": 5}], "paging": {"pageCount": 1}}
            return {}

    game, t = Game(), 1_000_000 * 86400 + 3600      # 01:00 UTC
    fetch(game, now=t)
    first = len(game.calls)
    assert first == 9                                 # 5 reads, 3 today pages, yesterday

    fetch(game, now=t + 60)                           # same quarter hour
    assert len(game.calls) == first

    raw = fetch(game, now=t + 900)                    # next quarter hour
    fresh = game.calls[first:]
    # Page 1 is the day line plus the new row, so page 2 is read to reach a
    # known id and the pager stops there; the rest comes from the cache.
    assert fresh == ["finance/cashFlow", "finance/accounting/history",
                     "finance/statements/today/true/1", "finance/statements/today/true/2"]
    ids = [r["id"] for r in raw["statements"]["statements"]]
    assert ids == [0, 14, 13, 12, 11, 10, 5] and raw["meta"]["mobile_calls"] == 4

    fetch(game, now=t + 960, force=True)              # Reload re-reads all
    assert len(game.calls) == first + 4 + 5 + 4 + 1   # 5 reads, 4 today pages, yesterday
