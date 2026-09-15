"""route_details.parse_showline — the /network/showline scrape, offline.

The fixture is one real page (FRA/PVG, 2026-09-04) trimmed to the blocks the
parser reads. Every expected number below was read off that page by hand.
"""

import os

import pytest

from route_details import parse_showline

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "showline_fra_pvg.html")


@pytest.fixture(scope="module")
def page():
    with open(FIXTURE) as handle:
        return parse_showline(handle.read())


def test_the_information_box_is_read_off_the_page_not_guessed(page):
    assert page["route"].startswith("FRA - Frankfurt")
    assert page["purchased_at"] == "03/11/2024 at 23h01"
    assert page["aircraft"] == 12 and page["flights_per_week"] == 42
    assert page["distance_km"] == 8860 and page["taxes"] == 27031
    assert page["categories"] == [1, 10]
    assert page["departure"] == {"iata": "FRA", "country_code": "de", "country": "Germany"}
    assert page["arrival"]["iata"] == "PVG"


def test_today_and_yesterday_are_separate_tbodies_of_one_table(page):
    # The trap: both days live in <table id="resultTable">, so a parser that
    # stops at </table> reads yesterday's rows over today's.
    assert page["per_class"]["today"]["offer"] == {"eco": 1586, "bus": 456, "first": 178, "cargo": 114}
    assert page["per_class"]["yesterday"]["offer"] == {"eco": 3502, "bus": 462, "first": 196, "cargo": 128}
    assert page["per_class"]["today"]["turnover"]["eco"] == 5591587
    assert page["totals"]["today"]["turnover"] == 10238156
    assert page["totals"]["yesterday"]["flight_profit"] == 15845780
    assert page["totals"]["today"]["incidents"] == 0


def test_history_and_forecast_share_a_table_and_both_have_a_turnover_row(page):
    # Same trap again: the forecast's "Turnover :" row is the later one, so
    # without the split it silently replaces the six-day history.
    assert page["history"]["dates"][0] == "30/08/2026"
    assert page["history"]["dates"][-1] == "04/09/2026"
    assert page["history"]["rows"]["turnover"] == [17285834] * 5 + [10238156]
    assert page["history"]["rows"]["offer"] == [4160] * 5 + [2220]
    assert page["history"]["rows"]["result"][-1] == 9324832
    assert page["forecast"]["dates"][0] == "04/09/2026"
    assert page["forecast"]["turnover"] == [17285834] * 6


def test_the_seven_day_summary_keeps_the_sign_on_costs(page):
    week = page["week"]
    assert week["turnover"] == 121000838
    assert week["ticket_turnover"] == 103692106 and week["ancillary"] == 17308732
    assert week["flight_costs"] == -10080378 and week["fuel_costs"] == -6390559
    assert week["airport_taxes"] == -3689819 and week["other_costs"] == 0
    assert week["flight_result"] == 110920460 and week["incidents"] == 0
    # "24 514 Pax" is space-grouped, not comma-grouped, unlike every $ figure.
    assert week["pax_eco"] == 24514 and week["pax_bus"] == 3234
    assert week["pax_first"] == 1372 and week["cargo_tons"] == 896


def test_every_aircraft_box_is_read_including_the_flying_marker(page):
    fleet = page["aircraft_list"]
    assert len(fleet) == 12
    first = fleet[0]
    assert first == {
        "aircraft_id": 154461191, "model": "A319-100LR", "name": "SHOP-A319-100LR",
        "in_flight": True, "range_km": 9250, "use_pct": 97, "cargo_t": 0,
        "seats": {"total": 160, "eco": 160, "bus": 0, "first": 0},
        "hub": "FRA", "result": 7527947, "wear_pct": 16, "age": "5/5",
    }
    # The game prints a trailing "-" after some names; it is markup, not a name.
    assert all(not (item["name"] or "").endswith("-") for item in fleet)
    assert {item["model"] for item in fleet} >= {"A380-800", "747-8I", "777-300ER"}


def test_a_page_that_never_loaded_parses_to_empties_rather_than_raising():
    empty = parse_showline("<html><body>signed out</body></html>")
    assert empty["aircraft_list"] == [] and empty["taxes"] is None
    assert empty["history"]["rows"] == {} and empty["forecast"]["turnover"] is None
