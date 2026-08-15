"""Pin AudioDB worker doesn't infinite-loop on direct-ID-lookup failures.

Issue #553: when an entity already had a stored AudioDB id (from a manual match or
an earlier scan) but no recorded attempt, the worker tried a direct id lookup. If
that lookup failed (returns None on timeout — AudioDB's `track.php` endpoint is
slow and 10s timeouts are common), the prior code returned WITHOUT recording an
outcome. The row stayed in its never-attempted state, the queue picked it up next
tick, retried, timed out, returned again — an infinite loop. The user saw constant
requests with no progress.

The fix:
  - record status='error' so the queue's never-attempted filter stops picking the
    row on every tick;
  - retry 'error' after the retry window as well as 'not_found', so transient
    AudioDB outages still recover automatically;
  - preserve the stored id (don't overwrite it via the name-search fallback —
    the original "preserve manual match" intent).

Restated against Library v2 (docs §32.3.1 stage 2): the outcome lives in
`lib2_provider_attempts` where it used to live in `audiodb_match_status` /
`audiodb_last_attempted`, and the stored id lives in `external_ids` rather than an
`audiodb_id` column. The loop-prevention itself is unchanged, and it is the reason
this file exists.
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock

from core.audiodb_worker import AudioDBWorker
from core.library2.provider_attempts import (
    attempt_state, ensure_provider_attempt_schema, record_attempt,
)
from core.library2.schema import ensure_library_v2_schema

from lib2_ownership import own_every_track


def _make_lib2_db(tmp_path):
    db_path = tmp_path / "audiodb_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    own_every_track(conn)
    ensure_provider_attempt_schema(conn.cursor())
    conn.commit()
    conn.close()

    class _RealDB:
        def _get_connection(self):
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return c

    return _RealDB(), db_path


def _seed_track(db, *, title="Sweet Talk", stored_id=None):
    """One artist → album → track, optionally carrying a stored AudioDB id."""
    conn = db._get_connection()
    try:
        artist = conn.execute(
            "INSERT INTO lib2_artists(name, sort_name) VALUES('Test Artist',"
            "'Test Artist')").lastrowid
        album = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id,title,album_type) "
            "VALUES(?,'An Album','album')", (artist,)).lastrowid
        track = conn.execute(
            "INSERT INTO lib2_tracks(album_id,title,external_ids) VALUES(?,?,?)",
            (album, title,
             json.dumps({"audiodb": stored_id} if stored_id else {}))).lastrowid
        conn.commit()
        return artist, track
    finally:
        conn.close()


def _make_worker(db, fake_client):
    """A worker with a real DB + mocked AudioDB client. Skips __init__ side
    effects (config load, thread start)."""
    worker = AudioDBWorker.__new__(AudioDBWorker)
    worker.db = db
    worker.client = fake_client
    worker.retry_days = 30
    worker.name_similarity_threshold = 0.80
    worker.stats = {'matched': 0, 'not_found': 0, 'errors': 0, 'pending': 0}
    worker.current_item = None
    worker.running = False
    worker.paused = False
    worker.thread = None
    return worker


def _track_state(db, track_id):
    conn = db._get_connection()
    try:
        return (
            attempt_state(conn, entity_type='track', entity_id=track_id
                          ).get('audiodb', {}),
            json.loads(conn.execute(
                "SELECT external_ids FROM lib2_tracks WHERE id=?",
                (track_id,)).fetchone()[0]),
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Issue #553 — direct-ID lookup failure no longer infinite-loops
# ---------------------------------------------------------------------------


class TestDirectLookupFailureMarksError:
    def test_lookup_returns_none_marks_status_error(self, tmp_path):
        """The reporter's exact scenario: the track has a stored AudioDB id and no
        recorded attempt. AudioDB times out → lookup returns None. Pre-fix: return
        without recording → infinite loop next tick. Post-fix: record 'error' →
        the queue stops re-picking it."""
        db, _path = _make_lib2_db(tmp_path)
        _artist, track = _seed_track(db, stored_id='12345')

        # The client returns None on timeout, matching lookup_track_by_id.
        fake_client = SimpleNamespace(
            lookup_artist_by_id=MagicMock(return_value=None),
            lookup_album_by_id=MagicMock(return_value=None),
            lookup_track_by_id=MagicMock(return_value=None),
        )
        worker = _make_worker(db, fake_client)

        worker._process_item({
            'type': 'track', 'id': track, 'name': 'Sweet Talk',
            'artist': 'Test Artist', 'artist_audiodb_id': None,
        })

        state, ids = _track_state(db, track)
        assert state.get('status') == 'error', (
            f"expected an 'error' attempt to break the loop; got {state!r}")
        assert state.get('last_attempted_at'), (
            "the attempt timestamp is what the retry window measures from")
        assert ids.get('audiodb') == '12345', "the stored id must NOT be cleared"
        assert worker.stats['errors'] == 1

    def test_lookup_raises_exception_marks_status_error(self, tmp_path):
        """Defensive: if the client itself raises rather than returning None, the
        same loop protection has to apply. Some client paths re-raise."""
        db, _path = _make_lib2_db(tmp_path)
        _artist, track = _seed_track(db, title='Y', stored_id='67890')

        fake_client = SimpleNamespace(
            lookup_artist_by_id=MagicMock(side_effect=RuntimeError("boom")),
            lookup_album_by_id=MagicMock(side_effect=RuntimeError("boom")),
            lookup_track_by_id=MagicMock(side_effect=RuntimeError("read timeout")),
        )
        worker = _make_worker(db, fake_client)

        worker._process_item({
            'type': 'track', 'id': track, 'name': 'Y', 'artist': 'Test Artist',
            'artist_audiodb_id': None,
        })

        state, _ids = _track_state(db, track)
        assert state.get('status') == 'error'

    def test_lookup_success_preserves_existing_path(self, tmp_path):
        """Sanity: when the direct lookup SUCCEEDS the existing match path still
        runs. Don't regress the happy path."""
        db, _path = _make_lib2_db(tmp_path)
        _artist, track = _seed_track(db, title='T', stored_id='111')

        fake_client = SimpleNamespace(
            lookup_artist_by_id=MagicMock(),
            lookup_album_by_id=MagicMock(),
            lookup_track_by_id=MagicMock(return_value={
                'idTrack': '111', 'strTrack': 'T', 'idArtist': '999',
            }),
        )
        worker = _make_worker(db, fake_client)
        worker._update_track = MagicMock()
        worker._verify_artist_id = MagicMock(return_value=True)

        worker._process_item({
            'type': 'track', 'id': track, 'name': 'T', 'artist': 'Test Artist',
            'artist_audiodb_id': None,
        })

        worker._update_track.assert_called_once()
        assert worker.stats['matched'] == 1
        assert worker.stats['errors'] == 0


# ---------------------------------------------------------------------------
# The retry window covers 'error' too — transient outages eventually recover
# ---------------------------------------------------------------------------


class TestErrorRetryAfterCutoff:
    def _seed_errored_track(self, db, *, days_ago):
        """An errored track whose artist and album are already settled, so the
        artist→album→track priority chain does not intercept the check."""
        artist, track = _seed_track(db, title='Errored')
        conn = db._get_connection()
        try:
            album = conn.execute(
                "SELECT id FROM lib2_albums WHERE primary_artist_id=?",
                (artist,)).fetchone()[0]
            for entity_type, entity_id in (('artist', artist), ('album', album)):
                record_attempt(conn, entity_type=entity_type, entity_id=entity_id,
                               service='audiodb', status='matched')
            record_attempt(conn, entity_type='track', entity_id=track,
                           service='audiodb', status='error')
            conn.execute(
                "UPDATE lib2_provider_attempts "
                "SET last_attempted_at=datetime('now', ?) "
                "WHERE entity_type='track' AND entity_id=?",
                (f'-{days_ago} days', track))
            conn.commit()
        finally:
            conn.close()
        return track

    def test_error_track_picked_up_after_cutoff(self, tmp_path):
        """'error' gets the same retry window as 'not_found'. Without it a
        transient AudioDB outage leaves the row errored forever."""
        db, _path = _make_lib2_db(tmp_path)
        track = self._seed_errored_track(db, days_ago=31)
        worker = _make_worker(db, SimpleNamespace())

        item = worker._get_next_item()

        assert item is not None, (
            "an error-status track past the retry cutoff must be picked up")
        assert item['type'] == 'track'
        assert item['id'] == track

    def test_error_track_NOT_picked_within_cutoff(self, tmp_path):
        """Sanity: a recently-attempted error row must NOT be picked, or the
        window does not actually rate-limit retries and we are back in the loop."""
        db, _path = _make_lib2_db(tmp_path)
        self._seed_errored_track(db, days_ago=1)
        worker = _make_worker(db, SimpleNamespace())

        assert worker._get_next_item() is None, (
            "recently-attempted error rows must NOT be picked up — that is the "
            "loop-prevention mechanism")
