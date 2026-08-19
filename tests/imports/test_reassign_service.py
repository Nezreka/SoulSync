"""Album reassign service: source lookups, preview, apply.

The mapping rules are covered in test_reassign_album_mapping.py. This covers
the layer around them — that the pieces are actually wired to each other, and
that a source that misbehaves degrades instead of exploding.
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
    conn.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, album_id TEXT, title TEXT, "
                 "track_number INTEGER, file_path TEXT)")
    conn.commit()

    class _KeepOpen:
        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

        def close(self):
            pass

    return SimpleNamespace(_get_connection=lambda: _KeepOpen(conn), _raw=conn)


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
    db._raw.execute("INSERT INTO tracks VALUES (1, 'AL', 'Has File', 1, '/m/1.flac')")
    db._raw.execute("INSERT INTO tracks VALUES (2, 'AL', 'Wishlisted', 2, NULL)")
    db._raw.commit()

    rows = svc.local_album_tracks(db, 'AL')

    assert [r['title'] for r in rows] == ['Has File']


# ── preview ──────────────────────────────────────────────────────────────────

def test_preview_shows_the_mapping_and_why(monkeypatch, db):
    db._raw.execute("INSERT INTO tracks VALUES (1, 'AL', 'One', 1, '/m/1.flac')")
    db._raw.execute("INSERT INTO tracks VALUES (2, 'AL', 'Bonus', 2, '/m/2.flac')")
    db._raw.commit()
    _use(monkeypatch, _Client(tracks=[{'id': 'T1', 'name': 'One', 'track_number': 1}]))

    preview = svc.preview_reassign(db, 'spotify', 'AL', 'AL9')

    assert preview['success'] is True
    assert preview['mapped_count'] == 1
    assert preview['unmapped_count'] == 1
    assert preview['pairings'][0]['matched_by'] == 'track_number'


def test_preview_refuses_an_album_with_no_files(monkeypatch, db):
    _use(monkeypatch, _Client(tracks=[{'id': 'T1', 'name': 'One', 'track_number': 1}]))

    preview = svc.preview_reassign(db, 'spotify', 'EMPTY', 'AL9')

    assert preview['success'] is False
    assert 'no files' in preview['error']


def test_preview_refuses_an_unreadable_tracklist(monkeypatch, db):
    db._raw.execute("INSERT INTO tracks VALUES (1, 'AL', 'One', 1, '/m/1.flac')")
    db._raw.commit()
    _use(monkeypatch, _Client(tracks=[]))

    preview = svc.preview_reassign(db, 'spotify', 'AL', 'AL9')

    assert preview['success'] is False
    assert 'tracklist' in preview['error']


# ── apply ────────────────────────────────────────────────────────────────────

def test_apply_refuses_when_nothing_lines_up(monkeypatch, db, tmp_path):
    db._raw.execute("INSERT INTO tracks VALUES (1, 'AL', 'Totally Different', 1, '/m/1.flac')")
    db._raw.commit()
    _use(monkeypatch, _Client(tracks=[{'id': 'T1', 'name': 'Nothing Alike', 'track_number': 9}]))

    result = svc.apply_reassign(
        db, source='spotify', local_album_id='AL', album_id='AL9', album_name='X',
        artist_id='AR9', artist_name='Y', album_type='album',
        staging_dir=str(tmp_path / 'staging'))

    assert result['success'] is False
    assert 'line up' in result['error']


def test_apply_refuses_a_partial_move_by_default(monkeypatch, db, tmp_path):
    """Moving 8 of 12 files leaves 4 under the old artist — an album split
    across two artists, which is the exact problem this feature fixes. A
    caller that has not shown the user a preview cannot cause it by accident.
    """
    db._raw.execute("INSERT INTO tracks VALUES (1, 'AL', 'One', 1, '/m/1.flac')")
    db._raw.execute("INSERT INTO tracks VALUES (2, 'AL', 'Bonus', 2, '/m/2.flac')")
    db._raw.commit()
    _use(monkeypatch, _Client(tracks=[{'id': 'T1', 'name': 'One', 'track_number': 1}]))

    result = svc.apply_reassign(
        db, source='spotify', local_album_id='AL', album_id='AL9', album_name='X',
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
    db._raw.execute(f"INSERT INTO tracks VALUES (1, 'AL', 'One', 1, '{library}/1.flac')")
    db._raw.execute(f"INSERT INTO tracks VALUES (2, 'AL', 'Bonus', 2, '{library}/2.flac')")
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
        db, source='spotify', local_album_id='AL', album_id='AL9', album_name='X',
        artist_id='AR9', artist_name='Y', album_type='album',
        staging_dir=str(tmp_path / 'staging'), allow_partial=True)

    assert result['success'] is True
    assert len(result['staged']) == 1
    assert [s['title'] for s in result['skipped']] == ['Bonus']


# ── the same-release guard (#889 invariant) ──────────────────────────────────

def test_reassigning_to_the_release_it_already_claims_is_refused(monkeypatch, db, tmp_path):
    """Every file would be restaged and re-imported to produce no change. The
    same-home guard stops it being destructive; this stops it being pointless
    and confusing. Enforced server-side because the API is callable directly."""
    db._raw.execute("CREATE TABLE albums (id TEXT PRIMARY KEY, spotify_album_id TEXT)")
    db._raw.execute("INSERT INTO albums VALUES ('AL', 'AL9')")
    db._raw.execute("INSERT INTO tracks VALUES (1, 'AL', 'One', 1, '/m/1.flac')")
    db._raw.commit()
    _use(monkeypatch, _Client(tracks=[{'id': 'T1', 'name': 'One', 'track_number': 1}]))

    preview = svc.preview_reassign(db, 'spotify', 'AL', 'AL9')
    applied = svc.apply_reassign(
        db, source='spotify', local_album_id='AL', album_id='AL9', album_name='X',
        artist_id='AR9', artist_name='Y', album_type='album',
        staging_dir=str(tmp_path / 'staging'))

    assert preview['success'] is False and 'already assigned' in preview['error']
    assert applied['success'] is False and 'already assigned' in applied['error']


def test_a_different_release_by_the_same_artist_is_allowed(monkeypatch, db):
    """Only the SAME release is refused — moving between two of an artist's
    albums is a legitimate reassign."""
    db._raw.execute("CREATE TABLE albums (id TEXT PRIMARY KEY, spotify_album_id TEXT)")
    db._raw.execute("INSERT INTO albums VALUES ('AL', 'AL9')")
    db._raw.execute("INSERT INTO tracks VALUES (1, 'AL', 'One', 1, '/m/1.flac')")
    db._raw.commit()
    _use(monkeypatch, _Client(tracks=[{'id': 'T1', 'name': 'One', 'track_number': 1}]))

    assert svc.preview_reassign(db, 'spotify', 'AL', 'DIFFERENT')['success'] is True


def test_an_album_with_no_stored_source_id_is_not_blocked(monkeypatch, db):
    """A locally-imported album has no spotify id. It must still be reassignable."""
    db._raw.execute("CREATE TABLE albums (id TEXT PRIMARY KEY, spotify_album_id TEXT)")
    db._raw.execute("INSERT INTO albums VALUES ('AL', NULL)")
    db._raw.execute("INSERT INTO tracks VALUES (1, 'AL', 'One', 1, '/m/1.flac')")
    db._raw.commit()
    _use(monkeypatch, _Client(tracks=[{'id': 'T1', 'name': 'One', 'track_number': 1}]))

    assert svc.preview_reassign(db, 'spotify', 'AL', 'AL9')['success'] is True


# ── the transaction boundary ─────────────────────────────────────────────────

def _album_ready(db, tmp_path, count=1):
    library = tmp_path / 'library'
    library.mkdir(exist_ok=True)
    for n in range(1, count + 1):
        (library / f'{n}.flac').write_bytes(b'audio')
        db._raw.execute(
            f"INSERT INTO tracks VALUES ({n}, 'AL', 'T{n}', {n}, '{library}/{n}.flac')")
    db._raw.execute("""CREATE TABLE IF NOT EXISTS rematch_hints (
        id INTEGER PRIMARY KEY, staged_path TEXT, content_hash TEXT, source TEXT,
        isrc TEXT, track_id TEXT, album_id TEXT, artist_id TEXT, track_title TEXT,
        album_name TEXT, artist_name TEXT, album_type TEXT, track_number INTEGER,
        disc_number INTEGER, replace_track_id INTEGER, exempt_dedup INTEGER,
        status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        consumed_at TIMESTAMP)""")
    db._raw.commit()
    return library


def test_a_failed_commit_takes_the_staged_copies_back_out(monkeypatch, db, tmp_path):
    """The files are on disk before the transaction lands. Left there,
    auto-import picks each one up as a new file and duplicates the album."""
    _album_ready(db, tmp_path, count=2)
    _use(monkeypatch, _Client(tracks=[{'id': 'T1', 'name': 'T1', 'track_number': 1},
                                      {'id': 'T2', 'name': 'T2', 'track_number': 2}]))
    staging = tmp_path / 'staging'

    real_conn = db._get_connection()

    class _CommitFails:
        def __getattr__(self, name):
            return getattr(real_conn, name)

        def commit(self):
            raise RuntimeError('disk full')

        def close(self):
            pass

    monkeypatch.setattr(db, '_get_connection', lambda: _CommitFails())

    result = svc.apply_reassign(
        db, source='spotify', local_album_id='AL', album_id='AL9', album_name='X',
        artist_id='AR9', artist_name='Y', album_type='album', staging_dir=str(staging))

    assert result['success'] is False
    leftovers = list(staging.glob('*')) if staging.exists() else []
    assert leftovers == [], f'orphaned staged copies left behind: {leftovers}'


def test_staging_nothing_is_not_reported_as_success(monkeypatch, db, tmp_path):
    """Every file failed. Saying "success" would tell the user their album
    moved when not one track did."""
    _album_ready(db, tmp_path, count=1)
    # Point the DB row at a file that does not exist.
    db._raw.execute("UPDATE tracks SET file_path = '/nowhere/gone.flac' WHERE id = 1")
    db._raw.commit()
    _use(monkeypatch, _Client(tracks=[{'id': 'T1', 'name': 'T1', 'track_number': 1}]))

    result = svc.apply_reassign(
        db, source='spotify', local_album_id='AL', album_id='AL9', album_name='X',
        artist_id='AR9', artist_name='Y', album_type='album',
        staging_dir=str(tmp_path / 'staging'))

    assert result['success'] is False
    assert len(result['failed']) == 1


# ── payload shapes ───────────────────────────────────────────────────────────

def test_the_items_key_is_read(monkeypatch):
    """The clients return a Spotify-compatible shape, and Spotify's
    album-tracks payload is {'items': [...]}. Discogs, iTunes, HydraBase and
    MusicBrainz all use it. Reading only 'tracks' made this return nothing for
    most sources, surfacing as "could not read the tracklist" not as a bug."""
    class _ItemsClient:
        def get_album_tracks(self, album_id):
            return {'items': [{'id': 'T1', 'name': 'One', 'track_number': 1}]}

    _use(monkeypatch, _ItemsClient())

    assert [t['id'] for t in svc.target_tracks('discogs', 'AL9')] == ['T1']


def test_the_tracks_key_still_works(monkeypatch):
    class _TracksClient:
        def get_album_tracks(self, album_id):
            return {'tracks': [{'id': 'T2', 'name': 'Two', 'track_number': 2}]}

    _use(monkeypatch, _TracksClient())

    assert [t['id'] for t in svc.target_tracks('tidal', 'AL9')] == ['T2']


def test_a_bare_list_payload_works(monkeypatch):
    class _ListClient:
        def get_album_tracks(self, album_id):
            return [{'id': 'T3', 'name': 'Three', 'track_number': 3}]

    _use(monkeypatch, _ListClient())

    assert [t['id'] for t in svc.target_tracks('x', 'AL9')] == ['T3']
