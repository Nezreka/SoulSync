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
        profile_id=1, source='spotify',
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


def _lib2_db(tmp_path):
    """A real Library v2 database: Pierce The Veil's album owned on disk, no
    media server has scanned it, plus a catalogue row with no file."""
    from database.music_database import MusicDatabase
    from tests import lib2_seed

    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        for number, t in enumerate(TRACKS, 1):
            lib2_seed.track(conn, 'Pierce The Veil', ALBUM['name'], t['name'], track_number=number)
        lib2_seed.track(conn, 'Pierce The Veil', 'Misadventures', 'Circles', track_number=1)
        lib2_seed.track(conn, 'Pierce The Veil', 'Misadventures', 'Texas Is Forever',
                        track_number=2, owned=False)
        conn.commit()
    return db


def test_preview_owns_what_library_v2_has_a_file_for(tmp_path):
    """Ownership is a live file in this library, not a row the active media
    server mapped: Discover → Your Albums showed a downloaded album as Missing
    on a server that had not scanned it, and Begin Analysis fetched it again."""
    db = _lib2_db(tmp_path)
    assert _preview_found(db) == {0: True, 1: True, 2: True}
    singles = [{'name': 'Circles', 'artists': ['Pierce The Veil']},
               {'name': 'Texas Is Forever', 'artists': ['Pierce The Veil']}]
    found = mw.preview_ownership(
        db, singles, album_context=None, artist_context=None, is_album=False,
        profile_id=1, source='spotify', allow_duplicates=True)
    assert [r['found'] for r in found] == [True, False]


def test_begin_analysis_owns_what_library_v2_has_a_file_for(tmp_path, monkeypatch):
    """the same album through Begin Analysis: found, so nothing is fetched again."""
    assert _analysis_found(_lib2_db(tmp_path), monkeypatch) == {0: True, 1: True, 2: True}


def test_preview_marks_a_missing_track_the_user_already_wants(tmp_path):
    """the one answer the dialog shows on open, so it carries the wishlist mark
    the Library v2 check used to paint before the preview overwrote it."""
    import json

    db = _lib2_db(tmp_path)
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO wishlist_tracks (spotify_track_id, spotify_data, profile_id) VALUES (?, ?, 1)",
            ('w1', json.dumps({'name': 'Texas Is Forever', 'artists': [{'name': 'Pierce The Veil'}]})))
        conn.commit()
    tracks = [{'name': name, 'artists': ['Pierce The Veil']}
              for name in ('Circles', 'Texas Is Forever', 'Bulls In The Bronx')]
    results = mw.preview_ownership(
        db, tracks, album_context=None, artist_context=None, is_album=False,
        profile_id=1, source='spotify', allow_duplicates=True)
    assert [(r['found'], r['in_wishlist']) for r in results] == [
        (True, False), (False, True), (False, False)]


def test_preview_asks_every_name_of_a_linked_artist(tmp_path):
    """the library linked the kanji row of a composer to his romaji one (§40
    artist aliases) and holds the soundtrack under the kanji name; Discover
    and Search ask with the romaji one and every track read as Missing."""
    from database.music_database import MusicDatabase
    from tests import lib2_seed

    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        canonical = lib2_seed.artist(conn, 'Sawano Hiroyuki')
        for number, title in enumerate(('Eye-Water', 'Vogel Im Kafig'), 1):
            lib2_seed.track(conn, '澤野弘之', 'Attack on Titan OST', title, track_number=number)
        lib2_seed.artist(conn, 'Hiroyuki Sawano')
        conn.execute("UPDATE lib2_artists SET canonical_artist_id = ? WHERE name IN (?, ?)",
                     (canonical, '澤野弘之', 'Hiroyuki Sawano'))
        conn.commit()
    tracks = [{'name': 'Eye-Water', 'artists': ['Sawano Hiroyuki']},
              {'name': 'Vogel Im Kafig', 'artists': ['Sawano Hiroyuki']}]
    album = mw.preview_ownership(
        db, tracks, album_context={'name': 'Attack on Titan OST', 'total_tracks': 2},
        artist_context={'name': 'Sawano Hiroyuki'}, is_album=True,
        profile_id=1, source='spotify', allow_duplicates=True)
    assert [r['found'] for r in album] == [True, True]
    single = mw.preview_ownership(
        db, [{'name': 'Eye-Water', 'artists': ['Hiroyuki Sawano']}], album_context=None,
        artist_context=None, is_album=False, profile_id=1, source='spotify', allow_duplicates=True)
    assert [r['found'] for r in single] == [True]
