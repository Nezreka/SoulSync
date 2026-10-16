"""the album modal's ownership preview (SeadogsBooty: 'library status stays
pending'). it must give the same found/missing answer Begin Analysis gives,
and must not do anything the analysis does besides answering."""

from __future__ import annotations

import pytest

from core.downloads import master as mw
from core.runtime_state import download_batches
from tests.downloads.test_downloads_master import (  # noqa: F401 - reset_state, no_persisted_album_pins are autouse fixtures
    _build_deps,
    _DBAlbum,
    _DBTrack,
    _FakeConfig,
    _FakeDB,
    _seed_batch,
    no_persisted_album_pins,
    reset_state,
)


ALBUM = {'name': 'Collide With The Sky', 'total_tracks': 3}
ARTIST = {'name': 'Pierce The Veil'}
TRACKS = [
    {'name': 'Hell Above', 'artists': ['Pierce The Veil']},
    {'name': 'King For A Day', 'artists': [{'name': 'Pierce The Veil'}]},
    {'name': 'Hold On Till May', 'artists': ['Pierce The Veil']},
]


def _analysis_found(db, monkeypatch, *, allow_duplicates=True, tracks=TRACKS):
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)
    _seed_batch('P1', is_album_download=True, album_context=ALBUM, artist_context=ARTIST)
    deps = _build_deps(config=_FakeConfig({'wishlist.allow_duplicate_tracks': allow_duplicates}))
    mw.run_full_missing_tracks_process('P1', 'artist_album_x_y', tracks, deps)
    return {r['track_index']: r['found'] for r in download_batches['P1']['analysis_results']}


def _preview_found(db, *, allow_duplicates=True, tracks=TRACKS):
    results = mw.preview_ownership(
        db, tracks, album_context=ALBUM, artist_context=ARTIST, is_album=True,
        profile_id=1, source='spotify', active_server='plex',
        allow_duplicates=allow_duplicates,
    )
    return {r['track_index']: r['found'] for r in results}


@pytest.mark.parametrize('case', ['album_owned', 'album_partial', 'album_absent_dupes_off', 'album_absent_dupes_on'])
def test_preview_matches_begin_analysis(monkeypatch, case):
    allow_duplicates = True
    if case == 'album_owned':
        db = _FakeDB(album=_DBAlbum(1, 'Collide With The Sky'),
                     album_tracks=[_DBTrack(t['name']) for t in TRACKS])
    elif case == 'album_partial':
        db = _FakeDB(album=_DBAlbum(1, 'Collide With The Sky'),
                     album_tracks=[_DBTrack('Hell Above')])
    else:
        # the album isn't in the db; one track is owned elsewhere
        db = _FakeDB(found_tracks={('king for a day', 'pierce the veil'): 0.95})
        allow_duplicates = case == 'album_absent_dupes_on'

    expected = _analysis_found(db, monkeypatch, allow_duplicates=allow_duplicates)
    assert _preview_found(db, allow_duplicates=allow_duplicates) == expected


def test_preview_reports_owned_album_as_found():
    db = _FakeDB(album=_DBAlbum(1, 'Collide With The Sky'),
                 album_tracks=[_DBTrack(t['name']) for t in TRACKS])
    assert _preview_found(db) == {0: True, 1: True, 2: True}


def test_preview_honours_a_live_manual_match(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr('core.library.manual_library_match.get_match_for_track',
                        lambda *_a, **_k: {'library_track_id': 9})
    monkeypatch.setattr('core.library.manual_library_match.match_is_live', lambda *_a: True)
    tracks = [{'id': 'sp1', 'name': 'Anything', 'artists': ['Pierce The Veil']}]
    assert _preview_found(db, tracks=tracks) == {0: True}


def test_preview_ignores_a_stale_manual_match(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr('core.library.manual_library_match.get_match_for_track',
                        lambda *_a, **_k: {'library_track_id': 9})
    monkeypatch.setattr('core.library.manual_library_match.match_is_live', lambda *_a: False)
    tracks = [{'id': 'sp1', 'name': 'Anything', 'artists': ['Pierce The Veil']}]
    assert _preview_found(db, tracks=tracks) == {0: False}


def test_preview_keeps_the_modal_row_index():
    db = _FakeDB(album=_DBAlbum(1, 'Collide With The Sky'), album_tracks=[_DBTrack('Hold On Till May')])
    tracks = [{**TRACKS[2], '_original_index': 11}]
    assert _preview_found(db, tracks=tracks) == {11: True}


def test_route_answers_without_starting_a_batch(monkeypatch):
    web_server = pytest.importorskip("web_server")
    db = _FakeDB(album=_DBAlbum(1, 'Collide With The Sky'), album_tracks=[_DBTrack('Hell Above')])
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)
    monkeypatch.setattr(web_server, "get_current_profile_id", lambda: 1)
    web_server.app.config["TESTING"] = True
    r = web_server.app.test_client().post("/api/playlists/artist_album_x_y/ownership-preview", json={
        "tracks": TRACKS, "is_album_download": True,
        "album_context": ALBUM, "artist_context": ARTIST,
    })
    body = r.get_json()
    assert r.status_code == 200 and body["success"] is True
    assert [res["found"] for res in body["results"]] == [True, False, False]
    assert download_batches == {}


def test_route_rejects_an_empty_track_list():
    web_server = pytest.importorskip("web_server")
    web_server.app.config["TESTING"] = True
    r = web_server.app.test_client().post("/api/playlists/artist_album_x_y/ownership-preview", json={"tracks": []})
    assert r.status_code == 400
