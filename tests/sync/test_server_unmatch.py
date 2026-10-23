"""Server playlist unmatch: break a wrong pairing, keep the track in the playlist.

a user asked for a way to drop a wrong match on the compare view without the
"Remove from playlist" that deletes the server track. the pairing is filed as
rejected, and both the compare reconcile and the real sync honour it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import web_server
from core.sync.match_overrides import is_rejected_match, reject_match
from core.sync.playlist_reconcile import reconcile_playlist
from database.music_database import MusicDatabase


def _src(name, artist, sid):
    return {"name": name, "artist": artist, "source_track_id": sid}


def _svr(title, artist, tid):
    return {"title": title, "artist": artist, "id": tid}


def _pairs(combined):
    return [(c["match_status"],
             (c["source_track"] or {}).get("source_track_id"),
             (c["server_track"] or {}).get("id")) for c in combined]


# ── reconcile ───────────────────────────────────────────────────────────────

def test_rejected_exact_pair_reads_missing_plus_extra():
    combined = reconcile_playlist(
        [_src("Sanctuary", "TAYA.", "sp1")],
        [_svr("Sanctuary", "TAYA.", "77")],
        rejected_pairs={("sp1", "77")})
    assert _pairs(combined) == [("missing", "sp1", None), ("extra", None, "77")]


def test_rejected_fuzzy_pair_is_not_paired_either():
    # pass 1 misses on the title, pass 2 would pair them at > 0.75
    src = [_src("Sanctuary (Original Mix)", "TAYA.", "sp1")]
    svr = [_svr("Sanctuary Original Mix", "TAYA.", "77")]
    assert _pairs(reconcile_playlist(src, svr))[0] == ("matched", "sp1", "77")
    combined = reconcile_playlist(src, svr, rejected_pairs={("sp1", "77")})
    assert _pairs(combined) == [("missing", "sp1", None), ("extra", None, "77")]


def test_rejection_frees_the_source_for_another_copy():
    combined = reconcile_playlist(
        [_src("Sanctuary", "TAYA.", "sp1")],
        [_svr("Sanctuary", "TAYA.", "77"), _svr("Sanctuary", "TAYA.", "78")],
        rejected_pairs={("sp1", "77")})
    assert ("matched", "sp1", "78") in _pairs(combined)


def test_rejection_is_per_source_track():
    # the same server track still pairs with a source it was never unmatched from
    combined = reconcile_playlist(
        [_src("Sanctuary", "TAYA.", "sp2")],
        [_svr("Sanctuary", "TAYA.", "77")],
        rejected_pairs={("sp1", "77")})
    assert _pairs(combined) == [("matched", "sp2", "77")]


# ── reject_match against a real database ────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "unmatch.db"))


def test_reject_drops_the_cache_row_pointing_at_that_track(db):
    db.save_sync_match_cache("sp1", "sanctuary", "taya.", "navidrome", 77, "Sanctuary", 0.92)
    assert reject_match(db, 1, "navidrome", "sp1", "77")
    assert db.read_sync_match_cache("sp1", "navidrome") is None
    assert db.get_server_match_rejections(1, "navidrome", ["sp1"]) == {("sp1", "77")}


def test_reject_leaves_a_cache_row_for_another_track(db):
    db.save_sync_match_cache("sp1", "sanctuary", "taya.", "navidrome", 78, "Sanctuary", 1.0)
    reject_match(db, 1, "navidrome", "sp1", "77")
    assert db.read_sync_match_cache("sp1", "navidrome")["server_track_id"] == 78


def test_reject_drops_the_manual_match_only_when_it_is_that_track(db):
    db.save_manual_library_match(1, "spotify", "sp1", "77", server_source="navidrome")
    db.save_manual_library_match(1, "spotify", "sp2", "90", server_source="navidrome")
    reject_match(db, 1, "navidrome", "sp1", "77")
    reject_match(db, 1, "navidrome", "sp2", "77")
    assert db.find_manual_library_match_by_source_track_id(1, "sp1", "navidrome") is None
    assert db.find_manual_library_match_by_source_track_id(1, "sp2", "navidrome") is not None


def test_rejections_are_scoped_by_profile_and_server(db):
    reject_match(db, 1, "navidrome", "sp1", "77")
    assert is_rejected_match(db, 1, "navidrome", ["sp1"], 77)
    assert not is_rejected_match(db, 2, "navidrome", ["sp1"], 77)
    assert not is_rejected_match(db, 1, "plex", ["sp1"], 77)
    # any of the track's ids counts: the sync carries the discovered id too
    assert is_rejected_match(db, 1, "navidrome", ["disc9", "sp1"], "77")


def test_clear_undoes_a_rejection(db):
    reject_match(db, 1, "navidrome", "sp1", "77")
    assert db.clear_server_match_rejection(1, "navidrome", "sp1", "77") == 1
    assert not is_rejected_match(db, 1, "navidrome", ["sp1"], "77")


def test_reject_needs_both_ids(db):
    assert not reject_match(db, 1, "navidrome", "", "77")
    assert not reject_match(db, 1, "navidrome", "sp1", None)


# ── the endpoint ────────────────────────────────────────────────────────────

@pytest.fixture
def endpoint(monkeypatch, db):
    monkeypatch.setattr(web_server, "get_database", lambda: db)
    monkeypatch.setattr(web_server, "get_current_profile_id", lambda: 1)
    monkeypatch.setattr(web_server.config_manager, "get_active_media_server", lambda: "navidrome")
    monkeypatch.setattr(web_server, "_server_playlist_guard",
                        lambda server, pid, name: (None, pid, name, None))
    return lambda body: web_server.app.test_client().post(
        "/api/server/playlist/pl1/unmatch-track", json=body)


def test_endpoint_files_the_rejection(endpoint, db):
    resp = endpoint({"playlist_name": "Mix", "source_track_id": "sp1", "server_track_id": "77"})
    assert resp.status_code == 200 and resp.get_json()["success"] is True
    assert db.get_server_match_rejections(1, "navidrome", ["sp1"]) == {("sp1", "77")}


def test_endpoint_wants_both_ids(endpoint):
    resp = endpoint({"playlist_name": "Mix", "source_track_id": "sp1"})
    assert resp.status_code == 400


def test_endpoint_honours_the_playlist_guard(monkeypatch, endpoint, db):
    monkeypatch.setattr(web_server, "_server_playlist_guard",
                        lambda server, pid, name: (
                            None, pid, name, (web_server.jsonify({"success": False}), 403)))
    resp = endpoint({"playlist_name": "Mix", "source_track_id": "sp1", "server_track_id": "77"})
    assert resp.status_code == 403
    assert db.get_server_match_rejections(1, "navidrome", ["sp1"]) == set()


def test_find_and_add_of_the_same_track_clears_the_rejection(endpoint, db, monkeypatch):
    endpoint({"playlist_name": "Mix", "source_track_id": "sp1", "server_track_id": "77"})
    web_server._persist_find_and_add_match("sp1", "navidrome", "77", "Sanctuary", "Sanctuary", "TAYA.")
    assert db.get_server_match_rejections(1, "navidrome", ["sp1"]) == set()


# ── the sync honours it ─────────────────────────────────────────────────────

def test_sync_gate_reads_the_rejection(monkeypatch, db):
    import services.sync_service as ss
    monkeypatch.setattr("database.music_database.MusicDatabase", lambda *a, **k: db)
    reject_match(db, 1, "navidrome", "sp1", "77")
    svc = ss.PlaylistSyncService.__new__(ss.PlaylistSyncService)
    token = ss._sync_profile_id.set(1)
    try:
        track = SimpleNamespace(id="disc9", source_track_id="sp1")
        assert svc._match_was_rejected(track, "navidrome", 77)
        assert not svc._match_was_rejected(track, "navidrome", 78)
    finally:
        ss._sync_profile_id.reset(token)


# ── the search modal shows the track's own artist ───────────────────────────

def test_search_tracks_prefers_the_track_artist(monkeypatch):
    row = SimpleNamespace(
        id=1, title="Sanctuary", artist_name="Various Artists", track_artist="TAYA.",
        album_title="Fauna", album_thumb_url="", file_path="", bitrate=320,
        duration=437, server_source="navidrome")
    monkeypatch.setattr(web_server, "get_database",
                        lambda: SimpleNamespace(search_tracks=lambda **kw: [row]))
    monkeypatch.setattr(web_server.config_manager, "get_active_media_server", lambda: "navidrome")
    monkeypatch.setattr(web_server, "media_server_engine", SimpleNamespace(client=lambda n: None))
    track = web_server.app.test_client().get("/api/library/search-tracks?q=x").get_json()["tracks"][0]
    assert track["artist_name"] == "TAYA."
    assert track["album_artist"] == "Various Artists"
