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


def _wish(db, name, profile_id, artist='Pierce The Veil'):
    import json

    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO wishlist_tracks (spotify_track_id, spotify_data, profile_id) VALUES (?, ?, ?)",
            (f'w-{name}', json.dumps({'name': name, 'artists': [{'name': artist}]}), profile_id))
        conn.commit()


def _wished_preview(db, names, profile_id=1):
    tracks = [{'name': name, 'artists': ['Pierce The Veil']} for name in names]
    results = mw.preview_ownership(
        db, tracks, album_context=None, artist_context=None, is_album=False,
        profile_id=profile_id, source='spotify', allow_duplicates=True)
    return [(r['found'], r['in_wishlist']) for r in results]


def test_preview_marks_a_missing_track_the_user_already_wants(tmp_path):
    """the one answer the dialog shows on open, so it carries the wishlist mark
    the Library v2 check used to paint before the preview overwrote it."""
    db = _lib2_db(tmp_path)
    _wish(db, 'Texas Is Forever', 1)
    assert _wished_preview(db, ('Circles', 'Texas Is Forever', 'Bulls In The Bronx')) == [
        (True, False), (False, True), (False, False)]


def test_preview_marks_the_wishlist_of_the_library_being_worked_in(tmp_path, monkeypatch):
    """wishlist entries belong to the library they fill (E-12): an admin who
    picked Guest's library sees Guest's marks, not the ones on their own list."""
    db = _lib2_db(tmp_path)
    _wish(db, 'Texas Is Forever', 2)
    _wish(db, 'Bulls In The Bronx', 1)
    monkeypatch.setattr('core.library_scope.acting_profile_id', lambda profile_id: 2)
    assert _wished_preview(db, ('Texas Is Forever', 'Bulls In The Bronx')) == [
        (False, True), (False, False)]


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
    # the lookups themselves, so the wishlist run, watchlist and completion
    # find it too, not only the dialog
    assert db.check_track_exists('Vogel Im Kafig', 'Sawano Hiroyuki', confidence_threshold=0.7)[0]
    assert db.check_album_exists_with_editions('Attack on Titan OST', 'Hiroyuki Sawano',
                                               confidence_threshold=0.7)[0]


def test_begin_analysis_asks_every_name_of_a_linked_artist(tmp_path, monkeypatch):
    from database.music_database import MusicDatabase
    from tests import lib2_seed

    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        canonical = lib2_seed.artist(conn, 'Sawano Hiroyuki')
        lib2_seed.track(conn, '澤野弘之', 'Attack on Titan OST', 'Eye-Water', track_number=1)
        conn.execute("UPDATE lib2_artists SET canonical_artist_id = ? WHERE name = ?",
                     (canonical, '澤野弘之'))
        conn.commit()
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)
    _seed_batch('P1', is_album_download=True,
                album_context={'name': 'Attack on Titan OST', 'total_tracks': 1},
                artist_context={'name': 'Sawano Hiroyuki'})
    deps = _build_deps(config=_FakeConfig({'wishlist.allow_duplicate_tracks': True}))

    mw.run_full_missing_tracks_process(
        'P1', 'artist_album_aot',
        [{'name': 'Eye-Water', 'artists': ['Sawano Hiroyuki']}], deps)

    assert [r['found'] for r in download_batches['P1']['analysis_results']] == [True]


def test_preview_marks_wishlist_entries_under_a_linked_artist_name(tmp_path):
    from database.music_database import MusicDatabase
    from tests import lib2_seed

    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        canonical = lib2_seed.artist(conn, 'Sawano Hiroyuki')
        lib2_seed.artist(conn, '澤野弘之')
        conn.execute("UPDATE lib2_artists SET canonical_artist_id = ? WHERE name = ?",
                     (canonical, '澤野弘之'))
        conn.commit()
    _wish(db, 'Barricades', 1, artist='澤野弘之')

    results = mw.preview_ownership(
        db, [{'name': 'Barricades', 'artists': ['Sawano Hiroyuki']}],
        album_context=None, artist_context=None, is_album=False,
        profile_id=1, source='spotify', allow_duplicates=True)

    assert [(r['found'], r['in_wishlist']) for r in results] == [(False, True)]


def test_preview_resolves_one_alias_group_once_per_artist(tmp_path, monkeypatch):
    from database.music_database import MusicDatabase
    from tests import lib2_seed

    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        canonical = lib2_seed.artist(conn, 'Sawano Hiroyuki')
        lib2_seed.artist(conn, '澤野弘之')
        conn.execute("UPDATE lib2_artists SET canonical_artist_id = ? WHERE name = ?",
                     (canonical, '澤野弘之'))
        conn.commit()
    real = db._artist_alias_names
    calls = []

    def counted(name):
        calls.append(name)
        return real(name)

    monkeypatch.setattr(db, '_artist_alias_names', counted)
    mw.preview_ownership(
        db,
        [{'name': f'Missing {i}', 'artists': ['Sawano Hiroyuki']} for i in range(3)],
        album_context=None, artist_context=None, is_album=False,
        profile_id=1, source='spotify', allow_duplicates=True)

    assert calls == ['Sawano Hiroyuki']
