"""circuit_scheduler.build_flight_schedule — the weekly offset-rotation builder.

Routes are (dest_iata, flight_time_rt_hours, game_line_id). The builder walks
them sequentially from a per-aircraft day offset, snapping every takeoff to the
game's 15-minute (900s) grid and wrapping modulo the 604800s week.
"""

import pytest

from circuit_scheduler import GRANULARITY, WEEK_SECONDS, build_flight_schedule

DAY = 86400


def test_takeoff_times_are_multiples_of_900():
    # 6.0h and 5.5h round-trips: 21600s and 19800s, both already on the grid
    routes = [("CPT", 6.0, 101), ("LOS", 5.5, 102), ("ABV", 2.25, 103)]
    flights = build_flight_schedule(routes, 0)

    assert len(flights) == 3
    for f in flights:
        assert f["takeOffTime"] % GRANULARITY == 0


def test_takeoff_times_stay_on_grid_for_off_grid_flight_times():
    """Flight times that aren't multiples of 15 min still yield on-grid takeoffs."""
    routes = [("AAA", 1.1, 1), ("BBB", 2.7, 2), ("CCC", 0.3, 3)]
    flights = build_flight_schedule(routes, 0)

    assert len(flights) == 3
    for f in flights:
        assert f["takeOffTime"] % GRANULARITY == 0
        assert 0 <= f["takeOffTime"] < WEEK_SECONDS


def test_sequential_takeoffs_accumulate_round_trip_time():
    """Each leg departs one (grid-rounded) round-trip after the previous one."""
    routes = [("CPT", 6.0, 101), ("LOS", 5.5, 102)]
    flights = build_flight_schedule(routes, 0)

    # 6.0h -> 21600s; 5.5h -> 19800s
    assert flights == [
        {"takeOffTime": 0, "lineId": 101},
        {"takeOffTime": 21600, "lineId": 102},
    ]


@pytest.mark.parametrize("aircraft_index, expected_first_takeoff", [
    (0, 0),
    (1, 1 * DAY),
    (3, 3 * DAY),
    (6, 6 * DAY),
    (7, 0),        # index wraps every 7 aircraft: 7 % 7 == 0 -> back to Monday
    (9, 2 * DAY),  # 9 % 7 == 2
])
def test_day_offset_per_aircraft_index(aircraft_index, expected_first_takeoff):
    routes = [("CPT", 6.0, 101)]
    flights = build_flight_schedule(routes, aircraft_index)

    assert flights[0]["takeOffTime"] == expected_first_takeoff


def test_day_offset_shifts_every_leg_by_one_day():
    routes = [("CPT", 6.0, 101), ("LOS", 5.5, 102)]
    day0 = build_flight_schedule(routes, 0)
    day1 = build_flight_schedule(routes, 1)

    for a, b in zip(day0, day1):
        assert b["takeOffTime"] - a["takeOffTime"] == DAY
        assert a["lineId"] == b["lineId"]


def test_empty_route_list_returns_no_flights():
    assert build_flight_schedule([], 0) == []
    assert build_flight_schedule([], 5) == []


def test_routes_without_line_id_are_skipped():
    routes = [("CPT", 6.0, 101), ("NOID", 5.5, None), ("ABV", 2.0, 103)]
    flights = build_flight_schedule(routes, 0)

    assert [f["lineId"] for f in flights] == [101, 103]
    # the skipped route consumes no time: ABV departs right after CPT's 6h RT
    assert flights[1]["takeOffTime"] == 21600


def test_takeoff_wraps_into_the_week():
    """A Sunday-start aircraft continues into the next Monday slot."""
    # index 6 -> starts at 518400s (Sun 00:00); a 40h RT pushes past the week end
    routes = [("AAA", 40.0, 1), ("BBB", 2.0, 2)]
    flights = build_flight_schedule(routes, 6)

    assert flights[0]["takeOffTime"] == 6 * DAY
    # 518400 + 144000 = 662400 -> 662400 % 604800 = 57600 (Mon 16:00)
    assert flights[1]["takeOffTime"] == 57600
    assert all(0 <= f["takeOffTime"] < WEEK_SECONDS for f in flights)


def test_circuit_longer_than_a_week_stops_early():
    """Cumulative round-trip time >= 1 week ends the schedule."""
    # 100h RT each: after the 2nd leg total is 720000s >= 604800s -> break
    routes = [("AAA", 100.0, 1), ("BBB", 100.0, 2), ("CCC", 100.0, 3)]
    flights = build_flight_schedule(routes, 0)

    assert len(flights) == 2


def test_single_full_week_route_yields_one_flight():
    # 168h RT == exactly one week -> first leg is kept, then the guard trips
    flights = build_flight_schedule([("AAA", 168.0, 1), ("BBB", 1.0, 2)], 0)

    assert flights == [{"takeOffTime": 0, "lineId": 1}]


def test_fmt_time_wraps_past_sunday_into_monday():
    # A leg that runs past Sunday midnight belongs to the next Monday, not to
    # a second Sunday. The dry-run printer used to clamp it and show "Sun".
    from circuit_scheduler import fmt_time

    assert fmt_time(0) == "Mon 00:00"
    assert fmt_time(6 * DAY + 3600) == "Sun 01:00"
    assert fmt_time(WEEK_SECONDS + 10800) == "Mon 03:00"


def test_wrapped_leg_matches_what_gets_posted():
    # Tuesday-offset aircraft on a ~168h circuit: the last leg wraps to Monday.
    routes = [("YOL", 26.5, 1), ("MSZ", 25.75, 2), ("CBT", 25.5, 3),
              ("VPE", 25.0, 4), ("VFA", 22.5, 5), ("KRT", 21.75, 6),
              ("MWZ", 21.0, 7)]
    flights = build_flight_schedule(routes, 1)

    assert all(0 <= f["takeOffTime"] < WEEK_SECONDS for f in flights)
    assert flights[-1]["takeOffTime"] == 10800  # Mon 03:00, not a second Sunday
