"""Mobile renamer: prefix matching, suffix handling, ordering, seat echo.

Offline — no session, no network. The rename goes through
`aircraft/reconfigure`, so the one thing that must never regress is that the
CURRENT seats are echoed back: wrong seats would both cost money and
reconfigure the aircraft instead of only renaming it.
"""

import mobile_renamer

FLEET = [
    {"id": 300, "name": "MPM-C003-009", "eco": 10, "bus": 2, "first": 1, "cargo": 5},
    {"id": 100, "name": "MPM-C003", "eco": 20, "bus": 4, "first": 0, "cargo": 7},
    {"id": 200, "name": "mpm-c003-12", "eco": 30, "bus": 0, "first": 3, "cargo": 9},
    {"id": 400, "name": "MPM-C0031", "eco": 1, "bus": 1, "first": 1, "cargo": 1},
    {"id": 500, "name": "HKG-C001-001", "eco": 1, "bus": 1, "first": 1, "cargo": 1},
]


def test_plan_matches_prefix_and_preserves_suffix():
    plan = mobile_renamer.build_plan(FLEET, "MPM-C003", "MPM-C012")
    # Ordered by aircraft id, and "MPM-C0031" / another circuit are not matches.
    assert [(ac["id"], new) for ac, new in plan] == [
        (100, "MPM-C012"),
        (200, "MPM-C012-12"),   # case-insensitive on the old prefix
        (300, "MPM-C012-009"),  # suffix preserved verbatim, not renumbered
    ]


def test_strip_suffix_drops_the_tail():
    plan = mobile_renamer.build_plan(FLEET, "MPM-C003", "MPM-C012",
                                     strip_suffix=True)
    assert {new for _, new in plan} == {"MPM-C012"}


def test_limit_takes_the_lowest_ids():
    plan = mobile_renamer.build_plan(FLEET, "MPM-C003", "MPM-C012", limit=2)
    assert [ac["id"] for ac, _ in plan] == [100, 200]


def test_rename_echoes_current_seats():
    calls = []

    class FakeClient:
        def reconfigure(self, aircraft_id, **kw):
            calls.append((aircraft_id, kw))
            return {"status": 1}

    mobile_renamer.rename(FakeClient(), FLEET[0], "MPM-C012-009")
    assert calls == [(300, {"name": "MPM-C012-009", "eco": 10, "bus": 2,
                            "first": 1, "payload": 5})]
