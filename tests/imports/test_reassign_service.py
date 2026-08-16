"""Album reassign service: source lookups, preview, apply.

The mapping rules are covered in test_reassign_album_mapping.py. This covers
the layer around them — that the pieces are actually wired to each other, and
that a source that misbehaves degrades instead of exploding.

The local side reads **Library v2**. It has to: the hint's ``replace_track_id``
is consumed by ``delete_replaced_track``, which resolves it against
``lib2_track_files.track_id``. Feeding it a legacy ``tracks.id`` would not fail
loudly — the two id spaces overlap, so it would delete a DIFFERENT track's
file. Hence the ``lib2:<id>`` subject contract, enforced in the service.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from core.imports import reassign_service as svc


class _Client:
    def __init__(self, artists=None, albums=None, tracks=None, raises=False):
        self._artists, self._albums, self._tracks, self._raises = artists, albums, tracks, raises

    def search_artists(self, query, limit=12):
        if self._raises:
            raise RuntimeError('source down')
        return self._artists or []

    def get_artist_albums(self, artist_id, limit=50):
        if self._raises:
            raise RuntimeError('source down')
        return self._albums or []

    def get_album_tracks(self, album_id):
        if self._raises:
            raise RuntimeError('source down')
        return {'tracks': self._tracks or []}


@pytest.fixture()
def db(tmp_path):
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE lib2_albums (id INTEGER PRIMARY KEY, title TEXT, "
                 "spotify_id TEXT, musicbrainz_id TEXT, external_ids TEXT DEFAULT '{}')")
    conn.execute("CREATE TABLE lib2_tracks (id INTEGER PRIMARY KEY, album_id INTEGER, "
                 "title TEXT, track_number INTEGER)")
    conn.execute("CREATE TABLE lib2_track_files (id INTEGER PRIMARY KEY, track_id INTEGER, "
                 "path TEXT, is_primary INTEGER DEFAULT 1, file_state TEXT DEFAULT 'active')")
    conn.commit()

    class _KeepOpen:
        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

        def close(self):
            pass

    return SimpleNamespace(_get_connection=lambda: _KeepOpen(conn), _raw=conn)


def _seed_track(db, track_id, title, track_number, path, *, album_id=7,
                file_state='active', is_primary=1):
    """One Library-v2 track of album 7, with (or without) a file."""
    db._raw.execute("INSERT OR IGNORE INTO lib2_albums (id, title) VALUES (?, 'The Album')",
                    (album_id,))
    db._raw.execute("INSERT INTO lib2_tracks (id, album_id, title, track_number) "
                    "VALUES (?, ?, ?, ?)", (track_id, album_id, title, track_number))
    if path is not None:
        db._raw.execute("INSERT INTO lib2_track_files (track_id, path, is_primary, file_state) "
                        "VALUES (?, ?, ?, ?)", (track_id, path, is_primary, file_state))
    db._raw.commit()


def _use(monkeypatch, client):
    monkeypatch.setattr(svc, '_client', lambda source: client)


# ── source lookups ───────────────────────────────────────────────────────────

def test_artist_search_returns_display_rows(monkeypatch):
    _use(monkeypatch, _Client(artists=[SimpleNamespace(id='A1', name='Pink Floyd', image_url='x')]))

    rows = svc.search_artists('spotify', 'pink floyd')

    assert rows == [{'id': 'A1', 'name': 'Pink Floyd', 'image_url': 'x'}]


def test_an_artist_without_an_id_is_dropped(monkeypatch):
    """An id-less row cannot be picked — offering it would dead-end the flow."""
    _use(monkeypatch, _Client(artists=[SimpleNamespace(id=None, name='Nameless')]))

    assert svc.search_artists('spotify', 'x') == []


def test_an_empty_query_does_not_hit_the_source(monkeypatch):
    called = []
    _use(monkeypatch, SimpleNamespace(search_artists=lambda *a, **k: called.append(1) or []))

    assert svc.search_artists('spotify', '   ') == []
    assert called == []


def test_a_source_that_raises_degrades_to_empty(monkeypatch):
    _use(monkeypatch, _Client(raises=True))

    assert svc.search_artists('spotify', 'x') == []
    assert svc.artist_albums('spotify', 'A1') == []
    assert svc.target_tracks('spotify', 'AL1') == []


def test_a_missing_client_degrades_to_empty(monkeypatch):
    monkeypatch.setattr(svc, '_client', lambda source: None)

    assert svc.search_artists('nope', 'x') == []
    assert svc.artist_albums('nope', 'A1') == []
    assert svc.target_tracks('nope', 'AL1') == []


# ── local side ───────────────────────────────────────────────────────────────

def test_local_tracks_exclude_files_that_are_not_on_disk(db):
    _seed_track(db, 1, 'Has File', 1, '/m/1.flac')
    _seed_track(db, 2, 'Wishlisted', 2, None)

    rows = svc.local_album_tracks(db, 'lib2:7')

    assert [r['title'] for r in rows] == ['Has File']


def test_local_tracks_carry_the_library_v2_track_id(db):
    """The id that ends up in the hint's ``replace_track_id``. A legacy id
    here would delete an unrelated track's file."""
    _seed_track(db, 4242, 'One', 1, '/m/1.flac')

    assert svc.local_album_tracks(db, 'lib2:7')[0]['id'] == 4242


def test_a_removed_file_is_not_offered(db):
    """``file_state`` other than active means the file is gone or quarantined
    — staging a copy of it is impossible."""
    _seed_track(db, 1, 'Deleted', 1, '/m/1.flac', file_state='deleted')

    assert svc.local_album_tracks(db, 'lib2:7') == []


def test_a_multi_file_track_is_listed_once(db):
    """An MP3 next to a FLAC is ONE track. Listing both would stage the same
    track twice and duplicate it under the new artist."""
    _seed_track(db, 1, 'One', 1, '/m/1.flac')
    db._raw.execute("INSERT INTO lib2_track_files (track_id, path, is_primary) "
                    "VALUES (1, '/m/1.mp3', 0)")
    db._raw.commit()

    rows = svc.local_album_tracks(db, 'lib2:7')

    assert len(rows) == 1
    assert rows[0]['file_path'] == '/m/1.flac', 'the primary file is the one to move'


def test_a_bare_id_is_refused(db):
    """The contract, enforced rather than trusted: an unprefixed id is a
    legacy row reference, and this flow can no longer act on one safely."""
    _seed_track(db, 1, 'One', 1, '/m/1.flac')

    assert svc.local_album_tracks(db, '7') == []


# ── preview ──────────────────────────────────────────────────────────────────

def test_preview_shows_the_mapping_and_why(monkeypatch, db):
    _seed_track(db, 1, 'One', 1, '/m/1.flac')
    _seed_track(db, 2, 'Bonus', 2, '/m/2.flac')
    _use(monkeypatch, _Client(tracks=[{'id': 'T1', 'name': 'One', 'track_number': 1}]))

    preview = svc.preview_reassign(db, 'spotify', 'lib2:7', 'AL9')

    assert preview['success'] is True
    assert preview['mapped_count'] == 1
    assert preview['unmapped_count'] == 1
    assert preview['pairings'][0]['matched_by'] == 'track_number'


def test_preview_refuses_an_album_with_no_files(monkeypatch, db):
    _use(monkeypatch, _Client(tracks=[{'id': 'T1', 'name': 'One', 'track_number': 1}]))

    preview = svc.preview_reassign(db, 'spotify', 'lib2:999', 'AL9')

    assert preview['success'] is False
    assert 'no files' in preview['error']


def test_preview_refuses_a_legacy_subject_with_a_reason(monkeypatch, db):
    """"That album has no files" would be a lie about a full album — the
    request named the wrong id space, and the message has to say so."""
    _seed_track(db, 1, 'One', 1, '/m/1.flac')
    _use(monkeypatch, _Client(tracks=[{'id': 'T1', 'name': 'One', 'track_number': 1}]))

    preview = svc.preview_reassign(db, 'spotify', '7', 'AL9')

    assert preview['success'] is False
    assert 'Library v2' in preview['error']


def test_preview_refuses_an_unreadable_tracklist(monkeypatch, db):
    _seed_track(db, 1, 'One', 1, '/m/1.flac')
    _use(monkeypatch, _Client(tracks=[]))

    preview = svc.preview_reassign(db, 'spotify', 'lib2:7', 'AL9')

    assert preview['success'] is False
    assert 'tracklist' in preview['error']


# ── apply ────────────────────────────────────────────────────────────────────

def test_apply_refuses_when_nothing_lines_up(monkeypatch, db, tmp_path):
    _seed_track(db, 1, 'Totally Different', 1, '/m/1.flac')
    _use(monkeypatch, _Client(tracks=[{'id': 'T1', 'name': 'Nothing Alike', 'track_number': 9}]))

    result = svc.apply_reassign(
        db, source='spotify', local_album_id='lib2:7', album_id='AL9', album_name='X',
        artist_id='AR9', artist_name='Y', album_type='album',
        staging_dir=str(tmp_path / 'staging'))

    assert result['success'] is False
    assert 'line up' in result['error']


def test_apply_refuses_a_legacy_subject(monkeypatch, db, tmp_path):
    _seed_track(db, 1, 'One', 1, '/m/1.flac')
    _use(monkeypatch, _Client(tracks=[{'id': 'T1', 'name': 'One', 'track_number': 1}]))

    result = svc.apply_reassign(
        db, source='spotify', local_album_id='7', album_id='AL9', album_name='X',
        artist_id='AR9', artist_name='Y', album_type='album',
        staging_dir=str(tmp_path / 'staging'))

    assert result['success'] is False
    assert 'Library v2' in result['error']
    assert not (tmp_path / 'staging').exists(), 'staged files despite refusing'


def test_apply_refuses_a_partial_move_by_default(monkeypatch, db, tmp_path):
    """Moving 8 of 12 files leaves 4 under the old artist — an album split
    across two artists, which is the exact problem this feature fixes. A
    caller that has not shown the user a preview cannot cause it by accident.
    """
    _seed_track(db, 1, 'One', 1, '/m/1.flac')
    _seed_track(db, 2, 'Bonus', 2, '/m/2.flac')
    _use(monkeypatch, _Client(tracks=[{'id': 'T1', 'name': 'One', 'track_number': 1}]))

    result = svc.apply_reassign(
        db, source='spotify', local_album_id='lib2:7', album_id='AL9', album_name='X',
        artist_id='AR9', artist_name='Y', album_type='album',
        staging_dir=str(tmp_path / 'staging'))

    assert result['success'] is False
    assert result['needs_confirmation'] is True
    assert result['mapped_count'] == 1 and result['unmapped_count'] == 1
    assert not (tmp_path / 'staging').exists(), 'staged files despite refusing'


def test_a_confirmed_partial_move_is_allowed(monkeypatch, db, tmp_path):
    """The user saw the preview and accepted it."""
    library = tmp_path / 'library'
    library.mkdir()
    for n in (1, 2):
        (library / f'{n}.flac').write_bytes(b'audio')
    _seed_track(db, 1, 'One', 1, f'{library}/1.flac')
    _seed_track(db, 2, 'Bonus', 2, f'{library}/2.flac')
    db._raw.execute("""CREATE TABLE rematch_hints (
        id INTEGER PRIMARY KEY, staged_path TEXT, content_hash TEXT, source TEXT,
        isrc TEXT, track_id TEXT, album_id TEXT, artist_id TEXT, track_title TEXT,
        album_name TEXT, artist_name TEXT, album_type TEXT, track_number INTEGER,
        disc_number INTEGER, replace_track_id INTEGER, exempt_dedup INTEGER,
        status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        consumed_at TIMESTAMP)""")
    db._raw.commit()
    _use(monkeypatch, _Client(tracks=[{'id': 'T1', 'name': 'One', 'track_number': 1}]))

    result = svc.apply_reassign(
        db, source='spotify', local_album_id='lib2:7', album_id='AL9', album_name='X',
        artist_id='AR9', artist_name='Y', album_type='album',
        staging_dir=str(tmp_path / 'staging'), allow_partial=True)

    assert result['success'] is True
    assert len(result['staged']) == 1
    assert [s['title'] for s in result['skipped']] == ['Bonus']


def test_the_hint_names_the_library_v2_track_to_replace(monkeypatch, db, tmp_path):
    """The whole point of the id contract, checked where it lands: the row
    ``delete_replaced_track`` will look up in ``lib2_track_files``."""
    library = tmp_path / 'library'
    library.mkdir()
    (library / '1.flac').write_bytes(b'audio')
    _seed_track(db, 4242, 'One', 1, f'{library}/1.flac')
    db._raw.execute("""CREATE TABLE rematch_hints (
        id INTEGER PRIMARY KEY, staged_path TEXT, content_hash TEXT, source TEXT,
        isrc TEXT, track_id TEXT, album_id TEXT, artist_id TEXT, track_title TEXT,
        album_name TEXT, artist_name TEXT, album_type TEXT, track_number INTEGER,
        disc_number INTEGER, replace_track_id INTEGER, exempt_dedup INTEGER,
        status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        consumed_at TIMESTAMP)""")
    db._raw.commit()
    _use(monkeypatch, _Client(tracks=[{'id': 'T1', 'name': 'One', 'track_number': 1}]))

    result = svc.apply_reassign(
        db, source='spotify', local_album_id='lib2:7', album_id='AL9', album_name='X',
        artist_id='AR9', artist_name='Y', album_type='album',
        staging_dir=str(tmp_path / 'staging'))

    assert result['success'] is True, result.get('error')
    stored = db._raw.execute("SELECT replace_track_id FROM rematch_hints").fetchall()
    assert [row[0] for row in stored] == [4242]


# ── the same-release guard (#889 invariant) ──────────────────────────────────

def test_reassigning_to_the_release_it_already_claims_is_refused(monkeypatch, db, tmp_path):
    """Every file would be restaged and re-imported to produce no change. The
    same-home guard stops it being destructive; this stops it being pointless
    and confusing. Enforced server-side because the API is callable directly."""
    _seed_track(db, 1, 'One', 1, '/m/1.flac')
    db._raw.execute("UPDATE lib2_albums SET spotify_id='AL9' WHERE id=7")
    db._raw.commit()
    _use(monkeypatch, _Client(tracks=[{'id': 'T1', 'name': 'One', 'track_number': 1}]))

    preview = svc.preview_reassign(db, 'spotify', 'lib2:7', 'AL9')
    applied = svc.apply_reassign(
        db, source='spotify', local_album_id='lib2:7', album_id='AL9', album_name='X',
        artist_id='AR9', artist_name='Y', album_type='album',
        staging_dir=str(tmp_path / 'staging'))

    assert preview['success'] is False and 'already assigned' in preview['error']
    assert applied['success'] is False and 'already assigned' in applied['error']


def test_the_guard_covers_the_source_the_request_names(monkeypatch, db):
    """Library v2 stores a MusicBrainz id of its own — the guard must read the
    column that belongs to the source being reassigned to, not only Spotify's."""
    _seed_track(db, 1, 'One', 1, '/m/1.flac')
    db._raw.execute("UPDATE lib2_albums SET musicbrainz_id='MB9' WHERE id=7")
    db._raw.commit()
    _use(monkeypatch, _Client(tracks=[{'id': 'T1', 'name': 'One', 'track_number': 1}]))

    same = svc.preview_reassign(db, 'musicbrainz', 'lib2:7', 'MB9')
    other = svc.preview_reassign(db, 'musicbrainz', 'lib2:7', 'MB-DIFFERENT')

    assert same['success'] is False and 'already assigned' in same['error']
    assert other['success'] is True


def test_a_long_tail_source_id_is_compared_too(monkeypatch, db):
    """Deezer/Tidal ids live in ``external_ids``, not in a column."""
    _seed_track(db, 1, 'One', 1, '/m/1.flac')
    db._raw.execute("""UPDATE lib2_albums SET external_ids='{"deezer": "DZ9"}' WHERE id=7""")
    db._raw.commit()
    _use(monkeypatch, _Client(tracks=[{'id': 'T1', 'name': 'One', 'track_number': 1}]))

    assert svc.preview_reassign(db, 'deezer', 'lib2:7', 'DZ9')['success'] is False


def test_a_different_release_by_the_same_artist_is_allowed(monkeypatch, db):
    """Only the SAME release is refused — moving between two of an artist's
    albums is a legitimate reassign."""
    _seed_track(db, 1, 'One', 1, '/m/1.flac')
    db._raw.execute("UPDATE lib2_albums SET spotify_id='AL9' WHERE id=7")
    db._raw.commit()
    _use(monkeypatch, _Client(tracks=[{'id': 'T1', 'name': 'One', 'track_number': 1}]))

    assert svc.preview_reassign(db, 'spotify', 'lib2:7', 'DIFFERENT')['success'] is True


def test_an_album_with_no_stored_source_id_is_not_blocked(monkeypatch, db):
    """A locally-imported album has no spotify id. It must still be reassignable."""
    _seed_track(db, 1, 'One', 1, '/m/1.flac')
    _use(monkeypatch, _Client(tracks=[{'id': 'T1', 'name': 'One', 'track_number': 1}]))

    assert svc.preview_reassign(db, 'spotify', 'lib2:7', 'AL9')['success'] is True
