"""The AudioDB worker writes Library v2 (docs §32.3.1 stage 2).

AudioDB is the worker that exposed the hole in the handover model: it writes
``style``, ``mood``, ``label`` and ``banner_url`` — shared column names carrying no
service prefix, while AudioDB is in fact their only legacy writer. Prefix matching
could not see them, so the mirror would have kept pushing a stale legacy value over
each fresh native one. See ``test_mirror_handover_backfill``.

Two behaviours here are worth pinning beyond the mechanical port. AudioDB retries
``error`` as well as ``not_found`` (issue #553 marks transient outages rather than
leaving the row NULL, and without the retry those rows stay errored forever). And
the artist-id correction from a more specific album/track match is guarded by a
name check, so a collaboration or compilation cannot stamp the wrong AudioDB id
onto our artist.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.library2.provider_attempts import (
    attempt_state, ensure_provider_attempt_schema,
)
from core.library2.schema import ensure_library_v2_schema

from .conftest import own_every_track


class _Db:
    def __init__(self, path):
        self.path = path

    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


@pytest.fixture
def worker(tmp_path):
    path = str(tmp_path / "lib2.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    own_every_track(conn)
    ensure_provider_attempt_schema(conn.cursor())
    artist = conn.execute(
        "INSERT INTO lib2_artists(name, sort_name) VALUES('Massive Attack',"
        "'Massive Attack')").lastrowid
    album = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title,album_type) "
        "VALUES(?,'Mezzanine','album')", (artist,)).lastrowid
    conn.execute("INSERT INTO lib2_tracks(album_id,title) VALUES(?,'Angel')", (album,))
    conn.commit()
    conn.close()

    from core.audiodb_worker import AudioDBWorker

    instance = AudioDBWorker.__new__(AudioDBWorker)
    instance.db = _Db(path)
    instance.retry_days = 30
    instance.name_similarity_threshold = 0.80
    instance.stats = {'matched': 0, 'not_found': 0, 'pending': 0, 'errors': 0}
    instance.client = None
    return instance


def _row(worker, table, entity_id=1):
    conn = worker.db._get_connection()
    try:
        return conn.execute(
            f"SELECT * FROM {table} WHERE id=?", (entity_id,)).fetchone()
    finally:
        conn.close()


class TestTheQueue:
    def test_all_three_entity_types_are_offered_in_order(self, worker):
        assert worker._get_next_item()['type'] == 'artist'
        worker._mark_status('artist', 1, 'matched')
        assert worker._get_next_item()['type'] == 'album'
        worker._mark_status('album', 1, 'matched')
        assert worker._get_next_item()['type'] == 'track'
        worker._mark_status('track', 1, 'matched')
        assert worker._get_next_item() is None

    def test_an_album_carries_its_artists_audiodb_id(self, worker):
        """_verify_artist_id compares the result against the parent artist's
        stored id, so the item has to bring it along."""
        worker._update_artist(1, {'idArtist': '111'})
        worker._mark_status('artist', 1, 'matched')

        item = worker._get_next_item()

        assert item['type'] == 'album'
        assert item['artist'] == 'Massive Attack'
        assert item['artist_audiodb_id'] == '111'

    def test_a_stale_error_comes_back(self, worker):
        """Issue #553: transient outages are marked rather than left NULL, and
        without this retry path those rows would stay errored forever."""
        for entity_type in ('album', 'track'):
            worker._mark_status(entity_type, 1, 'matched')
        worker._mark_status('artist', 1, 'error')
        conn = worker.db._get_connection()
        conn.execute("UPDATE lib2_provider_attempts "
                     "SET last_attempted_at=datetime('now','-90 days')")
        conn.commit()
        conn.close()

        assert worker._get_next_item()['type'] == 'artist'

    def test_pending_counts_all_three(self, worker):
        assert worker._count_pending_items() == 3
        assert set(worker._get_progress_breakdown()) == {
            'artists', 'albums', 'tracks'}


class TestWritingTheArtist:
    def test_the_shared_columns_are_written_outright(self, worker):
        """style/mood/label/banner_url are AudioDB's own fields even though their
        names do not say so — it is their only writer, so a fresh fetch replaces
        what is there rather than only filling a gap."""
        conn = worker.db._get_connection()
        conn.execute("UPDATE lib2_artists SET style='old', mood='old' WHERE id=1")
        conn.commit()
        conn.close()

        worker._update_artist(1, {
            'idArtist': '111', 'strStyle': 'Trip Hop', 'strMood': 'Brooding',
            'strLabel': 'Virgin', 'strArtistBanner': 'http://banner',
        })

        row = _row(worker, 'lib2_artists')
        assert row['style'] == 'Trip Hop'
        assert row['mood'] == 'Brooding'
        assert row['label'] == 'Virgin'
        assert row['banner_url'] == 'http://banner'
        assert json.loads(row['external_ids'])['audiodb'] == '111'

    def test_the_image_and_genres_are_backfilled_only(self, worker):
        conn = worker.db._get_connection()
        conn.execute("UPDATE lib2_artists SET image_url='http://chosen', "
                     "genres='[\"Electronic\"]' WHERE id=1")
        conn.commit()
        conn.close()

        worker._update_artist(1, {
            'idArtist': '111', 'strArtistThumb': 'http://audiodb',
            'strGenre': 'Rock',
        })

        row = _row(worker, 'lib2_artists')
        assert row['image_url'] == 'http://chosen'
        assert json.loads(row['genres']) == ['Electronic']

    def test_an_empty_image_and_genres_are_filled(self, worker):
        worker._update_artist(1, {
            'idArtist': '111', 'strArtistThumb': 'http://audiodb',
            'strGenre': 'Trip Hop',
        })

        row = _row(worker, 'lib2_artists')
        assert row['image_url'] == 'http://audiodb'
        assert json.loads(row['genres']) == ['Trip Hop']

    def test_the_attempt_is_recorded(self, worker):
        worker._update_artist(1, {'idArtist': '111'})

        conn = worker.db._get_connection()
        try:
            state = attempt_state(conn, entity_type='artist', entity_id=1)
        finally:
            conn.close()
        assert state['audiodb']['status'] == 'matched'


class TestWritingAlbumsAndTracks:
    def test_the_album_gets_its_id_and_shared_columns(self, worker):
        worker._update_album(1, {
            'idAlbum': '222', 'strStyle': 'Trip Hop', 'strMood': 'Brooding',
            'strAlbumThumb': 'http://cover', 'strGenre': 'Electronic',
        })

        row = _row(worker, 'lib2_albums')
        assert json.loads(row['external_ids'])['audiodb'] == '222'
        assert row['style'] == 'Trip Hop'
        assert row['image_url'] == 'http://cover'
        assert json.loads(row['genres']) == ['Electronic']

    def test_the_track_gets_its_id_and_shared_columns(self, worker):
        worker._update_track(1, {
            'idTrack': '333', 'strStyle': 'Trip Hop', 'strMood': 'Brooding'})

        row = _row(worker, 'lib2_tracks')
        assert json.loads(row['external_ids'])['audiodb'] == '333'
        assert row['style'] == 'Trip Hop'
        assert row['mood'] == 'Brooding'


class TestTheArtistIdCorrection:
    def test_a_more_specific_match_corrects_the_parent(self, worker):
        worker._update_artist(1, {'idArtist': '111'})

        assert worker._verify_artist_id(
            {'type': 'album', 'id': 1, 'name': 'Mezzanine',
             'artist': 'Massive Attack', 'artist_audiodb_id': '111'},
            {'idArtist': '999', 'strArtist': 'Massive Attack'}) is True

        assert json.loads(
            _row(worker, 'lib2_artists')['external_ids'])['audiodb'] == '999'

    def test_a_collaboration_does_not_stamp_the_wrong_id(self, worker):
        """A track our library credits to one artist but which lives on another
        artist's album would otherwise overwrite our artist's id."""
        worker._update_artist(1, {'idArtist': '111'})

        worker._verify_artist_id(
            {'type': 'track', 'id': 1, 'name': 'Angel',
             'artist': 'Massive Attack', 'artist_audiodb_id': '111'},
            {'idArtist': '999', 'strArtist': 'Some Other Band'})

        assert json.loads(
            _row(worker, 'lib2_artists')['external_ids'])['audiodb'] == '111'

    def test_the_correction_finds_the_parent_through_the_album(self, worker):
        """A track's parent artist is two joins away in lib2 — track → album →
        primary artist — where legacy carried tracks.artist_id directly."""
        worker._update_artist(1, {'idArtist': '111'})

        worker._verify_artist_id(
            {'type': 'track', 'id': 1, 'name': 'Angel',
             'artist': 'Massive Attack', 'artist_audiodb_id': '111'},
            {'idArtist': '999', 'strArtist': 'Massive Attack'})

        assert json.loads(
            _row(worker, 'lib2_artists')['external_ids'])['audiodb'] == '999'


def test_a_stored_id_is_reused_instead_of_searching(worker):
    worker._update_artist(1, {'idArtist': '111'})

    assert worker._get_existing_id('artist', 1) == '111'


def test_the_worker_holds_no_legacy_sql_at_all():
    import pathlib

    from tests.library2.legacy_usage import count_legacy_usage

    usage = count_legacy_usage(pathlib.Path("core/audiodb_worker.py").read_text())

    assert (usage.reads, usage.writes) == (0, 0)


def test_the_duplicate_id_gate_reads_lib2_not_the_legacy_twin(worker):
    """The 'one id smeared across many artists' guard has to look where the
    artists are.

    Every other migrated worker calls ``worker_support.accept_artist_match``,
    which asks ``lib2_artists``. AudioDB kept the legacy helper
    (``worker_utils.accept_artist_match`` -> ``SELECT name FROM artists``), and a
    V2-native artist has no legacy twin — so the gate saw an empty table and
    waved through exactly the collision it exists to stop.
    """
    conn = worker.db._get_connection()
    try:
        conn.execute(
            "INSERT INTO lib2_artists(id, name, sort_name, external_ids) "
            "VALUES(2, 'Portishead', 'Portishead', ?)",
            (json.dumps({'audiodb': '111'}),))
        conn.commit()
    finally:
        conn.close()

    class _Client:
        def search_artist(self, name):
            return {'strArtist': 'Massive Attack', 'idArtist': '111'}

    worker.client = _Client()
    worker._process_item({'type': 'artist', 'id': 1, 'name': 'Massive Attack'})

    assert _row(worker, 'lib2_artists', 1)['external_ids'] in (None, '', '{}')
    assert worker.stats['not_found'] == 1
