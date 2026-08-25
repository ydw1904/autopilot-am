"""Website orchestration for the three reward-livery feeds."""

import api_server
import booster_sync
import mobile_api
import mobile_store
import skin_name_sync


def test_sync_liveries_runs_all_reward_feeds_and_artwork(monkeypatch):
    calls = []
    snapshots = iter([
        {"booster_skins": 10, "shop_skins": 3, "challenge_skins": 1,
         "images": 8, "catalog_skins": 20},
        {"booster_skins": 12, "shop_skins": 5, "challenge_skins": 2,
         "images": 11, "catalog_skins": 24},
    ])

    class Session:
        def renew(self):
            calls.append("renew")

    class Client:
        def __init__(self, session, store):
            calls.append("client")

        def close(self):
            calls.append("close")

    monkeypatch.setattr(mobile_api.AMSession, "load", lambda: Session())
    monkeypatch.setattr(mobile_api, "AMClient", Client)
    monkeypatch.setattr(mobile_store, "MobileStore", lambda: object())
    monkeypatch.setattr(api_server, "_livery_sync_snapshot", lambda store: next(snapshots))
    monkeypatch.setattr(booster_sync, "sync_droprates", lambda *a, **k: calls.append("boosters"))
    monkeypatch.setattr(booster_sync, "sync_images", lambda *a, **k: calls.append("images"))
    monkeypatch.setattr(skin_name_sync, "sync_shop", lambda *a, **k: calls.append("shop"))
    monkeypatch.setattr(skin_name_sync, "sync_challenge", lambda *a, **k: calls.append("challenge"))
    monkeypatch.setattr(skin_name_sync, "classify_by_artwork", lambda *a, **k: calls.append("classify"))

    result = api_server.sync_liveries()

    assert calls == [
        "renew", "client", "boosters", "shop", "challenge", "images",
        "classify", "close",
    ]
    assert result["new_liveries"] == 4
    assert result["images_fetched"] == 3
    assert result["booster_skins"] == 12
    assert "shop" in result["message"]
