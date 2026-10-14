"""per-playlist sync mode (#1704).

a mirror the user adds songs to from jellyfin has to be append-only, while a
daily mix has to reconcile or replace. one global setting can't serve both, so
a mirror (and a pipeline) can carry its own mode. null means the global one.
"""

import threading
from unittest.mock import MagicMock

import pytest
from flask import Flask, g

from database.music_database import MusicDatabase


@pytest.fixture()
def mdb(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


def _mirror(db):
    return db.mirror_playlist(
        source="spotify", source_playlist_id="p1", name="Mix",
        tracks=[{"track_name": "Song", "artist_name": "Artist", "source_track_id": "sp1"}],
        profile_id=1,
    )


def _client(db, monkeypatch, invalidated):
    # monkeypatch, never configure(), same reason as the #1219 tests
    import api.mirrored_playlists as mod
    monkeypatch.setattr(mod, "get_database", lambda: db)
    monkeypatch.setattr(mod, "_invalidate_mirror_sync_fingerprint", invalidated.append)
    app = Flask(__name__)

    @app.before_request
    def _stamp():
        g.is_admin = True

    app.register_blueprint(mod.create_blueprint())
    return app.test_client()


def test_route_sets_and_clears_the_mode(mdb, monkeypatch):
    pk = _mirror(mdb)
    invalidated = []
    c = _client(mdb, monkeypatch, invalidated)

    resp = c.patch(f"/api/mirrored-playlists/{pk}/preferences", json={"sync_mode": "append"})
    assert resp.status_code == 200, resp.data
    assert resp.get_json()["playlist"]["sync_mode"] == "append"
    # a mode change has to reach the next sync, not the unchanged-tracks skip
    assert invalidated == [pk]

    resp = c.patch(f"/api/mirrored-playlists/{pk}/preferences", json={"sync_mode": None})
    assert resp.status_code == 200, resp.data
    assert mdb.get_mirrored_playlist(pk)["sync_mode"] is None


def test_route_rejects_a_bogus_mode(mdb, monkeypatch):
    pk = _mirror(mdb)
    c = _client(mdb, monkeypatch, [])
    resp = c.patch(f"/api/mirrored-playlists/{pk}/preferences", json={"sync_mode": "nuke"})
    assert resp.status_code == 400
    assert mdb.get_mirrored_playlist(pk)["sync_mode"] is None


def test_same_mode_again_keeps_the_fingerprint(mdb, monkeypatch):
    pk = _mirror(mdb)
    mdb.update_mirrored_playlist_preferences(pk, profile_id=1, sync_mode="append")
    invalidated = []
    c = _client(mdb, monkeypatch, invalidated)
    resp = c.patch(f"/api/mirrored-playlists/{pk}/preferences", json={"sync_mode": "append"})
    assert resp.status_code == 200
    assert invalidated == []


def _sync_kwargs(db, mirror_id, config=None):
    from core.automation.handlers.sync_playlist import auto_sync_playlist

    started, calls = threading.Event(), []

    def _run_sync_task(*a, **k):
        calls.append(k)
        started.set()

    deps = MagicMock()
    deps.get_database.return_value = db
    deps.run_sync_task = _run_sync_task
    deps.load_sync_status_file.return_value = {}
    deps.config_manager.get_active_media_server.return_value = "navidrome"
    auto_sync_playlist({"playlist_id": str(mirror_id), **(config or {})}, deps)
    assert started.wait(5)
    return calls[0]


def test_stored_mode_reaches_the_sync_through_the_real_db(mdb):
    pk = _mirror(mdb)
    assert _sync_kwargs(mdb, pk)["sync_mode"] is None
    mdb.update_mirrored_playlist_preferences(pk, profile_id=1, sync_mode="append")
    assert _sync_kwargs(mdb, pk, {"sync_mode": "replace"})["sync_mode"] == "append"


def _run_sync_task_mode(monkeypatch, requested, configured):
    import api.source_playlists as sp

    seen = {}
    cm = MagicMock()
    cm.get.side_effect = lambda key, default=None: configured if key == "playlist_sync.mode" else default
    monkeypatch.setattr(sp, "config_manager", cm)
    monkeypatch.setattr(sp, "_tracks_with_mirrored_quality_profile", lambda pid, name, t, profile_id: (t, None))
    monkeypatch.setattr(sp, "_build_sync_deps", lambda: None)
    monkeypatch.setattr(sp._discovery_sync, "run_sync_task",
                        lambda *a, **k: seen.setdefault("mode", k["sync_mode"]))
    sp._run_sync_task("auto_mirror_1", "Mix", [], None, 1, "", requested)
    return seen["mode"]


def test_run_sync_task_resolves_the_mode(monkeypatch):
    assert _run_sync_task_mode(monkeypatch, None, "append") == "append"
    assert _run_sync_task_mode(monkeypatch, "replace", "append") == "replace"
    # a junk stored value never reaches the media server
    assert _run_sync_task_mode(monkeypatch, "nuke", "append") == "reconcile"


def test_the_mirrored_pipeline_hands_its_mode_to_each_sync(mdb):
    from core.playlists import pipeline
    from tests.automation.test_playlist_pipeline_folder_mode import _minimal_deps

    pk = _mirror(mdb)
    seen = []

    def sync_and_wishlist(deps, automation_id, playlists, *, sync_one_fn, **_k):
        for pl in playlists:
            sync_one_fn(pl)
        return {'synced': 0, 'skipped': 0, 'wishlist_queued': 0}

    for mode in ('replace', None):
        pipeline.run_mirrored_playlist_pipeline(
            {'playlist_id': str(pk), 'profile_id': 1, '_automation_id': 'mirrored_x',
             **({'sync_mode': mode} if mode else {})},
            _minimal_deps(get_database=lambda: mdb, run_playlist_discovery_worker=lambda *a, **k: None,
                          update_progress=lambda *_a, **_k: None),
            refresh_fn=lambda cfg, deps: {'refreshed': '1', 'errors': '0'},
            sync_one_fn=lambda cfg, deps: seen.append(cfg.get('sync_mode')) or {},
            sync_and_wishlist_fn=sync_and_wishlist,
        )
    assert seen == ['replace', None]
