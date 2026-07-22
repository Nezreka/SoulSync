"""The central Begin Analysis route materializes only Search intents."""

from __future__ import annotations

import pytest

web_server = pytest.importorskip("web_server")


class _CapturingExecutor:
    def __init__(self):
        self.calls = []

    def submit(self, *args):
        self.calls.append(args)


@pytest.fixture()
def route_context(monkeypatch):
    executor = _CapturingExecutor()
    real_config_get = web_server.config_manager.get
    monkeypatch.setattr(
        web_server.config_manager,
        "get",
        lambda key, default=None: (
            True if key == "features.library_v2" else real_config_get(key, default)
        ),
    )
    monkeypatch.setattr(web_server, "get_current_profile_id", lambda: 1)
    monkeypatch.setattr(web_server, "missing_download_executor", executor)
    monkeypatch.setattr(web_server, "_record_sync_history_start", lambda *args, **kwargs: None)
    web_server.app.config["TESTING"] = True
    yield web_server.app.test_client(), executor
    with web_server.tasks_lock:
        web_server.download_batches.clear()


def _payload():
    return {
        "tracks": [{
            "id": "sp-track-route",
            "name": "Route Track",
            "source": "spotify",
            "artists": [{"id": "sp-artist-route", "name": "Route Artist"}],
            "album": {"id": "sp-album-route", "name": "Route Album"},
        }],
        "playlist_name": "Route Album",
        "album_context": {
            "id": "sp-album-route",
            "name": "Route Album",
            "source": "spotify",
        },
        "artist_context": {
            "id": "sp-artist-route",
            "name": "Route Artist",
            "source": "spotify",
        },
        "quality_profile_id": 7,
    }


def test_enhanced_search_materializes_before_worker_submit(route_context, monkeypatch):
    client, executor = route_context
    calls = []

    def fake_materialize(conn, tracks, **kwargs):
        calls.append((list(tracks), kwargs))
        enriched = dict(tracks[0])
        enriched["lib2_track_id"] = 321
        return (enriched,)

    monkeypatch.setattr(
        "core.library2.confirmed_intent.materialize_confirmed_search_tracks",
        fake_materialize,
    )

    response = client.post(
        "/api/playlists/enhanced_search_track_route/start-missing-process",
        json=_payload(),
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert len(calls) == 1
    assert calls[0][1]["explicit_profile_id"] == 7
    assert calls[0][1]["correlation_id"] == body["batch_id"]
    assert executor.calls[0][3][0]["lib2_track_id"] == 321


def test_regular_playlist_keeps_existing_non_materializing_path(route_context, monkeypatch):
    client, executor = route_context

    def fail_if_called(*args, **kwargs):
        raise AssertionError("regular playlists must not be materialized here")

    monkeypatch.setattr(
        "core.library2.confirmed_intent.materialize_confirmed_search_tracks",
        fail_if_called,
    )
    payload = _payload()
    payload.pop("quality_profile_id")
    response = client.post(
        "/api/playlists/artist_album_route/start-missing-process",
        json=payload,
    )

    assert response.status_code == 200
    assert len(executor.calls) == 1
    assert "lib2_track_id" not in executor.calls[0][3][0]
