from types import SimpleNamespace

import circuit_planner as planner


def test_plan_reranks_every_aircraft_candidate_with_phase2(monkeypatch):
    aircraft = {
        alias: {
            "alias": alias, "model": alias, "cat": 1, "speed": 700,
            "range": 20_000, "pax": 100, "tonnage": 10, "price": 1,
        }
        for alias in ("AC1", "AC2")
    }
    routes = {
        alias: [{
            "iata": f"{letter}{index}", "name": f"Route {letter}{index}",
            "dist": 1_000, "ft": 10, "eco_d": 1_000, "bus_d": 100,
            "fir_d": 10, "cargo_d": 100, "price": 1,
        } for letter in letters for index in range(2)]
        for alias, letters in (("AC1", "AB"), ("AC2", "CD"))
    }
    phase1 = {
        "AC1": [(100, 20, (0, 1)), (90, 20, (2, 3))],
        "AC2": [(95, 20, (0, 1)), (80, 20, (2, 3))],
    }
    revenue = {"A0": 100, "B0": 300, "C0": 200, "D0": 400}
    evaluated = []

    monkeypatch.setattr(planner, "get_db", object)
    monkeypatch.setattr(planner, "close_db", lambda: None)
    monkeypatch.setattr(planner, "load_aircraft", lambda _db, alias: aircraft[alias])
    monkeypatch.setattr(
        planner, "load_routes",
        lambda _db, _hub, ac, **_kwargs: routes[ac["alias"]],
    )
    monkeypatch.setattr(
        planner, "search_circuits",
        lambda _routes, ac, _comfort, _speed, **kwargs: (
            phase1[ac["alias"]] if kwargs["match"] == 0.9 else []),
    )

    def optimize(chosen, ac, **kwargs):
        evaluated.append((ac["alias"], chosen[0]["iata"], kwargs["wave_slack"]))
        return ({"eco": 100, "bus": 0, "fir": 0, "cargo": 0}, 1,
                revenue[chosen[0]["iata"]], [])

    monkeypatch.setattr(planner, "optimize_circuit", optimize)
    monkeypatch.setattr(planner, "print_circuit", lambda *_args, **_kwargs: None)

    result = planner._plan(SimpleNamespace(
        aircraft=["AC1", "AC2"], hub="MPM", circuits=1,
        owned_hubs=[], exclude=[], exclude_routes=None, ignore_saved=True,
        comfort=500, speed=700, max_waves=30, match=0.9,
        min_dist=None, max_dist=None, max_routes=150,
        candidates_per_ac=2, beam=1200, overshoot=0.0,
        phase1_only=False, wave_slack=0.02,
        bulk_discount=0.13, bulk_threshold=63, save=False,
    ))

    assert evaluated == [
        ("AC1", "A0", 0.02), ("AC1", "B0", 0.02),
        ("AC2", "C0", 0.02), ("AC2", "D0", 0.02),
    ]
    assert result["circuits"][0]["aircraft"] == "AC2"
    assert [route["iata"] for route in result["circuits"][0]["routes"]] == ["D0", "D1"]
    assert result["circuits"][0]["daily_rev"] == 400
