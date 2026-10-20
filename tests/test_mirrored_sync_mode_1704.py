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


def _client(db, monkeypatch):
    # monkeypatch, never configure(), same reason as the #1219 tests
    import api.mirrored_playlists as mod
    monkeypatch.setattr(mod, "get_database", lambda: db)
    app = Flask(__name__)

    @app.before_request
    def _stamp():
        g.is_admin = True

    app.register_blueprint(mod.create_blueprint())
    return app.test_client()


def test_route_sets_and_clears_the_mode(mdb, monkeypatch):
    pk = _mirror(mdb)
    c = _client(mdb, monkeypatch)

    resp = c.patch(f"/api/mirrored-playlists/{pk}/preferences", json={"sync_mode": "append"})
    assert resp.status_code == 200, resp.data
    assert resp.get_json()["playlist"]["sync_mode"] == "append"

    resp = c.patch(f"/api/mirrored-playlists/{pk}/preferences", json={"sync_mode": None})
    assert resp.status_code == 200, resp.data
    assert mdb.get_mirrored_playlist(pk)["sync_mode"] is None


@pytest.mark.parametrize("bad", ["nuke", False, 0, [], {}])
def test_route_rejects_anything_but_a_mode_or_null(mdb, monkeypatch, bad):
    pk = _mirror(mdb)
    mdb.update_mirrored_playlist_preferences(pk, profile_id=1, sync_mode="append")
    c = _client(mdb, monkeypatch)
    resp = c.patch(f"/api/mirrored-playlists/{pk}/preferences", json={"sync_mode": bad})
    assert resp.status_code == 400
    # a falsy junk value must not quietly clear it back to global
    assert mdb.get_mirrored_playlist(pk)["sync_mode"] == "append"


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
    # a junk stored value means the global mode, not a hardcoded one
    assert _run_sync_task_mode(monkeypatch, "nuke", "append") == "append"


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


def test_resolve_sync_mode():
    from core.sync.playlist_edit import resolve_sync_mode
    assert resolve_sync_mode("append", "replace") == "append"
    assert resolve_sync_mode(None, "replace") == "replace"
    assert resolve_sync_mode("Append", "replace") == "replace"
    assert resolve_sync_mode(None, None) == "reconcile"


# ── the unchanged-tracks skip has to notice a mode change ────────────────────

def _fast_path_run(db, pk, last_status, config=None, global_mode="reconcile"):
    """run the real handler with a status that matches the tracks, so only
    the mode can stop the skip. returns the result."""
    import hashlib

    from core.automation.handlers.sync_playlist import auto_sync_playlist

    h = hashlib.md5("sp1".encode()).hexdigest()
    status = {"tracks_hash": h, "mirror_tracks_hash": h, "matched_tracks": 1,
              "quality_profile_id": db.get_mirrored_playlist(pk).get("quality_profile_id"),
              **last_status}
    deps = MagicMock()
    deps.get_database.return_value = db
    deps.load_sync_status_file.return_value = {f"auto_mirror_{pk}": status}
    deps.config_manager.get.side_effect = (
        lambda key, default=None: global_mode if key == "playlist_sync.mode" else default)
    deps.config_manager.get_active_media_server.return_value = "navidrome"
    started = threading.Event()
    deps.run_sync_task.side_effect = lambda *a, **k: started.set()
    import core.sync.library_presence as lp
    orig = lp.matched_tracks_still_present
    lp.matched_tracks_still_present = lambda *_a, **_k: True
    try:
        return auto_sync_playlist({"playlist_id": str(pk), **(config or {})}, deps)
    finally:
        lp.matched_tracks_still_present = orig


def test_same_mode_still_skips(mdb):
    pk = _mirror(mdb)
    assert _fast_path_run(mdb, pk, {"sync_mode": "reconcile"})["status"] == "skipped"


def test_a_status_from_before_the_key_still_skips(mdb):
    # no upgrade burst: an old status ran in the global mode
    pk = _mirror(mdb)
    assert _fast_path_run(mdb, pk, {})["status"] == "skipped"


def test_a_new_mirror_mode_breaks_the_skip(mdb):
    pk = _mirror(mdb)
    mdb.update_mirrored_playlist_preferences(pk, profile_id=1, sync_mode="append")
    assert _fast_path_run(mdb, pk, {"sync_mode": "reconcile"})["status"] == "started"


def test_a_new_pipeline_mode_breaks_the_skip(mdb):
    pk = _mirror(mdb)
    result = _fast_path_run(mdb, pk, {"sync_mode": "reconcile"}, {"sync_mode": "replace"})
    assert result["status"] == "started"


def test_a_new_global_mode_breaks_the_skip(mdb):
    pk = _mirror(mdb)
    result = _fast_path_run(mdb, pk, {"sync_mode": "reconcile"}, global_mode="append")
    assert result["status"] == "started"


def test_the_status_writer_keeps_the_mode():
    """web_server._update_and_save_sync_status copies a fixed key list; a mode
    it drops would make every run look like a mode change."""
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[1].joinpath("web_server.py").read_text(encoding="utf-8")
    body = src.split("def _update_and_save_sync_status", 1)[1].split("\ndef ", 1)[0]
    keys = re.search(r"for key in \((.*?)\):", body, re.S).group(1)
    assert "'sync_mode'" in keys


# ── a re-sync from history keeps the mirror's own mode ───────────────────────

def _start(monkeypatch, db, payload, global_mode="reconcile", submitted_kwargs=None):
    import api.source_playlists as sp

    submitted = []

    class _Exec:
        def submit(self, fn, *args, **kwargs):
            submitted.append(args)
            if submitted_kwargs is not None:
                submitted_kwargs.append(kwargs)
            return MagicMock(done=lambda: True)

    cm = MagicMock()
    cm.get.side_effect = lambda key, default=None: global_mode if key == "playlist_sync.mode" else default
    monkeypatch.setattr(sp, "config_manager", cm)
    monkeypatch.setattr(sp, "sync_executor", _Exec())
    monkeypatch.setattr(sp, "sync_lock", threading.Lock())
    monkeypatch.setattr(sp, "sync_states", {})
    monkeypatch.setattr(sp, "active_sync_workers", {})
    monkeypatch.setattr(sp, "add_activity_item", lambda *a, **k: None)
    monkeypatch.setattr(sp, "get_current_profile_id", lambda: 1)
    import database.music_database as mdbmod
    monkeypatch.setattr(mdbmod, "get_database", lambda: db)
    app = Flask(__name__)
    with app.test_request_context():
        sp.start_playlist_sync_from_payload(payload)
    return submitted[0][6]


def test_history_resync_uses_the_mirrors_mode(mdb, monkeypatch):
    pk = _mirror(mdb)
    mdb.update_mirrored_playlist_preferences(pk, profile_id=1, sync_mode="append")
    base = {"playlist_id": f"auto_mirror_{pk}", "playlist_name": "Mix", "tracks": [{"name": "a"}]}
    # the dashboard sends '' (= no choice made)
    assert _start(monkeypatch, mdb, {**base, "sync_mode": ""}) == "append"
    assert _start(monkeypatch, mdb, base) == "append"
    # an explicit choice still wins
    assert _start(monkeypatch, mdb, {**base, "sync_mode": "replace"}) == "replace"


def test_a_non_mirror_sync_uses_the_global_mode(mdb, monkeypatch):
    payload = {"playlist_id": "spotify_xyz", "playlist_name": "Mix", "tracks": [{"name": "a"}]}
    assert _start(monkeypatch, mdb, payload, global_mode="replace") == "replace"


def test_history_resync_of_a_request_list_stays_a_request_list(mdb, monkeypatch):
    """a request list re-synced from history or the dashboard band goes through
    /api/sync/start, which used to drop the flag and build a server playlist."""
    pk = mdb.mirror_playlist(
        source="spotify", source_playlist_id="p1_req_ab12", name="Requests",
        tracks=[{"track_name": "Song", "artist_name": "Artist", "source_track_id": "sp1"}],
        profile_id=1, is_request_list=1, auto_remove_fulfilled=1,
    )
    kw = []
    payload = {"playlist_id": f"auto_mirror_{pk}", "playlist_name": "Requests", "tracks": [{"name": "a"}]}
    _start(monkeypatch, mdb, payload, submitted_kwargs=kw)
    assert kw[0]["is_request_list"] is True
    assert kw[0]["auto_remove_fulfilled"] is True


def test_history_resync_of_a_plain_mirror_is_not_a_request_list(mdb, monkeypatch):
    pk = _mirror(mdb)
    kw = []
    payload = {"playlist_id": f"auto_mirror_{pk}", "playlist_name": "Mix", "tracks": [{"name": "a"}]}
    _start(monkeypatch, mdb, payload, submitted_kwargs=kw)
    assert kw[0]["is_request_list"] is False
    assert kw[0]["auto_remove_fulfilled"] is False
