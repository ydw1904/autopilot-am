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
