"""Pure conversion checks for the mobile-first fleet sync."""

import pytest

import api_server
from api_server import SyncRequest, _mobile_items_to_fleet, _name_unknown_models


def test_mobile_fleet_matches_warehouse_contract_and_learns_models():
    items = [
        {"id": 101, "n": "FRA-C001-001", "al_id": 14, "as_id": 9001,
         "h_id": 9480309, "up": 100},
        {"id": 102, "n": "MPM-IDLE-001", "al_id": 14, "as_id": 9002,
         "h_id": 9535579, "up": 0},
    ]

    fleet, hubs, learned = _mobile_items_to_fleet(
        items,
        {9480309: "FRA", 9535579: "MPM"},
        {101: "A330-300"},
        {},
    )

    assert learned == {14: "A330-300"}
    assert hubs == {"FRA", "MPM"}
    assert fleet == [
        {"id": 101, "name": "FRA-C001-001", "model": "A330-300",
         "util": 100, "hub": "FRA", "skin_id": 9001},
        {"id": 102, "name": "MPM-IDLE-001", "model": "A330-300",
         "util": 0, "hub": "MPM", "skin_id": 9002},
    ]


def test_mobile_fleet_rejects_an_unknown_model_for_cdp_fallback():
    with pytest.raises(ValueError, match="incomplete mobile aircraft 101"):
        _mobile_items_to_fleet(
            [{"id": 101, "n": "NEW", "al_id": 999, "h_id": 9480309}],
            {9480309: "FRA"},
            {},
            {},
        )


def test_a_brand_new_model_is_named_from_its_profile_once():
    items = [
        {"id": 101, "al_id": 14, "h_id": 9480309},   # known from the fleet table
        {"id": 190108575, "al_id": 207, "h_id": 10242059},  # first of its type
        {"id": 190108576, "al_id": 207, "h_id": 10242059},  # same new model
    ]
    asked = []

    def profile(aircraft_id):
        asked.append(aircraft_id)
        return {"model": {"id": 207, "name": "VC10"}}

    model_by_id = {}
    _name_unknown_models(items, model_by_id, {101: "A330-300"}, profile)

    assert asked == [190108575]
    assert model_by_id == {207: "VC10"}

    fleet, _, _ = _mobile_items_to_fleet(
        items, {9480309: "FRA", 10242059: "JRO"}, {101: "A330-300"}, model_by_id)
    assert [ac["model"] for ac in fleet] == ["A330-300", "VC10", "VC10"]


def test_single_hub_sync_ignores_incomplete_records_at_other_hubs():
    fleet, hubs, learned = _mobile_items_to_fleet(
        [
            {"id": 101, "n": "FRA-C001", "al_id": 14, "h_id": 9480309, "up": 98},
            {"id": 999, "n": "OTHER", "al_id": 999, "h_id": 9535579, "up": 0},
        ],
        {9480309: "FRA", 9535579: "MPM"},
        {101: "A330-300"},
        {},
        requested_hub="FRA",
    )

    assert [item["id"] for item in fleet] == [101]
    assert hubs == {"FRA"}
    assert learned == {14: "A330-300"}


def _stub_sync_writes(monkeypatch):
    monkeypatch.setattr(api_server, "resolve_skin_ids", lambda fleet: (len(fleet), 0))
    monkeypatch.setattr(api_server, "upsert_fleet", lambda fleet, prune_hubs: None)
    monkeypatch.setattr(api_server, "fetch_missing_skin_images", lambda fleet: (0, 0))


def test_sync_uses_mobile_without_touching_cdp(monkeypatch):
    fleet = [{"id": 101, "skin_id": 9001}]
    monkeypatch.setattr(api_server, "_read_mobile_fleet", lambda hub: (fleet, ["FRA"]))
    monkeypatch.setattr(
        api_server, "_read_cdp_fleet",
        lambda hub, mobile_error=None: pytest.fail(
            "CDP fallback should not run after a successful mobile read"),
    )
    _stub_sync_writes(monkeypatch)

    result = api_server.sync_fleet(SyncRequest(hub="fra"))

    assert result["source"] == "mobile"
    assert "via mobile API" in result["message"]


def test_sync_falls_back_to_preserved_cdp_reader(monkeypatch):
    fleet = [{"id": 101, "skin_id": 9001}]
    monkeypatch.setattr(
        api_server, "_read_mobile_fleet",
        lambda hub: (_ for _ in ()).throw(OSError("mobile offline")),
    )
    seen = []
    monkeypatch.setattr(
        api_server, "_read_cdp_fleet",
        lambda hub, mobile_error=None: (seen.append((hub, str(mobile_error))) or fleet, [hub]),
    )
    _stub_sync_writes(monkeypatch)

    result = api_server.sync_fleet(SyncRequest(hub="fra"))

    assert seen == [("FRA", "mobile offline")]
    assert result["source"] == "cdp_fallback"
    assert "via browser fallback" in result["message"]
